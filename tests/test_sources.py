import unittest

import pandas as pd

from etf_backend.sources import (
    DDBAdapter, build_source, load_config, source_capabilities, source_descriptions,
)


class FakeDDB:
    def __init__(self, flow_dates, market_date):
        self.flow_dates = flow_dates
        self.market_date = market_date

    def run(self, sql):
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


if __name__ == "__main__":
    unittest.main()
