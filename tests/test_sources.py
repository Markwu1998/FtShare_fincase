import unittest

from etf_backend.sources import source_capabilities, source_descriptions


class SourceMetadataTest(unittest.TestCase):
    def test_capabilities_distinguish_share_driven_metrics(self):
        self.assertIn("shares", source_capabilities("RQData"))
        self.assertIn("netflow", source_capabilities("DDB"))
        self.assertIn("calendar", source_capabilities("RQData"))

    def test_descriptions_cover_every_chart_source(self):
        keys = {
            "broadTable", "chart", "chartAmt", "chartBroad", "chartCum",
            "chartDAmt", "chartDNf", "chartNf", "chartTypCase", "chartTypNorm", "etfTable",
        }
        for source in ("rqdata", "ddb"):
            descriptions = source_descriptions(source)
            self.assertTrue(descriptions)
        self.assertTrue(keys.issubset(set(source_descriptions("ddb"))))

    def test_each_description_item_has_label_and_code(self):
        for source in ("rqdata", "ddb"):
            for items in source_descriptions(source).values():
                for item in items:
                    self.assertTrue(item.get("label"))
                    self.assertTrue(item.get("code"))


if __name__ == "__main__":
    unittest.main()
