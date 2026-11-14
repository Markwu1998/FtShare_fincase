from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from etf_backend.collect import ROOT, history_snapshot, publish, read, validate_replacement
from etf_backend.crowding import add_percentiles, basket_total, turnover_metrics
from etf_backend.crowding_extra import released_values, shsz_symbol
from etf_backend.model import BASKET, INDEX, fund_flows
from etf_backend.web import Archive, validate_seed


class BackendTests(unittest.TestCase):
    def test_macro_ratios_respect_release_date_and_contiguous_quarters(self):
        events = [
            ("2024-04-16", "2024-03-31", 2),
            ("2024-10-18", "2024-09-30", 4),
            ("2025-01-17", "2024-12-31", 5),
            ("2025-03-01", "2024-06-30", 3),
            ("2025-04-16", "2025-03-31", 6),
        ]
        values = released_values(events, ["2025-02-28", "2025-03-03", "2025-04-16"], trailing_gdp=True)
        self.assertNotIn("2025-02-28", values)
        self.assertEqual(values["2025-03-03"], (14, "2024-12-31", "2025-03-01"))
        self.assertEqual(values["2025-04-16"][0], 18)
        self.assertEqual(shsz_symbol("600519.SH"), "600519.XSHG")
        self.assertEqual(shsz_symbol("000001"), "000001.XSHE")
        self.assertIsNone(shsz_symbol("920008"))

    def test_crowding_distinguishes_suspensions_from_missing_quotes(self):
        instruments = {"A", "B", "C", "D"}
        amounts = {"A": 6e8, "B": 4e8, "D": 0}
        partial = turnover_metrics(instruments, amounts, set())
        self.assertEqual(partial["covered"], 3)
        self.assertIsNone(partial["tiers"]["1"]["share"])
        complete = turnover_metrics(instruments, amounts, {"C"})
        self.assertEqual(complete["traded"], 2)
        self.assertEqual(complete["tiers"]["1"], {"count": 1, "amount": 6, "share": 60})
        self.assertEqual(basket_total(instruments, amounts, {"C"}), 1e9)
        self.assertIsNone(basket_total(instruments, amounts, positive=True))

        rows = [{"date": "2019-01-01", "concentration": complete},
                {"date": "2026-01-01", "concentration": partial},
                {"date": "2026-01-02", "concentration": deepcopy(complete)}]
        add_percentiles(rows)
        self.assertEqual([r["concentration"]["tiers"]["1"]["sample"] for r in rows], [1, 0, 1])

    def test_size_and_premium_use_same_day_inputs_and_independent_coverage(self):
        first, second, third = days = ["2026-11-10", "2026-11-11", "2026-11-12"]
        universe = read(ROOT / "config/universe.json")
        universe["funds"] = [f for f in universe["funds"] if f["symbol"] in BASKET]
        target, other, *rest = universe["funds"]
        target["listed_date"] = second
        records = {
            f["symbol"]: {"shares": [(d, 1e8) for d in days],
                          "navs": [(d, 2, 1) for d in days], "splits": []}
            for f in universe["funds"]
        }
        records[other["symbol"]]["navs"].pop()
        closes = {second: {other["symbol"]: 2.2}, third: {target["symbol"]: 2.4,
                  other["symbol"]: 4, rest[0]["symbol"]: 1.8}}
        ms = 1794441600000
        market = {"calendar": days, "daily": [[INDEX, ms, 4000, None]] +
                  [[symbol, ms, 2, 1e8] for symbol in BASKET], "minutes": []}
        snapshot = history_snapshot(market, records, universe, {
            "flow_start": first, "market_start": first, "minute_days": 0,
        }, third, closes)
        size, premium = (snapshot["broad_metrics"][name] for name in ("size", "premium"))
        self.assertEqual(size["total"], [40, None])
        self.assertEqual(size["latest"], third)
        self.assertAlmostEqual(premium["total"][0], 10)
        self.assertAlmostEqual(premium["total"][1], 5)
        self.assertEqual({row["symbol"] for row in premium["members"]}, {target["symbol"], rest[0]["symbol"]})
        self.assertEqual(sum(row[-1] for row in premium["valid"].values()), 2)
        validate_seed(snapshot, "live")
        malformed = deepcopy(snapshot)
        malformed["broad_metrics"]["size"]["total"].pop()
        with self.assertRaisesRegex(ValueError, "Invalid broad metrics"):
            validate_seed(malformed, "live")

    def test_same_day_sz_shares_and_confirmed_split(self):
        days = ["2026-11-11", "2026-11-12"]
        fund = {"symbol": "159915.SZ", "inception_date": "2011-09-20", "listed_date": "2011-12-09"}
        records = {"shares": list(zip(days, [100, 206])), "navs": [(days[0], 2, 1), (days[1], 1, 2)],
                   "splits": [(days[1], 2)]}
        result = fund_flows(fund, records, days)[days[1]]
        self.assertEqual(result["shares_record_date"], days[1])
        self.assertEqual(result["flow"], 6 / 1e8)
        records["splits"] = [(days[1], 3)]
        self.assertIsNone(fund_flows(fund, records, days)[days[1]]["flow"])

    def test_reload_keeps_last_valid_snapshot_and_rejects_losing_history(self):
        original = read(ROOT / "data/demo-seed.json")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "seed.json"
            publish(path, original)
            archive = Archive(path, lambda value: validate_seed(value, "demo"))
            self.assertEqual(archive.load()["built_at"], original["built_at"])
            path.write_text("broken", encoding="utf-8")
            self.assertEqual(archive.load()["built_at"], original["built_at"])
            updated = deepcopy(original)
            updated["built_at"] = "2026-11-14T00:00:00.000Z"
            publish(path, updated)
            self.assertEqual(archive.load()["built_at"], updated["built_at"])
            index = next(i for i, value in enumerate(updated["history"]["netflow"]) if value is not None)
            updated["history"]["netflow"][index] = None
            with self.assertRaisesRegex(ValueError, "lose previously valid"):
                validate_replacement(original, updated)


if __name__ == "__main__":
    unittest.main()
