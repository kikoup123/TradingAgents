// Londres Phase 25 — NinjaTrader 8 native read-only bridge producer.
//
// SAFETY BOUNDARY
// - This AddOn reads account values, instrument metadata, rollover metadata and L1 quotes.
// - It contains no order creation, submission, change, cancel, flatten, ATM or position-close API.
// - It writes a local JSON snapshot consumed by tradingagents/brokers/ninjatrader.py.
// - Provider PERSONAL/PROP_FIRM classification is never inferred here.
//
// Install by copying this file into a NinjaTrader 8 AddOn source location and compiling it
// in the NinjaScript Editor. See docs/LONDRES_PHASE25_NINJATRADER_NATIVE_BRIDGE.md.

#region Using declarations
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Windows;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.Gui;
using NinjaTrader.Gui.Tools;
#endregion

namespace NinjaTrader.NinjaScript.AddOns
{
    public class LondresReadOnlyBridge : AddOnBase
    {
        private const int DefaultPublishIntervalMs = 500;
        private const int MinimumPublishIntervalMs = 100;
        private const int MaximumPublishIntervalMs = 10000;

        private static readonly string[] SupportedRoots =
        {
            "NQ", "MNQ", "ES", "MES", "YM", "MYM"
        };

        private static readonly Dictionary<string, ExpectedFuturesSpec> ExpectedSpecs =
            new Dictionary<string, ExpectedFuturesSpec>(StringComparer.OrdinalIgnoreCase)
            {
                { "NQ",  new ExpectedFuturesSpec("NASDAQ", 0.25, 20.0, 5.0) },
                { "MNQ", new ExpectedFuturesSpec("NASDAQ", 0.25, 2.0, 0.5) },
                { "ES",  new ExpectedFuturesSpec("SP500", 0.25, 50.0, 12.5) },
                { "MES", new ExpectedFuturesSpec("SP500", 0.25, 5.0, 1.25) },
                { "YM",  new ExpectedFuturesSpec("DOW", 1.0, 5.0, 5.0) },
                { "MYM", new ExpectedFuturesSpec("DOW", 1.0, 0.5, 0.5) }
            };

        private readonly object lifecycleLock = new object();
        private readonly object subscriptionLock = new object();
        private readonly Dictionary<string, MarketData> marketDataByContract =
            new Dictionary<string, MarketData>(StringComparer.OrdinalIgnoreCase);

        private Timer publishTimer;
        private bool started;
        private bool publishing;
        private string outputPath;
        private string configPath;
        private BridgeConfig config;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name = "Londres Read-Only Bridge";
                Description = "Publishes sanitized NinjaTrader account/market metadata for Londres. No order APIs.";
            }
            else if (State == State.Terminated)
            {
                StopBridge();
            }
        }

        protected override void OnWindowCreated(Window window)
        {
            if (!(window is ControlCenter))
                return;

            StartBridge();
        }

        protected override void OnWindowDestroyed(Window window)
        {
            if (window is ControlCenter)
                StopBridge();
        }

        private void StartBridge()
        {
            lock (lifecycleLock)
            {
                if (started)
                    return;

                string rootDirectory = Path.Combine(NinjaTrader.Core.Globals.UserDataDir, "Londres");
                Directory.CreateDirectory(rootDirectory);

                configPath = Path.Combine(rootDirectory, "londres_bridge_config.json");
                outputPath = Path.Combine(rootDirectory, "ninjatrader_snapshot.json");
                config = LoadConfig(configPath);

                int intervalMs = config.PublishIntervalMs <= 0
                    ? DefaultPublishIntervalMs
                    : config.PublishIntervalMs;
                intervalMs = Math.Max(MinimumPublishIntervalMs, Math.Min(MaximumPublishIntervalMs, intervalMs));

                started = true;
                publishTimer = new Timer(_ => SafePublish(), null, 0, intervalMs);
            }
        }

        private void StopBridge()
        {
            lock (lifecycleLock)
            {
                if (!started)
                    return;

                started = false;
                if (publishTimer != null)
                {
                    publishTimer.Dispose();
                    publishTimer = null;
                }
            }

            lock (subscriptionLock)
            {
                foreach (MarketData marketData in marketDataByContract.Values.ToList())
                {
                    try
                    {
                        marketData.Update -= OnMarketData;
                    }
                    catch
                    {
                        // Read-only shutdown must never escalate into an execution path.
                    }
                }
                marketDataByContract.Clear();
            }
        }

        private void SafePublish()
        {
            lock (lifecycleLock)
            {
                if (!started || publishing)
                    return;
                publishing = true;
            }

            try
            {
                BridgeSnapshot snapshot = BuildSnapshot();
                WriteSnapshotAtomically(snapshot);
            }
            catch (Exception exception)
            {
                NinjaTrader.Code.Output.Process(
                    "Londres read-only bridge publish failed: " + exception.Message,
                    PrintTo.OutputTab1);
            }
            finally
            {
                lock (lifecycleLock)
                    publishing = false;
            }
        }

        private BridgeSnapshot BuildSnapshot()
        {
            long nowMs = ToUnixMilliseconds(DateTime.UtcNow);
            List<AccountSnapshot> accounts = BuildAccountSnapshots();
            Dictionary<string, InstrumentSnapshot> instruments =
                new Dictionary<string, InstrumentSnapshot>(StringComparer.OrdinalIgnoreCase);
            Dictionary<string, QuoteSnapshot> quotes =
                new Dictionary<string, QuoteSnapshot>(StringComparer.OrdinalIgnoreCase);
            Dictionary<string, RolloverSnapshot> rollovers =
                new Dictionary<string, RolloverSnapshot>(StringComparer.OrdinalIgnoreCase);

            foreach (string root in SupportedRoots)
            {
                RolloverResolution resolution = ResolveRollover(root, nowMs);
                rollovers[root] = resolution.Rollover;

                if (!resolution.Ready || resolution.Instrument == null)
                    continue;

                InstrumentSnapshot instrumentSnapshot = BuildInstrumentSnapshot(root, resolution.Instrument);
                instruments[instrumentSnapshot.Symbol] = instrumentSnapshot;

                EnsureMarketDataSubscription(resolution.Instrument);
                QuoteSnapshot quote = ReadQuoteSnapshot(resolution.Instrument.FullName);
                if (quote != null)
                    quotes[resolution.Instrument.FullName] = quote;
            }

            return new BridgeSnapshot
            {
                SchemaVersion = 1,
                BridgeStatus = "CONNECTED",
                GeneratedAtMs = nowMs,
                Accounts = accounts,
                Instruments = instruments,
                Quotes = quotes,
                Rollovers = rollovers
            };
        }

        private List<AccountSnapshot> BuildAccountSnapshots()
        {
            List<Account> accounts;
            lock (Account.All)
                accounts = Account.All.ToList();

            List<AccountSnapshot> result = new List<AccountSnapshot>();
            foreach (Account account in accounts)
            {
                if (account == null)
                    continue;

                Currency denomination = account.Denomination;
                string currency = CurrencyToIso(denomination);
                Connection connection = account.Connection;
                string connectionName = connection != null && connection.Options != null
                    ? connection.Options.Name
                    : "UNKNOWN";
                string provider = connection != null && connection.Options != null
                    ? connection.Options.Provider.ToString()
                    : "UNKNOWN";
                bool connected = connection != null &&
                    (connection.Status == ConnectionStatus.Connected ||
                     connection.PriceStatus == ConnectionStatus.Connected);

                double balance = ReadAccountValue(account, AccountItem.CashValue, denomination);
                double equity = ReadAccountValue(account, AccountItem.NetLiquidation, denomination);
                double usedMargin = ReadAccountValue(account, AccountItem.InitialMargin, denomination, 0.0);
                double freeMargin = ReadAccountValue(account, AccountItem.BuyingPower, denomination, 0.0);

                result.Add(new AccountSnapshot
                {
                    AccountKey = StableAccountKey(connectionName, account.Name),
                    MaskedAccount = MaskAccount(account.Name),
                    Provider = connectionName + " / " + provider,
                    ProviderClassification = null,
                    ProviderClassificationVerified = false,
                    Connected = connected,
                    Currency = currency,
                    Balance = balance,
                    Equity = equity,
                    UsedMargin = usedMargin,
                    FreeMargin = freeMargin
                });
            }

            return result;
        }

        private RolloverResolution ResolveRollover(string root, long nowMs)
        {
            Instrument masterInstrument = Instrument.GetInstrument(root);
            if (masterInstrument == null || masterInstrument.MasterInstrument == null)
                return RolloverResolution.Blocked(root, nowMs, "MASTER_INSTRUMENT_UNAVAILABLE");

            var rolloverCollection = masterInstrument.MasterInstrument.RolloverCollection;
            if (rolloverCollection == null || rolloverCollection.Count == 0)
                return RolloverResolution.Blocked(root, nowMs, "ROLLOVER_COLLECTION_UNAVAILABLE");

            DateTime localNow = DateTime.Now;
            var eligible = rolloverCollection
                .Where(item => item != null && item.Date <= localNow)
                .OrderBy(item => item.Date)
                .ToList();
            if (eligible.Count == 0)
                return RolloverResolution.Blocked(root, nowMs, "NO_EFFECTIVE_ROLLOVER_FOUND");

            var active = eligible[eligible.Count - 1];
            string contractSymbol = string.Format(
                System.Globalization.CultureInfo.InvariantCulture,
                "{0} {1:MM-yy}",
                root,
                active.ContractMonth);
            Instrument exactInstrument = Instrument.GetInstrument(contractSymbol);
            if (exactInstrument == null || exactInstrument.MasterInstrument == null)
                return RolloverResolution.Blocked(root, nowMs, "ACTIVE_CONTRACT_INSTRUMENT_UNAVAILABLE");

            if (!string.Equals(
                    exactInstrument.MasterInstrument.Name,
                    root,
                    StringComparison.OrdinalIgnoreCase))
                return RolloverResolution.Blocked(root, nowMs, "ACTIVE_CONTRACT_ROOT_MISMATCH");

            return new RolloverResolution
            {
                Ready = true,
                Instrument = exactInstrument,
                Rollover = new RolloverSnapshot
                {
                    ActiveContract = exactInstrument.FullName,
                    Verified = true,
                    Source = "NINJATRADER_MASTER_INSTRUMENT_ROLLOVER_COLLECTION",
                    AsOfMs = nowMs
                }
            };
        }

        private InstrumentSnapshot BuildInstrumentSnapshot(string root, Instrument instrument)
        {
            ExpectedFuturesSpec expected;
            if (!ExpectedSpecs.TryGetValue(root, out expected))
                throw new InvalidOperationException("Unsupported futures root: " + root);

            double tickSize = instrument.MasterInstrument.TickSize;
            double pointValue = instrument.MasterInstrument.PointValue;
            double tickValue = tickSize * pointValue;
            string currency = CurrencyToIso(instrument.MasterInstrument.Currency);

            int maxQuantity;
            bool maxQuantityConfigured = config.MaxQuantityByRoot != null &&
                config.MaxQuantityByRoot.TryGetValue(root, out maxQuantity) &&
                maxQuantity > 0;

            bool metadataVerified =
                maxQuantityConfigured &&
                string.Equals(instrument.MasterInstrument.Name, root, StringComparison.OrdinalIgnoreCase) &&
                string.Equals(currency, "USD", StringComparison.OrdinalIgnoreCase) &&
                NearlyEqual(tickSize, expected.TickSize) &&
                NearlyEqual(pointValue, expected.PointValue) &&
                NearlyEqual(tickValue, expected.TickValue);

            return new InstrumentSnapshot
            {
                Symbol = instrument.FullName,
                Root = root,
                CanonicalSymbol = expected.CanonicalSymbol,
                TickSize = tickSize,
                PointValue = pointValue,
                TickValue = tickValue,
                Currency = currency,
                MaxQuantity = maxQuantityConfigured ? maxQuantity : 0,
                MetadataVerified = metadataVerified,
                MetadataSource = metadataVerified
                    ? "NINJATRADER_MASTER_INSTRUMENT_PLUS_EXPLICIT_LOCAL_MAX_QUANTITY"
                    : "UNVERIFIED_MISSING_OR_CONFLICTING_METADATA"
            };
        }

        private void EnsureMarketDataSubscription(Instrument instrument)
        {
            if (instrument == null || string.IsNullOrWhiteSpace(instrument.FullName))
                return;

            lock (subscriptionLock)
            {
                if (marketDataByContract.ContainsKey(instrument.FullName))
                    return;
            }

            Action subscribe = () =>
            {
                lock (subscriptionLock)
                {
                    if (marketDataByContract.ContainsKey(instrument.FullName))
                        return;

                    MarketData marketData = new MarketData(instrument);
                    marketData.Update += OnMarketData;
                    marketDataByContract[instrument.FullName] = marketData;
                }
            };

            if (instrument.Dispatcher != null && !instrument.Dispatcher.HasShutdownStarted)
                instrument.Dispatcher.InvokeAsync(subscribe);
        }

        private void OnMarketData(object sender, MarketDataEventArgs e)
        {
            // The subscription intentionally has no trading side effect. MarketData keeps
            // its Bid/Ask snapshot updated; publishing reads those values on the next cycle.
        }

        private QuoteSnapshot ReadQuoteSnapshot(string contractSymbol)
        {
            MarketData marketData;
            lock (subscriptionLock)
            {
                if (!marketDataByContract.TryGetValue(contractSymbol, out marketData))
                    return null;
            }

            MarketDataEventArgs bid = marketData.Bid;
            MarketDataEventArgs ask = marketData.Ask;
            if (bid == null || ask == null)
                return null;
            if (!IsFinitePositive(bid.Price) || !IsFinitePositive(ask.Price) || ask.Price < bid.Price)
                return null;

            // Use the older side timestamp so Phase 22 sees the true freshness of the complete BBO.
            long bidMs = ToUnixMilliseconds(bid.Time.ToUniversalTime());
            long askMs = ToUnixMilliseconds(ask.Time.ToUniversalTime());
            return new QuoteSnapshot
            {
                Bid = bid.Price,
                Ask = ask.Price,
                TimestampMs = Math.Min(bidMs, askMs)
            };
        }

        private void WriteSnapshotAtomically(BridgeSnapshot snapshot)
        {
            string directory = Path.GetDirectoryName(outputPath);
            if (string.IsNullOrWhiteSpace(directory))
                throw new InvalidOperationException("Bridge output directory is unavailable");
            Directory.CreateDirectory(directory);

            string temporaryPath = outputPath + ".tmp";
            DataContractJsonSerializer serializer = new DataContractJsonSerializer(
                typeof(BridgeSnapshot),
                new DataContractJsonSerializerSettings { UseSimpleDictionaryFormat = true });

            using (FileStream stream = new FileStream(
                temporaryPath,
                FileMode.Create,
                FileAccess.Write,
                FileShare.None))
            {
                serializer.WriteObject(stream, snapshot);
                stream.Flush(true);
            }

            if (File.Exists(outputPath))
            {
                string backupPath = outputPath + ".bak";
                try
                {
                    File.Replace(temporaryPath, outputPath, backupPath, true);
                    if (File.Exists(backupPath))
                        File.Delete(backupPath);
                }
                catch (PlatformNotSupportedException)
                {
                    File.Delete(outputPath);
                    File.Move(temporaryPath, outputPath);
                }
            }
            else
            {
                File.Move(temporaryPath, outputPath);
            }
        }

        private static BridgeConfig LoadConfig(string path)
        {
            if (!File.Exists(path))
                return new BridgeConfig
                {
                    PublishIntervalMs = DefaultPublishIntervalMs,
                    MaxQuantityByRoot = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase)
                };

            try
            {
                DataContractJsonSerializer serializer = new DataContractJsonSerializer(
                    typeof(BridgeConfig),
                    new DataContractJsonSerializerSettings { UseSimpleDictionaryFormat = true });
                using (FileStream stream = File.OpenRead(path))
                {
                    BridgeConfig loaded = serializer.ReadObject(stream) as BridgeConfig;
                    if (loaded == null)
                        throw new InvalidDataException("Bridge config is empty");
                    if (loaded.MaxQuantityByRoot == null)
                        loaded.MaxQuantityByRoot = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
                    else
                        loaded.MaxQuantityByRoot = new Dictionary<string, int>(
                            loaded.MaxQuantityByRoot,
                            StringComparer.OrdinalIgnoreCase);
                    return loaded;
                }
            }
            catch (Exception exception)
            {
                throw new InvalidDataException(
                    "Londres bridge config cannot be trusted: " + exception.Message,
                    exception);
            }
        }

        private static double ReadAccountValue(
            Account account,
            AccountItem item,
            Currency currency,
            double? fallback = null)
        {
            try
            {
                double value = account.Get(item, currency);
                if (!double.IsNaN(value) && !double.IsInfinity(value))
                    return value;
            }
            catch
            {
                // Fall through to explicit fallback/fail-closed value.
            }

            if (fallback.HasValue)
                return fallback.Value;
            return 0.0;
        }

        private static string CurrencyToIso(Currency currency)
        {
            string name = currency.ToString();
            switch (name)
            {
                case "UsDollar":
                    return "USD";
                case "Euro":
                    return "EUR";
                case "BritishPound":
                    return "GBP";
                case "JapaneseYen":
                    return "JPY";
                case "CanadianDollar":
                    return "CAD";
                case "AustralianDollar":
                    return "AUD";
                case "SwissFranc":
                    return "CHF";
                default:
                    return name.ToUpperInvariant();
            }
        }

        private static string StableAccountKey(string connectionName, string accountName)
        {
            string material = (connectionName ?? "") + "|" + (accountName ?? "");
            using (SHA256 sha = SHA256.Create())
            {
                byte[] digest = sha.ComputeHash(Encoding.UTF8.GetBytes(material));
                StringBuilder builder = new StringBuilder(digest.Length * 2);
                foreach (byte value in digest)
                    builder.Append(value.ToString("x2", System.Globalization.CultureInfo.InvariantCulture));
                return builder.ToString();
            }
        }

        private static string MaskAccount(string accountName)
        {
            string value = accountName ?? string.Empty;
            string suffix = value.Length <= 4 ? value : value.Substring(value.Length - 4);
            return "••••" + suffix;
        }

        private static bool NearlyEqual(double left, double right)
        {
            return Math.Abs(left - right) <= 1e-9;
        }

        private static bool IsFinitePositive(double value)
        {
            return !double.IsNaN(value) && !double.IsInfinity(value) && value > 0;
        }

        private static long ToUnixMilliseconds(DateTime utc)
        {
            DateTime normalized = utc.Kind == DateTimeKind.Utc ? utc : utc.ToUniversalTime();
            return (long)(normalized - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalMilliseconds;
        }

        private sealed class ExpectedFuturesSpec
        {
            public ExpectedFuturesSpec(string canonicalSymbol, double tickSize, double pointValue, double tickValue)
            {
                CanonicalSymbol = canonicalSymbol;
                TickSize = tickSize;
                PointValue = pointValue;
                TickValue = tickValue;
            }

            public string CanonicalSymbol { get; private set; }
            public double TickSize { get; private set; }
            public double PointValue { get; private set; }
            public double TickValue { get; private set; }
        }

        private sealed class RolloverResolution
        {
            public bool Ready { get; set; }
            public Instrument Instrument { get; set; }
            public RolloverSnapshot Rollover { get; set; }

            public static RolloverResolution Blocked(string root, long nowMs, string reason)
            {
                return new RolloverResolution
                {
                    Ready = false,
                    Instrument = null,
                    Rollover = new RolloverSnapshot
                    {
                        ActiveContract = null,
                        Verified = false,
                        Source = "NINJATRADER_ROLLOVER_UNVERIFIED:" + reason,
                        AsOfMs = nowMs
                    }
                };
            }
        }

        [DataContract]
        private sealed class BridgeConfig
        {
            [DataMember(Name = "publish_interval_ms")]
            public int PublishIntervalMs { get; set; }

            [DataMember(Name = "max_quantity_by_root")]
            public Dictionary<string, int> MaxQuantityByRoot { get; set; }
        }

        [DataContract]
        private sealed class BridgeSnapshot
        {
            [DataMember(Name = "schema_version")]
            public int SchemaVersion { get; set; }

            [DataMember(Name = "bridge_status")]
            public string BridgeStatus { get; set; }

            [DataMember(Name = "generated_at_ms")]
            public long GeneratedAtMs { get; set; }

            [DataMember(Name = "accounts")]
            public List<AccountSnapshot> Accounts { get; set; }

            [DataMember(Name = "instruments")]
            public Dictionary<string, InstrumentSnapshot> Instruments { get; set; }

            [DataMember(Name = "quotes")]
            public Dictionary<string, QuoteSnapshot> Quotes { get; set; }

            [DataMember(Name = "rollovers")]
            public Dictionary<string, RolloverSnapshot> Rollovers { get; set; }
        }

        [DataContract]
        private sealed class AccountSnapshot
        {
            [DataMember(Name = "account_key")]
            public string AccountKey { get; set; }

            [DataMember(Name = "masked_account")]
            public string MaskedAccount { get; set; }

            [DataMember(Name = "provider")]
            public string Provider { get; set; }

            [DataMember(Name = "provider_classification", EmitDefaultValue = false)]
            public string ProviderClassification { get; set; }

            [DataMember(Name = "provider_classification_verified")]
            public bool ProviderClassificationVerified { get; set; }

            [DataMember(Name = "connected")]
            public bool Connected { get; set; }

            [DataMember(Name = "currency")]
            public string Currency { get; set; }

            [DataMember(Name = "balance")]
            public double Balance { get; set; }

            [DataMember(Name = "equity")]
            public double Equity { get; set; }

            [DataMember(Name = "used_margin")]
            public double UsedMargin { get; set; }

            [DataMember(Name = "free_margin")]
            public double FreeMargin { get; set; }
        }

        [DataContract]
        private sealed class InstrumentSnapshot
        {
            [DataMember(Name = "symbol")]
            public string Symbol { get; set; }

            [DataMember(Name = "root")]
            public string Root { get; set; }

            [DataMember(Name = "canonical_symbol")]
            public string CanonicalSymbol { get; set; }

            [DataMember(Name = "tick_size")]
            public double TickSize { get; set; }

            [DataMember(Name = "point_value")]
            public double PointValue { get; set; }

            [DataMember(Name = "tick_value")]
            public double TickValue { get; set; }

            [DataMember(Name = "currency")]
            public string Currency { get; set; }

            [DataMember(Name = "max_quantity")]
            public int MaxQuantity { get; set; }

            [DataMember(Name = "metadata_verified")]
            public bool MetadataVerified { get; set; }

            [DataMember(Name = "metadata_source")]
            public string MetadataSource { get; set; }
        }

        [DataContract]
        private sealed class QuoteSnapshot
        {
            [DataMember(Name = "bid")]
            public double Bid { get; set; }

            [DataMember(Name = "ask")]
            public double Ask { get; set; }

            [DataMember(Name = "timestamp_ms")]
            public long TimestampMs { get; set; }
        }

        [DataContract]
        private sealed class RolloverSnapshot
        {
            [DataMember(Name = "active_contract", EmitDefaultValue = false)]
            public string ActiveContract { get; set; }

            [DataMember(Name = "verified")]
            public bool Verified { get; set; }

            [DataMember(Name = "source")]
            public string Source { get; set; }

            [DataMember(Name = "as_of_ms")]
            public long AsOfMs { get; set; }
        }
    }
}
