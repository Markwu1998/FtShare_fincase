"""Guard the availability-time and baseline boundaries of single-day replay."""
from datetime import datetime
import importlib.util
from pathlib import Path
import tempfile
import unittest

from etf_backend.burst import RULE_VERSION
from etf_backend.collect import publish
from etf_backend.provider import CHINA

spec = importlib.util.spec_from_file_location(
    "single_day_replay", Path(__file__).resolve().parents[1] / "scripts/replay-burst-day.py")
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)

DAY, SYMBOL = "2026-10-21", "510050.SH"
CODES = ["IH2611", "IH2612", "IH2703", "IH2706"]


def t(time):
    return datetime.fromisoformat(DAY + "T" + time).replace(tzinfo=CHINA)


class SingleDayReplayTests(unittest.TestCase):
    def test_daily_flow_uses_current_nav_and_keeps_missing_basket_total_null(self):
        previous = "2026-10-20"
        fund = {"symbol": SYMBOL, "listed_date": "2025-01-01", "inception_date": "2024-12-01"}
        inputs = {"date": DAY, "previous_date": previous, "funds": {SYMBOL: {
            "shares": [[previous, 100], [DAY, 225]],
            "navs": [[previous, 4, 1], [DAY, 2, 2]], "splits": [[DAY, 2]],
        }}}
        summary = replay.daily_flow_summary(DAY, [fund], inputs)
        self.assertEqual(summary["net_flow_cny"], 50)
        self.assertEqual(summary["by_symbol"][SYMBOL]["split_ratio"], 2)
        partial = replay.daily_flow_summary(DAY, [fund, {**fund, "symbol": "159919.SZ"}], inputs)
        self.assertEqual((partial["valid"], partial["expected"]), (1, 2))
        self.assertIsNone(partial["net_flow_cny"])
        self.assertIn("fund_records_missing", partial["by_symbol"]["159919.SZ"]["issues"])

    def test_uses_only_futures_ticks_available_at_confirmation_cutoff(self):
        report = {"date": DAY, "events": [{"symbol": SYMBOL, "evidence": {}, "updates": [
            {"level": 2, "start": "14:51", "end": "14:55", "received_at": t("14:55:02").isoformat(),
             "amount": 1e8}]}], "minute_volume_shares": {SYMBOL: {f"14:{i}": 10 for i in range(51, 56)}}}
        rows = []
        for code in CODES:
            rows.extend([
                (t("14:50:00"), t("14:50:00"), code, {"last": 3000, "open_interest": 10, "volume": 10, "total_turnover": 0}),
                (t("14:54:59"), t("14:54:59"), code, {"last": 3000, "open_interest": 11, "volume": 11, "total_turnover": 900000}),
                (t("14:55:07"), t("14:55:00"), code, {"last": 3000, "open_interest": 100, "volume": 100, "total_turnover": 81000000}),
            ])
        used = replay.attach_futures(report, sorted(rows, key=lambda r: r[0]), {"IH": CODES}, {SYMBOL: "IH"})
        evidence = report["events"][0]["evidence"]["futures_windows"][0]
        self.assertEqual(evidence["observed_at"], "2026-10-21T14:55:06.000+08:00")
        self.assertEqual(evidence["net_oi_cny"], 3600000)
        self.assertEqual(evidence["etf_volume_shares"], 50)
        self.assertEqual(len(used), 8)

    def test_rejects_target_day_data_as_its_own_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "baseline.json"
            publish(path, {"identity": {"day": DAY, "as_of": DAY, "symbols": [SYMBOL], "quantile": .75,
                                         "rule_version": RULE_VERSION}, "baseline": {}})
            with self.assertRaisesRegex(ValueError, "Baseline date"):
                replay.baseline_for(path, DAY, [SYMBOL], .75)
