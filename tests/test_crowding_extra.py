from datetime import date
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from etf_backend.collect import publish
from etf_backend.crowding_extra import _is_daily, industry_rows, path_for, update_extra


DAYS = [date(2026, 11, 16), date(2026, 11, 17)]


class FakeAPI:
    def get_trading_dates(self, start, end):
        return [day for day in DAYS if str(day) >= start and str(day) <= end]

    def index_components(self, code, start_date, end_date):
        size = 300 if code.startswith("000300") else 500
        symbols = {f"{code[-2:]}{i:04d}" for i in range(size)}
        return {pd.Timestamp(day): symbols for day in DAYS}

    def get_instrument_industry(self, batch, source, level, date):
        names = ["电子", "银行", "计算机", "医药", "非银金融"]
        return pd.DataFrame({"first_industry_name": [names[i % len(names)] for i, _ in enumerate(batch)]},
                            index=batch)

    def _frame(self, symbols, field, value):
        index = pd.MultiIndex.from_product([sorted(symbols), pd.to_datetime(DAYS)])
        return pd.DataFrame({field: [value] * len(index)}, index=index)

    def get_price(self, batch, start_date, end_date, fields, adjust_type):
        return self._frame(batch, fields[0], 1e8)

    def get_factor(self, batch, field, start_date, end_date):
        return self._frame(batch, field, 2e8)

    def is_suspended(self, batch, start_date, end_date):
        return pd.DataFrame(False, index=pd.to_datetime(DAYS), columns=sorted(batch))


def valid_row(day="2026-11-13"):
    return {"date": day, "indexes": {"000300.XSHG": {}, "000905.XSHG": {}}}


class CrowdingExtraTest(unittest.TestCase):
    def test_is_daily_detects_weekly_archive(self):
        weekly = [valid_row(f"2026-10-{day:02d}") for day in (2, 9, 16, 23, 30)] + [valid_row("2026-11-06")]
        daily = [valid_row("2026-11-16"), valid_row("2026-11-17")]
        self.assertFalse(_is_daily(weekly))
        self.assertTrue(_is_daily(daily))

    def test_industry_rows_are_daily(self):
        rows = list(industry_rows(FakeAPI(), "2026-11-16", "2026-11-17"))
        self.assertEqual([row["date"] for row in rows], ["2026-11-16", "2026-11-17"])
        self.assertTrue(rows[0]["indexes"]["000300.XSHG"]["capital"])
        self.assertTrue(rows[0]["indexes"]["000905.XSHG"]["turnover"])

    def test_industry_snapshot_marks_weekly_label_cache(self):
        row = {"date": "2026-11-17", "indexes": {"000300.XSHG": {}, "000905.XSHG": {}}}
        with tempfile.TemporaryDirectory() as directory, patch(
                "etf_backend.crowding_extra.industry_rows", return_value=iter([row])):
            base = Path(directory) / "crowding.json"
            snapshot = update_extra(FakeAPI(), "industry", base, None, "2026-11-17")
            self.assertEqual(snapshot["price_frequency"], "daily")
            self.assertEqual(snapshot["label_frequency"], "weekly")

    def test_stale_daily_archive_does_not_silently_succeed(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "crowding.json"
            publish(path_for("industry", base), {
                "provider": "RQData", "module": "industry", "built_at": "2026-11-18T00:00:00Z",
                "rows": [valid_row()],
            })
            with patch("etf_backend.crowding_extra.industry_rows", return_value=iter([])):
                with self.assertRaisesRegex(ValueError, "no fresh observations"):
                    update_extra(FakeAPI(), "industry", base, None, "2026-11-17")


if __name__ == "__main__":
    unittest.main()
