"""Offline checks for research accounting and causal feature boundaries."""
import importlib.util
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

spec = importlib.util.spec_from_file_location('intraday_research',
    Path(__file__).resolve().parents[1] / 'scripts/research-intraday-factors.py')
research = importlib.util.module_from_spec(spec)
spec.loader.exec_module(research)


class ResearchAccountingTest(unittest.TestCase):
    def test_confirmed_window_union_does_not_double_count_or_include_watch(self):
        events = [{'updates': [
            {'start': '09:31', 'end': '09:35', 'level': 2},
            {'start': '09:34', 'end': '09:38', 'level': 3},
            {'start': '09:40', 'end': '09:41', 'level': 1}]}]
        self.assertEqual(research.window_union(events), list(range(8)))

    def test_missing_and_lunch_are_not_rolling_volume(self):
        values = np.ones(len(research.GRID))
        values[10] = np.nan
        rolling = research.rolling_sum(values, 6)
        self.assertTrue(np.isnan(rolling[:5]).all())
        self.assertTrue(np.isnan(rolling[10:16]).all())
        self.assertTrue(np.isnan(rolling[1440:1445]).all())
        self.assertEqual(rolling[1445], 6)
        self.assertEqual(rolling[16], 6)

    def test_p75_needs_sufficient_history_and_positive_reference(self):
        matrix = np.ones((60, 3))
        matrix[:6, 1] = np.nan
        matrix[:, 2] = 0
        baseline = research.historical_p75(matrix)
        self.assertEqual(baseline[0], 1)
        self.assertTrue(np.isnan(baseline[1:]).all())
        current = np.full((1, 3), 10000.)
        extended = np.r_[matrix, current]
        np.testing.assert_equal(research.historical_p75(extended[:-1]), baseline)

    def test_invalid_denominators_are_missing_not_zero(self):
        for numerator, denominator in [(1, 0), (np.nan, 1), (1, np.nan), (None, 1)]:
            self.assertIsNone(research.ratio(numerator, denominator))

    def test_exact_minute_tick_boundary_is_explicit(self):
        day = '2026-11-04'
        frame = pd.DataFrame({'total_turnover': [100., 110., 120.]}, index=pd.to_datetime([
            day + ' 09:30:57', day + ' 09:31:00', day + ' 09:31:03']))
        sampled = research.resample_day(frame, day)
        self.assertEqual(sampled[2][0], 100.)  # provider-compatible left limit
        self.assertEqual(sampled[4][0], 110.)  # current right-closed convention


if __name__ == '__main__':
    unittest.main()
