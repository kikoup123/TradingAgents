// Londres Phase 33 — NinjaTrader 8 exactly-once local execution bridge.
// Execution is disabled unless Londres/londres_execution_config.json explicitly enables it.
// Demo/live account classification remains private; public commands bind only to hashed aliases.

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
using NinjaTrader.Gui;
#endregion

namespace NinjaTrader.NinjaScript.AddOns
{
    public class LondresExecutionBridge : AddOnBase
    {
        private const int DefaultPollMs = 100;
        private readonly object sync = new object();
        private readonly Dictionary<string, InflightCommand> inflight =
            new Dictionary<string, InflightCommand>(StringComparer.OrdinalIgnoreCase);
        private readonly HashSet<Account> subscribedAccounts = new HashSet<Account>();
        private Timer timer;
        private bool started;
        private bool processing;
        private string root;
        private string requestDir;
        private string responseDir;
        private string inflightDir;
        private BridgeConfig config;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name = "Londres Phase 33 Execution Bridge";
                Description = "Exactly-once command bridge with reconciliation and OCO protection.";
            }
            else if (State == State.Terminated)
                StopBridge();
        }

        protected override void OnWindowCreated(Window window)
        {
            if (window is ControlCenter)
                StartBridge();
        }

        protected override void OnWindowDestroyed(Window window)
        {
            if (window is ControlCenter)
                StopBridge();
        }

        private void StartBridge()
        {
            lock (sync)
            {
                if (started)
                    return;
                root = Path.Combine(NinjaTrader.Core.Globals.UserDataDir, "Londres", "Execution");
                requestDir = Path.Combine(root, "requests");
                responseDir = Path.Combine(root, "responses");
                inflightDir = Path.Combine(root, "inflight");
                Directory.CreateDirectory(requestDir);
                Directory.CreateDirectory(responseDir);
                Directory.CreateDirectory(inflightDir);
                config = LoadConfig(Path.Combine(NinjaTrader.Core.Globals.UserDataDir, "Londres", "londres_execution_config.json"));
                SubscribeAccounts();
                int pollMs = config.PollIntervalMs <= 0 ? DefaultPollMs : config.PollIntervalMs;
                started = true;
                timer = new Timer(_ => SafePoll(), null, 0, Math.Max(50, Math.Min(5000, pollMs)));
            }
        }

        private void StopBridge()
        {
            lock (sync)
            {
                started = false;
                if (timer != null)
                {
                    timer.Dispose();
                    timer = null;
                }
            }
            foreach (Account account in subscribedAccounts.ToList())
            {
                try { account.OrderUpdate -= OnOrderUpdate; } catch { }
                try { account.ExecutionUpdate -= OnExecutionUpdate; } catch { }
            }
            subscribedAccounts.Clear();
        }

        private void SubscribeAccounts()
        {
            List<Account> accounts;
            lock (Account.All)
                accounts = Account.All.ToList();
            foreach (Account account in accounts)
            {
                if (account == null || subscribedAccounts.Contains(account))
                    continue;
                account.OrderUpdate += OnOrderUpdate;
                account.ExecutionUpdate += OnExecutionUpdate;
                subscribedAccounts.Add(account);
            }
        }

        private void SafePoll()
        {
            lock (sync)
            {
                if (!started || processing)
                    return;
                processing = true;
            }
            try
            {
                SubscribeAccounts();
                foreach (string path in Directory.GetFiles(requestDir, "*.json").OrderBy(value => value))
                {
                    string response = Path.Combine(responseDir, Path.GetFileName(path));
                    if (File.Exists(response))
                        continue;
                    try { ProcessRequest(path, response); }
                    catch (Exception exception)
                    {
                        RequestEnvelope fallback = IdentityFromFile(path);
                        WriteResponse(response, fallback, Receipt.ReconciliationRequired("NINJATRADER_BRIDGE_EXCEPTION", exception.GetType().Name));
                    }
                }
            }
            finally
            {
                lock (sync)
                    processing = false;
            }
        }

        private void ProcessRequest(string requestPath, string responsePath)
        {
            RequestEnvelope envelope = ReadJson<RequestEnvelope>(requestPath);
            if (envelope == null || envelope.SchemaVersion != 1 || envelope.Command == null)
                throw new InvalidDataException("Invalid Phase 33 NinjaTrader request schema");
            if (!string.Equals(envelope.CommandId, envelope.Command.CommandId, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("NinjaTrader request command identity mismatch");
            if (envelope.Operation == "RECONCILE")
            {
                WriteResponse(responsePath, envelope, Reconcile(envelope.Command));
                return;
            }
            if (envelope.Operation != "SUBMIT_MARKET")
                throw new InvalidDataException("Unsupported NinjaTrader execution operation");
            if (!config.ExecutionEnabled)
            {
                WriteResponse(responsePath, envelope, Receipt.Rejected("NINJATRADER_LOCAL_EXECUTION_NOT_ENABLED", true));
                return;
            }

            ValidateCommand(envelope.Command);
            string markerPath = Path.Combine(inflightDir, envelope.Command.CommandId + ".json");
            if (File.Exists(markerPath))
            {
                WriteResponse(responsePath, envelope, Reconcile(envelope.Command));
                return;
            }

            Account account = ResolveAccount(envelope.Command.AccountAlias);
            Instrument instrument = Instrument.GetInstrument(envelope.Command.BrokerSymbol);
            if (account == null || instrument == null)
            {
                WriteResponse(responsePath, envelope, Receipt.Rejected("NINJATRADER_ACCOUNT_OR_CONTRACT_UNAVAILABLE", true));
                return;
            }
            int quantity = ExactQuantity(envelope.Command.ExactVolume);
            PersistMarker(markerPath, envelope);

            InflightCommand context = new InflightCommand
            {
                Envelope = envelope,
                Account = account,
                Instrument = instrument,
                MarkerPath = markerPath,
                ResponsePath = responsePath,
                RequestedQuantity = quantity
            };
            lock (sync)
                inflight[envelope.Command.CommandId] = context;

            Action submit = () =>
            {
                try
                {
                    OrderAction action = envelope.Command.Direction == "BULLISH" ? OrderAction.Buy : OrderAction.SellShort;
                    context.EntryOrder = account.CreateOrder(
                        instrument,
                        action,
                        OrderType.Market,
                        OrderEntry.Automated,
                        TimeInForce.Gtc,
                        quantity,
                        0,
                        0,
                        string.Empty,
                        envelope.Command.ClientOrderLabel,
                        NinjaTrader.Core.Globals.MaxDate,
                        null);
                    account.Submit(new[] { context.EntryOrder });
                }
                catch (Exception exception)
                {
                    WriteResponse(responsePath, envelope, Receipt.ReconciliationRequired("NINJATRADER_SUBMIT_EXCEPTION", exception.GetType().Name));
                }
            };
            if (Application.Current != null && Application.Current.Dispatcher != null)
                Application.Current.Dispatcher.Invoke(submit);
            else
                submit();
        }

        private void OnExecutionUpdate(object sender, ExecutionEventArgs e)
        {
            if (e == null || e.Execution == null || e.Execution.Order == null)
                return;
            InflightCommand context = FindByOrderName(e.Execution.Order.Name);
            if (context == null || e.Quantity <= 0)
                return;
            lock (context.Sync)
            {
                if (e.Execution.Order != context.EntryOrder && e.Execution.Order.Name != context.Envelope.Command.ClientOrderLabel)
                    return;
                context.FilledQuantity += e.Quantity;
                context.FillNotional += e.Price * e.Quantity;
                try
                {
                    SubmitProtectionForFill(context, e.Quantity);
                }
                catch (Exception exception)
                {
                    WriteResponse(context.ResponsePath, context.Envelope, Receipt.ReconciliationRequired("NINJATRADER_PROTECTION_SUBMIT_EXCEPTION", exception.GetType().Name));
                }
            }
        }

        private void SubmitProtectionForFill(InflightCommand context, int fillQuantity)
        {
            int sequence = context.Brackets.Count + 1;
            string prefix = context.Envelope.Command.CommandId.Substring(0, Math.Min(12, context.Envelope.Command.CommandId.Length));
            string oco = string.Format("L33OCO-{0}-{1}-{2}", prefix, context.ProtectedQuantity, fillQuantity);
            string stopName = string.Format("L33S-{0}-{1}", prefix, sequence);
            string targetName = string.Format("L33T-{0}-{1}", prefix, sequence);
            bool bullish = context.Envelope.Command.Direction == "BULLISH";
            OrderAction exitAction = bullish ? OrderAction.Sell : OrderAction.BuyToCover;
            Order stop = context.Account.CreateOrder(
                context.Instrument,
                exitAction,
                OrderType.StopMarket,
                OrderEntry.Automated,
                TimeInForce.Gtc,
                fillQuantity,
                0,
                context.Envelope.Command.StopPrice,
                oco,
                stopName,
                NinjaTrader.Core.Globals.MaxDate,
                null);
            Order target = context.Account.CreateOrder(
                context.Instrument,
                exitAction,
                OrderType.Limit,
                OrderEntry.Automated,
                TimeInForce.Gtc,
                fillQuantity,
                context.Envelope.Command.TargetPrice,
                0,
                oco,
                targetName,
                NinjaTrader.Core.Globals.MaxDate,
                null);
            ProtectionBracket bracket = new ProtectionBracket
            {
                Stop = stop,
                Target = target,
                Quantity = fillQuantity
            };
            context.Brackets.Add(bracket);
            context.Account.Submit(new[] { stop, target });
            context.ProtectedQuantity += fillQuantity;
        }

        private void OnOrderUpdate(object sender, OrderEventArgs e)
        {
            if (e == null || e.Order == null)
                return;
            InflightCommand context = FindByOrderName(e.Order.Name);
            if (context == null)
                return;
            lock (context.Sync)
            {
                if (e.Order.Name == context.Envelope.Command.ClientOrderLabel)
                {
                    context.EntryOrder = e.Order;
                    if (e.OrderState == OrderState.Rejected && e.Filled == 0)
                    {
                        WriteResponse(context.ResponsePath, context.Envelope,
                            Receipt.Rejected("NINJATRADER_ENTRY_REJECTED", true, e.Order.OrderId, e.Comment));
                        return;
                    }
                    if (e.OrderState == OrderState.Unknown)
                    {
                        WriteResponse(context.ResponsePath, context.Envelope,
                            Receipt.ReconciliationRequired("NINJATRADER_ENTRY_ORDER_UNKNOWN", e.Comment));
                        return;
                    }
                }
                foreach (ProtectionBracket bracket in context.Brackets)
                {
                    if (bracket.Stop == e.Order)
                        bracket.StopReady = IsProtectiveOrderReady(e.OrderState);
                    if (bracket.Target == e.Order)
                        bracket.TargetReady = IsProtectiveOrderReady(e.OrderState);
                    if ((bracket.Stop == e.Order || bracket.Target == e.Order) && e.OrderState == OrderState.Rejected)
                    {
                        WriteResponse(context.ResponsePath, context.Envelope,
                            Receipt.ReconciliationRequired("NINJATRADER_PROTECTIVE_ORDER_REJECTED", e.Comment));
                        return;
                    }
                }
                TryAcknowledge(context);
            }
        }

        private void TryAcknowledge(InflightCommand context)
        {
            if (context.EntryOrder == null || context.EntryOrder.OrderState != OrderState.Filled)
                return;
            if (context.FilledQuantity != context.RequestedQuantity || context.ProtectedQuantity != context.RequestedQuantity)
                return;
            if (context.Brackets.Count == 0 || context.Brackets.Any(item => !item.StopReady || !item.TargetReady))
                return;
            double average = context.FilledQuantity > 0 ? context.FillNotional / context.FilledQuantity : context.EntryOrder.AverageFillPrice;
            Receipt receipt = Receipt.Acknowledged(
                context.EntryOrder.OrderId,
                context.RequestedQuantity,
                average);
            WriteResponse(context.ResponsePath, context.Envelope, receipt);
        }

        private Receipt Reconcile(ExecutionCommand command)
        {
            ValidateCommand(command);
            Account account = ResolveAccount(command.AccountAlias);
            if (account == null)
                return Receipt.ReconciliationRequired("NINJATRADER_ACCOUNT_ROUTE_NOT_FOUND", null);
            int expected = ExactQuantity(command.ExactVolume);
            List<Execution> executions;
            lock (account.Executions)
                executions = account.Executions.Where(item => item != null && item.Order != null && item.Order.Name == command.ClientOrderLabel).ToList();
            int filled = executions.Sum(item => item.Quantity);
            double notional = executions.Sum(item => item.Quantity * item.Price);
            List<Order> orders;
            lock (account.Orders)
                orders = account.Orders.Where(item => item != null).ToList();
            Order entry = orders.FirstOrDefault(item => item.Name == command.ClientOrderLabel);
            string prefix = command.CommandId.Substring(0, Math.Min(12, command.CommandId.Length));
            List<Order> stops = orders.Where(item => item.Name != null && item.Name.StartsWith("L33S-" + prefix + "-", StringComparison.OrdinalIgnoreCase) && IsProtectiveOrderReady(item.OrderState)).ToList();
            List<Order> targets = orders.Where(item => item.Name != null && item.Name.StartsWith("L33T-" + prefix + "-", StringComparison.OrdinalIgnoreCase) && IsProtectiveOrderReady(item.OrderState)).ToList();
            int stopQuantity = stops.Sum(item => item.Quantity - item.Filled);
            int targetQuantity = targets.Sum(item => item.Quantity - item.Filled);
            bool stopPrices = stops.All(item => NearlyEqual(item.StopPrice, command.StopPrice));
            bool targetPrices = targets.All(item => NearlyEqual(item.LimitPrice, command.TargetPrice));
            if (filled == expected && stopQuantity == expected && targetQuantity == expected && stopPrices && targetPrices)
            {
                double average = filled > 0 ? notional / filled : (entry != null ? entry.AverageFillPrice : 0.0);
                return Receipt.Acknowledged(entry != null ? entry.OrderId : null, expected, average);
            }
            if (entry != null && entry.OrderState == OrderState.Rejected && entry.Filled == 0 && filled == 0)
                return Receipt.Rejected("NINJATRADER_RECONCILED_REJECTED_NO_FILL", true, entry.OrderId, null);
            if (entry != null || filled > 0 || stops.Count > 0 || targets.Count > 0)
                return Receipt.ReconciliationRequired("NINJATRADER_MATCH_FOUND_BUT_EXACT_PROTECTION_NOT_PROVEN", null, entry != null ? entry.OrderId : null, filled, filled > 0 ? notional / filled : (double?)null, stopQuantity > 0, targetQuantity > 0);
            return Receipt.NotFound("NINJATRADER_NO_CURRENT_SESSION_COMMAND_EVIDENCE");
        }

        private InflightCommand FindByOrderName(string name)
        {
            if (string.IsNullOrWhiteSpace(name))
                return null;
            lock (sync)
            {
                foreach (InflightCommand item in inflight.Values)
                {
                    if (item.Envelope.Command.ClientOrderLabel == name)
                        return item;
                    string prefix = item.Envelope.Command.CommandId.Substring(0, Math.Min(12, item.Envelope.Command.CommandId.Length));
                    if (name.StartsWith("L33S-" + prefix + "-", StringComparison.OrdinalIgnoreCase) || name.StartsWith("L33T-" + prefix + "-", StringComparison.OrdinalIgnoreCase))
                        return item;
                }
            }
            return null;
        }

        private Account ResolveAccount(string alias)
        {
            List<Account> accounts;
            lock (Account.All)
                accounts = Account.All.ToList();
            foreach (Account account in accounts)
            {
                if (account == null || account.Connection == null || account.Connection.Options == null)
                    continue;
                string connectionName = account.Connection.Options.Name;
                string key = StableAccountKey(connectionName, account.Name);
                if (PublicAlias(key) == alias)
                    return account;
            }
            return null;
        }

        private static void ValidateCommand(ExecutionCommand command)
        {
            if (command == null || string.IsNullOrWhiteSpace(command.CommandId) || string.IsNullOrWhiteSpace(command.AccountAlias))
                throw new InvalidDataException("NinjaTrader command identity is required");
            if (command.Venue != "NINJATRADER" || command.BrokerType != "NINJATRADER")
                throw new InvalidDataException("NinjaTrader command venue/type mismatch");
            if (command.VolumeUnit != "contracts")
                throw new InvalidDataException("NinjaTrader command requires contract volume");
            if (command.ExecutionStyle != "MARKET_ON_SIGNAL")
                throw new InvalidDataException("NinjaTrader execution bridge supports MARKET_ON_SIGNAL only");
            if (command.Direction != "BULLISH" && command.Direction != "BEARISH")
                throw new InvalidDataException("NinjaTrader command direction invalid");
            if (command.StopPrice <= 0 || command.TargetPrice <= 0)
                throw new InvalidDataException("NinjaTrader stop/target must be positive");
        }

        private static int ExactQuantity(double value)
        {
            if (double.IsNaN(value) || double.IsInfinity(value) || value <= 0 || Math.Abs(value - Math.Round(value)) > 1e-9)
                throw new InvalidDataException("NinjaTrader exact contract quantity must be a positive integer");
            return checked((int)Math.Round(value));
        }

        private static bool IsProtectiveOrderReady(OrderState state)
        {
            return state == OrderState.Accepted || state == OrderState.Working || state == OrderState.Submitted;
        }

        private void PersistMarker(string path, RequestEnvelope envelope)
        {
            byte[] payload = Serialize(envelope);
            using (FileStream stream = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                stream.Write(payload, 0, payload.Length);
                stream.Flush(true);
            }
        }

        private static void WriteResponse(string path, RequestEnvelope envelope, Receipt receipt)
        {
            ResponseEnvelope response = new ResponseEnvelope
            {
                SchemaVersion = 1,
                CommandId = envelope.CommandId,
                Operation = envelope.Operation,
                GeneratedAtMs = ToUnixMilliseconds(DateTime.UtcNow),
                AccountEnvironment = "HIDDEN_INTERNAL",
                Receipt = receipt
            };
            byte[] payload = Serialize(response);
            string temp = path + ".tmp";
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            using (FileStream stream = new FileStream(temp, FileMode.Create, FileAccess.Write, FileShare.None))
            {
                stream.Write(payload, 0, payload.Length);
                stream.Flush(true);
            }
            if (File.Exists(path))
                File.Delete(temp);
            else
                File.Move(temp, path);
        }

        private static RequestEnvelope IdentityFromFile(string path)
        {
            string name = Path.GetFileNameWithoutExtension(path);
            int dot = name.IndexOf('.');
            return new RequestEnvelope
            {
                SchemaVersion = 1,
                CommandId = dot >= 0 ? name.Substring(0, dot) : name,
                Operation = dot >= 0 ? name.Substring(dot + 1) : "UNKNOWN"
            };
        }

        private static T ReadJson<T>(string path) where T : class
        {
            DataContractJsonSerializer serializer = new DataContractJsonSerializer(typeof(T), new DataContractJsonSerializerSettings { UseSimpleDictionaryFormat = true });
            using (FileStream stream = File.OpenRead(path))
                return serializer.ReadObject(stream) as T;
        }

        private static byte[] Serialize<T>(T value)
        {
            DataContractJsonSerializer serializer = new DataContractJsonSerializer(typeof(T), new DataContractJsonSerializerSettings { UseSimpleDictionaryFormat = true });
            using (MemoryStream stream = new MemoryStream())
            {
                serializer.WriteObject(stream, value);
                return stream.ToArray();
            }
        }

        private static BridgeConfig LoadConfig(string path)
        {
            if (!File.Exists(path))
                return new BridgeConfig { ExecutionEnabled = false, PollIntervalMs = DefaultPollMs };
            BridgeConfig result = ReadJson<BridgeConfig>(path);
            if (result == null)
                throw new InvalidDataException("NinjaTrader execution config is invalid");
            return result;
        }

        private static string StableAccountKey(string connectionName, string accountName)
        {
            using (SHA256 sha = SHA256.Create())
                return Hex(sha.ComputeHash(Encoding.UTF8.GetBytes(connectionName + "\n" + accountName)));
        }

        private static string PublicAlias(string accountKey)
        {
            using (SHA256 sha = SHA256.Create())
                return "NT-" + Hex(sha.ComputeHash(Encoding.UTF8.GetBytes(accountKey))).Substring(0, 8).ToUpperInvariant();
        }

        private static string Hex(byte[] data) { return BitConverter.ToString(data).Replace("-", "").ToLowerInvariant(); }
        private static long ToUnixMilliseconds(DateTime utc) { DateTime value = utc.Kind == DateTimeKind.Utc ? utc : utc.ToUniversalTime(); return (long)(value - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalMilliseconds; }
        private static bool NearlyEqual(double left, double right) { return Math.Abs(left - right) <= 1e-9; }

        private sealed class InflightCommand
        {
            public readonly object Sync = new object();
            public RequestEnvelope Envelope;
            public Account Account;
            public Instrument Instrument;
            public Order EntryOrder;
            public string MarkerPath;
            public string ResponsePath;
            public int RequestedQuantity;
            public int FilledQuantity;
            public int ProtectedQuantity;
            public double FillNotional;
            public readonly List<ProtectionBracket> Brackets = new List<ProtectionBracket>();
        }

        private sealed class ProtectionBracket
        {
            public Order Stop;
            public Order Target;
            public int Quantity;
            public bool StopReady;
            public bool TargetReady;
        }

        [DataContract]
        private sealed class BridgeConfig
        {
            [DataMember(Name = "execution_enabled")] public bool ExecutionEnabled { get; set; }
            [DataMember(Name = "poll_interval_ms")] public int PollIntervalMs { get; set; }
        }

        [DataContract]
        private sealed class RequestEnvelope
        {
            [DataMember(Name = "schema_version")] public int SchemaVersion { get; set; }
            [DataMember(Name = "operation")] public string Operation { get; set; }
            [DataMember(Name = "command_id")] public string CommandId { get; set; }
            [DataMember(Name = "requested_at_ms")] public long RequestedAtMs { get; set; }
            [DataMember(Name = "command")] public ExecutionCommand Command { get; set; }
        }

        [DataContract]
        private sealed class ExecutionCommand
        {
            [DataMember(Name = "command_id")] public string CommandId { get; set; }
            [DataMember(Name = "client_order_label")] public string ClientOrderLabel { get; set; }
            [DataMember(Name = "account_alias")] public string AccountAlias { get; set; }
            [DataMember(Name = "venue")] public string Venue { get; set; }
            [DataMember(Name = "broker_type")] public string BrokerType { get; set; }
            [DataMember(Name = "broker_symbol")] public string BrokerSymbol { get; set; }
            [DataMember(Name = "direction")] public string Direction { get; set; }
            [DataMember(Name = "execution_style")] public string ExecutionStyle { get; set; }
            [DataMember(Name = "exact_volume")] public double ExactVolume { get; set; }
            [DataMember(Name = "volume_unit")] public string VolumeUnit { get; set; }
            [DataMember(Name = "stop_price")] public double StopPrice { get; set; }
            [DataMember(Name = "target_price")] public double TargetPrice { get; set; }
        }

        [DataContract]
        private sealed class ResponseEnvelope
        {
            [DataMember(Name = "schema_version")] public int SchemaVersion { get; set; }
            [DataMember(Name = "command_id")] public string CommandId { get; set; }
            [DataMember(Name = "operation")] public string Operation { get; set; }
            [DataMember(Name = "generated_at_ms")] public long GeneratedAtMs { get; set; }
            [DataMember(Name = "account_environment")] public string AccountEnvironment { get; set; }
            [DataMember(Name = "receipt")] public Receipt Receipt { get; set; }
        }

        [DataContract]
        private sealed class Receipt
        {
            [DataMember(Name = "outcome")] public string Outcome { get; set; }
            [DataMember(Name = "broker_order_id")] public string BrokerOrderId { get; set; }
            [DataMember(Name = "broker_position_id")] public string BrokerPositionId { get; set; }
            [DataMember(Name = "filled_volume")] public double? FilledVolume { get; set; }
            [DataMember(Name = "average_fill_price")] public double? AverageFillPrice { get; set; }
            [DataMember(Name = "stop_protection_active")] public bool StopProtectionActive { get; set; }
            [DataMember(Name = "target_protection_active")] public bool TargetProtectionActive { get; set; }
            [DataMember(Name = "provider_code")] public string ProviderCode { get; set; }
            [DataMember(Name = "provider_message")] public string ProviderMessage { get; set; }
            [DataMember(Name = "definite_no_fill")] public bool DefiniteNoFill { get; set; }

            public static Receipt Acknowledged(string orderId, int quantity, double average)
            {
                return new Receipt { Outcome = "ACKNOWLEDGED", BrokerOrderId = orderId, FilledVolume = quantity, AverageFillPrice = average > 0 ? average : (double?)null, StopProtectionActive = true, TargetProtectionActive = true, ProviderCode = "NINJATRADER_FILLED_AND_OCO_PROTECTED", DefiniteNoFill = false };
            }

            public static Receipt Rejected(string code, bool definiteNoFill, string orderId = null, string message = null)
            {
                return new Receipt { Outcome = "REJECTED", BrokerOrderId = orderId, FilledVolume = 0, StopProtectionActive = false, TargetProtectionActive = false, ProviderCode = code, ProviderMessage = message, DefiniteNoFill = definiteNoFill };
            }

            public static Receipt ReconciliationRequired(string code, string message, string orderId = null, int? filled = null, double? average = null, bool stop = false, bool target = false)
            {
                return new Receipt { Outcome = "RECONCILIATION_REQUIRED", BrokerOrderId = orderId, FilledVolume = filled, AverageFillPrice = average, StopProtectionActive = stop, TargetProtectionActive = target, ProviderCode = code, ProviderMessage = message, DefiniteNoFill = false };
            }

            public static Receipt NotFound(string code)
            {
                return new Receipt { Outcome = "NOT_FOUND", ProviderCode = code, DefiniteNoFill = false };
            }
        }
    }
}
