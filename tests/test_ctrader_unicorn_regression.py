import unittest

from tradingagents.dataflows.ctrader_unicorn import (
    _evaluate_housing_candidate,
    _find_breakers,
    _find_fvgs,
    _overlap,
)


def bar(time, o, h, l, c):
    return {
        "time": time,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": 0,
    }


BARS = [
    # Preceding candles required to establish the
    # verified 05:42 swing low with pivot_window=2.
    bar("2026-09-17T05:33:00+00:00", 29114.3, 29118.4, 29110.3, 29111.1),
    bar("2026-09-17T05:36:00+00:00", 29111.6, 29113.2, 29099.6, 29101.9),
    bar("2026-09-17T05:39:00+00:00", 29101.8, 29107.7, 29088.4, 29088.4),
    bar("2026-09-17T05:42:00+00:00", 29087.8, 29093.8, 29085.9, 29086.7),
    bar("2026-09-17T05:45:00+00:00", 29087.1, 29098.1, 29087.1, 29091.7),
    bar("2026-09-17T05:48:00+00:00", 29091.6, 29099.7, 29088.2, 29092.4),
    bar("2026-09-17T05:51:00+00:00", 29092.6, 29093.8, 29081.2, 29081.2),
    bar("2026-09-17T05:54:00+00:00", 29081.3, 29081.8, 29073.3, 29074.2),
    bar("2026-09-17T05:57:00+00:00", 29074.3, 29074.3, 29066.1, 29068.1),
    bar("2026-09-17T06:00:00+00:00", 29067.7, 29091.5, 29063.8, 29089.3),
    bar("2026-09-17T06:03:00+00:00", 29089.1, 29102.8, 29084.4, 29098.3),
    bar("2026-09-17T06:06:00+00:00", 29098.7, 29108.3, 29090.0, 29090.6),
    bar("2026-09-17T06:09:00+00:00", 29090.9, 29092.1, 29077.3, 29083.6),
    bar("2026-09-17T06:12:00+00:00", 29083.1, 29093.3, 29082.8, 29086.9),
    bar("2026-09-17T06:15:00+00:00", 29087.6, 29089.1, 29071.0, 29088.4),
    bar("2026-09-17T06:18:00+00:00", 29088.1, 29090.7, 29082.1, 29083.9),
    bar("2026-09-17T06:21:00+00:00", 29084.6, 29096.2, 29083.4, 29092.9),
    bar("2026-09-17T06:24:00+00:00", 29093.1, 29123.1, 29093.1, 29121.6),
    bar("2026-09-17T06:27:00+00:00", 29121.3, 29139.3, 29119.2, 29138.3),
    bar("2026-09-17T06:30:00+00:00", 29137.6, 29150.9, 29137.1, 29145.0),
    bar("2026-09-17T06:33:00+00:00", 29145.4, 29153.8, 29140.5, 29145.0),
    bar("2026-09-17T06:36:00+00:00", 29145.4, 29155.5, 29143.4, 29148.9),
    bar("2026-09-17T06:39:00+00:00", 29149.2, 29152.9, 29139.6, 29150.2),
    bar("2026-09-17T06:42:00+00:00", 29150.3, 29164.6, 29147.9, 29158.1),
    bar("2026-09-17T06:45:00+00:00", 29157.3, 29164.2, 29153.8, 29155.8),
    bar("2026-09-17T06:48:00+00:00", 29156.1, 29160.6, 29151.8, 29153.6),
]


class TestVerifiedUnicornCandidate(unittest.TestCase):

    def test_candidate_4_complete_sequence(self):
        breakers = _find_breakers(
            bars=BARS,
            timeframe="M3",
            direction="bullish",
            pivot_window=2,
            confirmation_bars=10,
        )

        breaker = next(
            b for b in breakers
            if b["source_start_time"]
            == "2026-09-17T05:51:00+00:00"
        )

        self.assertEqual(
            breaker["source_end_time"],
            "2026-09-17T05:57:00+00:00",
        )

        self.assertEqual(
            breaker["confirmation_time"],
            "2026-09-17T06:03:00+00:00",
        )

        self.assertTrue(
            breaker["still_holding"]
        )

        fvgs = _find_fvgs(BARS)

        bearish_fvg = next(
            f for f in fvgs
            if f["direction"] == "bearish"
            and f["formed_time"]
            == "2026-09-17T05:54:00+00:00"
        )

        bullish_fvg = next(
            f for f in fvgs
            if f["direction"] == "bullish"
            and f["formed_time"]
            == "2026-09-17T06:03:00+00:00"
        )

        zone = _overlap(
            breaker["source_range_low"],
            breaker["source_range_high"],
            bullish_fvg["low"],
            bullish_fvg["high"],
        )

        self.assertIsNotNone(zone)

        unicorn = {
            "direction": "bullish",
            "timeframe": "M3",

            "breaker_low":
                breaker["source_range_low"],

            "breaker_high":
                breaker["source_range_high"],

            "breaker_source_start":
                breaker["source_start_time"],

            "breaker_source_end":
                breaker["source_end_time"],

            "breaker_confirmation_time":
                breaker["confirmation_time"],

            "fvg_low":
                bullish_fvg["low"],

            "fvg_high":
                bullish_fvg["high"],

            "fvg_formed_time":
                bullish_fvg["formed_time"],

            "unicorn_low":
                zone[0],

            "unicorn_high":
                zone[1],

            "retested": True,

            "retest_time":
                "2026-09-17T06:09:00+00:00",
        }

        result = _evaluate_housing_candidate(
            bars=BARS,
            timeframe="M3",
            direction="bullish",
            unicorn=unicorn,
            opposing_fvg=bearish_fvg,
            confirmation_bars=10,
        )

        self.assertEqual(
            result["status"],
            "ENTRY_MODEL_CONFIRMED",
        )

        # Housing Candle
        self.assertEqual(
            result["housing_time"],
            "2026-09-17T05:51:00+00:00",
        )

        # Negated bearish FVG
        self.assertTrue(
            result["fvg_negated"]
        )

        self.assertEqual(
            result["fvg_negation_time"],
            "2026-09-17T06:00:00+00:00",
        )

        # Body close through Housing / IFVG confirmation
        self.assertEqual(
            result["ifvg_confirmation_time"],
            "2026-09-17T06:03:00+00:00",
        )

        # IFVG retest
        self.assertEqual(
            result["retest_time"],
            "2026-09-17T06:09:00+00:00",
        )

        # Exact validated entry zone
        self.assertAlmostEqual(
            result["entry_zone_low"],
            29081.8,
        )

        self.assertAlmostEqual(
            result["entry_zone_high"],
            29084.4,
        )

        # Second CSD
        second_csd = result["second_csd"]

        self.assertTrue(
            second_csd["confirmed"]
        )

        self.assertEqual(
            second_csd["confirmation_time"],
            "2026-09-17T06:21:00+00:00",
        )

        # New IOF
        new_iof = result["new_iof"]

        self.assertTrue(
            new_iof["confirmed"]
        )

        self.assertTrue(
            new_iof["still_holding"]
        )

        self.assertEqual(
            new_iof["confirmation_time"],
            "2026-09-17T06:42:00+00:00",
        )


if __name__ == "__main__":
    unittest.main()
