import unittest
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

import pandas as pd

from etf_backend.sources import (
    DDBAdapter, build_source, load_config, source_capabilities, source_descriptions,
)
from etf_backend.model import fund_flows


class FakeDDB:
    def __init__(self, flow_dates, market_date):
        self.flow_dates = flow_dates
        self.market_date = market_date

    def run(self, sql):
        if "'mkt_fundd'" in sql:
            return pd.DataFrame({"TICKER_SYMBOL": list(self.flow_dates),
                                 "maxd": [pd.Timestamp(self.market_date)] * len(self.flow_dates)})
        if "group by TICKER_SYMBOL" in sql:
            return pd.DataFrame({"TICKER_SYMBOL": list(self.flow_dates),
                                 "maxd": [pd.Timestamp(day) for day in self.flow_dates.values()]})
        return pd.DataFrame({"maxd": [pd.Timestamp(self.market_date)]})


class SourceMetadataTest(unittest.TestCase):
    def setUp(self):
        self.config = load_config()

    def test_capabilities_come_from_source_config(self):
        self.assertIn("shares", source_capabilities(self.config, "rqdata"))
        self.assertIn("netflow", source_capabilities(self.config, "ddb"))
        self.assertIn("calendar", source_capabilities(self.config, "rqdata"))
        self.assertNotIn("calendar", source_capabilities(self.config, "ddb"))

    def test_descriptions_cover_every_chart_source(self):
        keys = {
            "broadTable", "chart", "chartAmt", "chartBroad", "chartCum",
            "chartDAmt", "chartDNf", "chartNf", "chartTypCase", "chartTypNorm", "etfTable",
        }
        for source in ("rqdata", "ddb"):
            self.assertTrue(keys.issubset(set(source_descriptions(source))))

    def test_unknown_source_has_no_description(self):
        self.assertEqual(source_descriptions("future"), {})

    def test_each_description_item_has_label_and_code(self):
        for source in ("rqdata", "ddb"):
            for items in source_descriptions(source).values():
                for item in items:
                    self.assertTrue(item.get("label"))
                    self.assertTrue(item.get("code"))

    def test_adapter_factory_does_not_require_rqdata_for_ddb(self):
        self.assertIsInstance(build_source("ddb", self.config), DDBAdapter)
        self.assertEqual(build_source("ddb", self.config).id, "ddb")

    def test_ddb_status_requires_every_core_symbol(self):
        adapter = DDBAdapter(self.config)
        core = adapter._core()
        target = "2026-11-17"
        flow = {symbol: target for symbol in core}
        adapter._session = FakeDDB(flow, target)
        self.assertTrue(adapter.status(target).ready)

        flow.pop(core[0])
        adapter._session = FakeDDB(flow, target)
        status = adapter.status(target)
        self.assertFalse(status.ready)
        self.assertIn("缺少核心", status.reason)

    def test_ddb_status_rejects_one_stale_core(self):
        adapter = DDBAdapter(self.config)
        core = adapter._core()
        target = "2026-11-17"
        flow = {symbol: target for symbol in core}
        flow[core[0]] = "2026-11-14"
        adapter._session = FakeDDB(flow, target)
        status = adapter.status(target)
        self.assertFalse(status.ready)
        self.assertIn("未到", status.reason)

    def test_ddb_status_rejects_stale_market(self):
        adapter = DDBAdapter(self.config)
        core = adapter._core()
        target = "2026-11-17"
        adapter._session = FakeDDB({symbol: target for symbol in core}, "2026-11-14")
        status = adapter.status(target)
        self.assertFalse(status.ready)
        self.assertIn("mkt_fundd", status.reason)

    def test_ddb_missing_single_market_is_not_hidden_by_global_max(self):
        adapter = DDBAdapter(self.config)
        core = adapter._core()
        class Session(FakeDDB):
            def run(self, sql):
                result = super().run(sql)
                return result.iloc[1:] if "'mkt_fundd'" in sql else result
        adapter._session = Session(dict.fromkeys(core, "2026-11-13"), "2026-11-13")
        self.assertFalse(adapter.status("2026-11-13").ready)

    def test_ddb_target_uses_observed_index_session_for_weekend(self):
        adapter = DDBAdapter(self.config)
        adapter._session = FakeDDB({}, "2026-11-13")
        self.assertEqual(adapter.target_date("2026-11-15"), "2026-11-13")


class DDBCollectionTest(unittest.TestCase):
    def collect(self, split=1.1, expected=0., missing_nav=False, duplicate=False):
        fund = {"symbol": "510050.SH", "inception_date": "2004-01-01", "listed_date": "2005-01-01"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            (root / "config/universe.json").write_text(json.dumps({"funds": [fund]}), encoding="utf-8")
            (root / "config/basket.json").write_text(json.dumps({"symbols": [fund["symbol"]]}), encoding="utf-8")
            adapter = DDBAdapter()
            def query(sql):
                dates = pd.to_datetime(["2026-11-12", "2026-11-13"])
                if "'fund_etf_info'" in sql:
                    return pd.DataFrame({"SECURITY_ID": ["id1"], "TICKER_SYMBOL": ["510050"]})
                if "'mkt_fundd'" in sql:
                    return pd.DataFrame({"TICKER_SYMBOL": ["510050"] * 2, "TRADE_DATE": dates,
                                         "CLOSE_PRICE": [10., 10 / split], "TURNOVER_VOL": [1000] * 2,
                                         "TURNOVER_VALUE": [10000] * 2})
                if "'fund_etf_nr'" in sql:
                    frame = pd.DataFrame({"TICKER_SYMBOL": ["510050"] * 2, "TRADE_DATE": dates,
                                         "SHARES": [1e8, 1e8 * split], "NAV": [10., None if missing_nav else 10 / split],
                                         "SPLIT_RATIO": [1., split], "NET_INFLOW": [None, expected]})
                    return pd.concat([frame, frame.iloc[-1:]]) if duplicate else frame
                if "'fund_nav'" in sql:
                    return pd.DataFrame({"SECURITY_ID": ["id1"], "END_DATE": [dates[-1]], "NAV": [10 / split]})
                return pd.DataFrame({"TRADE_DT": dates, "S_DQ_CLOSE": [3000, 3001]})
            adapter.query = query
            with patch("etf_backend.sources.ROOT", root):
                market, records, _ = adapter.collect_history("2026-11-12", "2026-11-13")
            return fund_flows(fund, records["funds"][fund["symbol"]], market["calendar"])["2026-11-13"]

    def test_split_is_reconciled_and_does_not_create_false_flow(self):
        row = self.collect()
        self.assertAlmostEqual(row["flow"], 0, places=12)
        self.assertEqual(row["split_ratio"], 1.1)
        self.assertIn("DDB", row["split_source"])

    def test_split_requires_independent_netflow(self):
        with self.assertRaisesRegex(ValueError, "requires NET_INFLOW"):
            self.collect(expected=None)

    def test_netflow_mismatch_rejects_publication(self):
        with self.assertRaisesRegex(ValueError, "NET_INFLOW mismatch"):
            self.collect(expected=1000)

    def test_nav_fallback_uses_same_date_unit_nav(self):
        self.assertAlmostEqual(self.collect(missing_nav=True)["flow"], 0, places=12)

    def test_duplicate_fund_dates_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.collect(duplicate=True)


if __name__ == "__main__":
    unittest.main()
