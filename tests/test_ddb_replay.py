import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from etf_backend import ddb_replay
from etf_backend.model import SLOTS
from etf_backend.provider import CHINA


def stamps(day, slots):
    return [datetime.combine(date.fromisoformat(day), time.fromisoformat(slot)).replace(tzinfo=CHINA)
            for slot in slots]


class RoutedSession:
    """Minimal session stub that answers by SQL marker."""

    def __init__(self, routes):
        self.routes = routes

    def run(self, sql):
        for marker, frame in self.routes:
            if marker in sql:
                return frame
        raise AssertionError("Unrouted SQL: " + sql)


class NormalizationTest(unittest.TestCase):
    def test_minute_amounts_use_cumulative_differences_and_lunch_anchor(self):
        day = "2026-11-20"
        times = stamps(day, ["09:31", "09:32", "11:30", "13:01", "13:02"])
        values = list(zip(times, [1000., 1300., 5000., 5200., 5400.]))
        amounts = ddb_replay.minute_amounts(day, values)
        self.assertEqual(amounts[0], 1000.)  # opening auction kept in first minute
        self.assertEqual(amounts[1], 300.)
        self.assertEqual(amounts[SLOTS.index("13:01")], 200.)
        self.assertEqual(amounts[SLOTS.index("13:02")], 200.)
        self.assertIsNone(amounts[SLOTS.index("11:30")])

    def test_etf_rows_scale_price_and_turnover_and_drop_off_session_ticks(self):
        day = "2026-11-20"
        frame = pd.DataFrame([
            {"MDDate": pd.Timestamp(day), "SecurityID": "510050.SH", "MDTime": pd.Timestamp("1970-01-01T09:25:00"),
             "LastPx": 11000, "TotalVolumeTrade": 10, "TotalValueTrade": 1000},
            {"MDDate": pd.Timestamp(day), "SecurityID": "510050.SH", "MDTime": pd.Timestamp("1970-01-01T09:31:00"),
             "LastPx": 11000, "TotalVolumeTrade": 100, "TotalValueTrade": 12345678},
            {"MDDate": pd.Timestamp(day), "SecurityID": "510050.SH", "MDTime": pd.Timestamp("1970-01-01T15:02:00"),
             "LastPx": 11000, "TotalVolumeTrade": 200, "TotalValueTrade": 2000},
        ])
        rows = ddb_replay.etf_rows(frame, day)
        self.assertEqual(len(rows["510050.SH"]), 1)
        symbol, stamp, last, cash, volume = rows["510050.SH"][0]
        self.assertEqual(symbol, "510050.SH")
        self.assertTrue(stamp.endswith("+08:00"))
        self.assertAlmostEqual(last, 1.1)
        self.assertAlmostEqual(cash, 1234.5678)
        self.assertEqual(volume, 100)

    def test_futures_rows_keep_cny_units_and_first_queue_level(self):
        day = "2026-11-20"
        frame = pd.DataFrame([
            {"TradeDate": pd.Timestamp(day), "SecurityID": "IH2611", "TradeTime": pd.Timestamp("1970-01-01T13:00:00"),
             "LastPrice": 3000., "Volume": 1200, "Turnover": 6.72e9, "OpenInt": 800,
             "BidPrice": [2999.8, 2999.6], "AskPrice": [3000.2, 3000.4]},
        ])
        rows = ddb_replay.futures_rows(frame, day)
        code, _, last, cash, volume, oi, b1, a1 = rows["IH2611"][0]
        self.assertEqual((code, last, volume, oi), ("IH2611", 3000., 1200, 800))
        self.assertAlmostEqual(cash, 6.72e9)
        self.assertAlmostEqual(b1, 2999.8)
        self.assertAlmostEqual(a1, 3000.2)


class ContractsAndFundsTest(unittest.TestCase):
    def test_contracts_keep_the_nearest_four_in_expiry_order(self):
        frame = pd.DataFrame({"SecurityID": ["IH2709", "IH2612", "IH2611", "IF2611", "IH2703", "IH2706"]})
        session = RoutedSession([("distinct SecurityID", frame)])
        contracts = ddb_replay.contracts_for(session, "2026-11-20", {"IH"})
        self.assertEqual(contracts, {"IH": ["IH2611", "IH2612", "IH2703", "IH2706"]})

    def test_contracts_raise_when_a_product_is_incomplete(self):
        frame = pd.DataFrame({"SecurityID": ["IH2611", "IH2612"]})
        session = RoutedSession([("distinct SecurityID", frame)])
        with self.assertRaisesRegex(ValueError, "fewer than four"):
            ddb_replay.contracts_for(session, "2026-11-20", {"IH"})

    def test_fund_input_builds_share_nav_and_split_rows(self):
        calendar = pd.DataFrame({"MDDate": pd.to_datetime(["2026-11-19", "2026-11-20"])})
        fund = pd.DataFrame({
            "TICKER_SYMBOL": ["510050", "510050"],
            "TRADE_DATE": pd.to_datetime(["2026-11-19", "2026-11-20"]),
            "SHARES": [1e8, 1.1e8], "NAV": [1.0, 1.0], "SPLIT_RATIO": [1.0, 1.1], "NET_INFLOW": [None, 0.0],
        })
        session = RoutedSession([("distinct MDDate", calendar), ("fund_etf_nr", fund)])
        inputs = ddb_replay.fund_input(session, "2026-11-20", ["510050.SH"])
        self.assertEqual(inputs["previous_date"], "2026-11-19")
        records = inputs["funds"]["510050.SH"]
        self.assertEqual([row[0] for row in records["shares"]], ["2026-11-19", "2026-11-20"])
        self.assertEqual(records["splits"], [["2026-11-19", 1.0], ["2026-11-20", 1.1]])

    def test_fund_input_rejects_unreconciled_split(self):
        calendar = pd.DataFrame({"MDDate": pd.to_datetime(["2026-11-19", "2026-11-20"])})
        fund = pd.DataFrame({
            "TICKER_SYMBOL": ["510050"], "TRADE_DATE": pd.to_datetime(["2026-11-20"]),
            "SHARES": [1.1e8], "NAV": [1.0], "SPLIT_RATIO": [1.1], "NET_INFLOW": [None],
        })
        session = RoutedSession([("distinct MDDate", calendar), ("fund_etf_nr", fund)])
        with self.assertRaisesRegex(ValueError, "NET_INFLOW"):
            ddb_replay.fund_input(session, "2026-11-20", ["510050.SH"])


class BaselineTest(unittest.TestCase):
    def test_prior_minutes_scale_snapshot_turnover_to_cny(self):
        day = "2026-11-20"
        ticks = []
        for index, slot in enumerate(SLOTS[:3]):
            ticks.append({"MDDate": pd.Timestamp(day), "SecurityID": "510050.SH",
                          "MDTime": datetime.combine(date.fromisoformat(day), time.fromisoformat(slot)),
                          "TotalValueTrade": (index + 1) * 1e10})
        session = RoutedSession([("'MDSnapshot'", pd.DataFrame(ticks))])
        minutes = ddb_replay.prior_minutes(session, [day], ["510050.SH"])
        # DDB stores turnover ×10000; the first slot keeps the opening auction, later slots use differences.
        self.assertAlmostEqual(minutes[day]["510050.SH"][0], 1e6)
        self.assertAlmostEqual(minutes[day]["510050.SH"][1], 1e6)

    def test_prepare_baseline_publishes_the_rqdata_compatible_shape(self):
        day = "2026-11-20"
        calendar = [date(2026, 9, 21) + timedelta(days=offset) for offset in range(61)]
        dates = [value.isoformat() for value in calendar]
        self.assertEqual(dates[-1], day)
        calendar_frame = pd.DataFrame({"MDDate": pd.to_datetime(dates)})
        daily_frame = pd.DataFrame({
            "TICKER_SYMBOL": ["510050"] * 61,
            "TRADE_DATE": pd.to_datetime(dates),
            "TURNOVER_VALUE": [1e9] * 61,
        })
        ticks = []
        for value in dates:
            for index, slot in enumerate(SLOTS):
                ticks.append({"MDDate": pd.Timestamp(value), "SecurityID": "510050.SH",
                              "MDTime": datetime.combine(date.fromisoformat(value), time.fromisoformat(slot)),
                              "TotalValueTrade": (index + 1) * 1e7})
        session = RoutedSession([
            ("distinct MDDate", calendar_frame),
            ("'mkt_fundd'", daily_frame),
            ("'MDSnapshot'", pd.DataFrame(ticks)),
        ])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "baseline.json"
            ddb_replay.prepare_baseline(session, day, ["510050.SH"], .75, path)
            data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["identity"]["day"], day)
        self.assertEqual(data["identity"]["as_of"], dates[-2])
        item = data["baseline"]["510050.SH"]
        self.assertEqual(len(item["daily"]), 2)
        self.assertTrue(all(value > 0 for value in item["daily"]))
        windows = {(length, slot): refs for length, slot, refs in item["windows"]}
        self.assertIn((5, SLOTS.index("14:57")), windows)
        self.assertIn((10, SLOTS.index("14:57")), windows)
        self.assertTrue(all(value > 0 for value in windows[5, SLOTS.index("14:57")][0]))

    def test_prepare_baselines_reuse_one_history_aggregation(self):
        calendar = [date(2026, 8, 1) + timedelta(days=offset) for offset in range(81)]
        dates = [value.isoformat() for value in calendar]
        targets = [dates[78], dates[80]]
        calendar_frame = pd.DataFrame({"MDDate": pd.to_datetime(dates)})
        daily_frame = pd.DataFrame({
            "TICKER_SYMBOL": ["510050"] * 81,
            "TRADE_DATE": pd.to_datetime(dates),
            "TURNOVER_VALUE": [1e9] * 81,
        })
        ticks = []
        for value in dates:
            for index, slot in enumerate(SLOTS):
                ticks.append({"MDDate": pd.Timestamp(value), "SecurityID": "510050.SH",
                              "MDTime": datetime.combine(date.fromisoformat(value), time.fromisoformat(slot)),
                              "TotalValueTrade": (index + 1) * 1e7})
        session = RoutedSession([
            ("distinct MDDate", calendar_frame),
            ("'mkt_fundd'", daily_frame),
            ("'MDSnapshot'", pd.DataFrame(ticks)),
        ])
        with tempfile.TemporaryDirectory() as directory:
            ddb_replay.prepare_baselines(session, targets, ["510050.SH"], .75, directory)
            written = sorted(path.name for path in Path(directory).glob("*.json"))
            first = json.loads((Path(directory) / f"{targets[0]}.json").read_text(encoding="utf-8"))
            last = json.loads((Path(directory) / f"{targets[-1]}.json").read_text(encoding="utf-8"))
        self.assertEqual(written, [f"{day}.json" for day in targets])
        self.assertEqual(first["identity"]["as_of"], dates[77])
        self.assertEqual(last["identity"]["as_of"], dates[79])


if __name__ == "__main__":
    unittest.main()
