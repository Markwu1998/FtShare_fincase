"""Synthetic DDB contract tests: no credentials, live database or quota usage."""
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout

import numpy as np
import pandas as pd

from etf_backend import preopen
from etf_backend.collect import publish
from etf_backend.model import SLOTS
from etf_backend.realtime import PushSession
from etf_backend.tick_factors import build_references, load_references

SYMBOL, INDEX, DAY = '510050.SH', '000016.SH', '2026-11-26'
DAYS = pd.bdate_range(end='2026-11-25', periods=60).strftime('%Y-%m-%d').tolist()


def snapshots(day, ordinal):
    """Five-second cumulative ETF snapshots plus minute-end index observations."""
    stamps = (pd.date_range(day + ' 09:30:00', day + ' 11:30:00', freq='5s').append(
        pd.date_range(day + ' 13:00:00', day + ' 15:00:00', freq='5s')))
    cash = (np.arange(len(stamps)) + 1) * (100 + ordinal)
    etf = pd.DataFrame({'MDDate': pd.Timestamp(day), 'SecurityID': SYMBOL,
                        'MDTime': stamps, 'TotalValueTrade': cash * 10000., 'LastPx': 11000.})
    index = pd.DataFrame({'MDDate': pd.Timestamp(day), 'SecurityID': INDEX,
                          'MDTime': pd.to_datetime([day + ' ' + slot for slot in SLOTS]),
                          'TotalValueTrade': 0., 'LastPx': np.arange(len(SLOTS)) * 1000. + 30000000.})
    return pd.concat([etf, index], ignore_index=True)


class Session:
    def __init__(self, missing=(), duplicate=False):
        self.queries, self.closed = [], False
        self.missing, self.duplicate = missing, duplicate

    def run(self, sql):
        self.queries.append(sql)
        if 'distinct TRADE_DT' in sql:
            return pd.DataFrame({'TRADE_DT': pd.to_datetime(DAYS)})
        if "'mkt_fundd'" in sql:
            return pd.DataFrame({'TICKER_SYMBOL': ['510050'] * 60, 'TRADE_DATE': pd.to_datetime(DAYS),
                                 'CLOSE_PRICE': 1.1, 'TURNOVER_VOL': 100000.,
                                 'TURNOVER_VALUE': [1e6 + i * 100 for i in range(60)]})
        if "'fund_etf_nr'" in sql:
            return pd.DataFrame({'TICKER_SYMBOL': ['510050'], 'TRADE_DATE': [pd.Timestamp(DAYS[-1])],
                                 'SHARES': [1e8], 'NAV': [1.05], 'NET_INFLOW': [0.]})
        if "'MDSnapshot'" in sql:
            day = re.search(r'MDDate=(\d{4}\.\d{2}\.\d{2})', sql).group(1).replace('.', '-')
            frame = snapshots(day, DAYS.index(day))
            if day in self.missing:
                frame = frame[frame['SecurityID'] != SYMBOL]
            if self.duplicate:
                frame = pd.concat([frame, frame.iloc[:1]], ignore_index=True)
            return frame
        raise AssertionError('Unexpected query: ' + sql)

    def close(self):
        self.closed = True


class PreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.root = Path(cls.folder.name)
        publish(cls.root / 'basket.json', {'symbols': [SYMBOL]})
        publish(cls.root / 'universe.json', {'funds': [{'symbol': SYMBOL, 'name': 'Test ETF',
                'bucket': '上证50', 'tracking_index_symbol': INDEX}]})
        publish(cls.root / 'rules.json', {'amount_window_quantile': .75})
        cls.config = {'basket_file': str(cls.root / 'basket.json'), 'universe_file': str(cls.root / 'universe.json'),
                      'burst_config_file': str(cls.root / 'rules.json'), 'prepare_tick_references': True,
                      'output': str(cls.root / '{date}' / 'monitor-preopen.json'),
                      'archives': {'market': str(cls.root / 'missing.json')}}
        cls.session = Session()
        cls.bundle = preopen.prepare(cls.session, DAY, cls.config, cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def test_target_day_is_not_required_in_snapshot_calendar_or_history(self):
        value = self.bundle
        self.assertEqual(value['as_of'], '2026-11-25')
        self.assertEqual(value['calendar'], DAYS)
        self.assertEqual(value['target_calendar_check'], 'caller_supplied')
        self.assertEqual(len([q for q in self.session.queries if "'MDSnapshot'" in q]), 60)
        self.assertTrue(all('2026.11.26' not in q for q in self.session.queries))
        self.assertEqual(value['sources']['snapshot_amount_divisor'], 10000)

    def test_existing_daily_medians_minute_p75_units_and_previous_data(self):
        baseline = self.bundle['minute_baseline']['baseline'][SYMBOL]
        self.assertEqual(baseline['daily'], [1004950., 1002950.])
        windows = {(k, i): refs for k, i, refs in baseline['windows']}
        # Twelve five-second snapshots per minute; cash increases 100+i each tick.
        refs = windows[1, SLOTS.index('10:00')]
        self.assertEqual(refs[0], [12 * (100 + 54.25), 12 * (100 + 44.25)])
        self.assertEqual(refs[1], [12 * 149.5, 12 * 129.5])
        self.assertTrue(self.bundle['coverage'][SYMBOL]['formal_ready'])
        self.assertEqual(self.bundle['previous_daily'][SYMBOL]['net_inflow'], 0.)
        self.assertEqual(self.bundle['archives']['market']['status'], 'missing')

    def test_streamed_tick_references_match_single_batch_and_existing_loader(self):
        frames = []
        for i, day in enumerate(DAYS):
            frame = snapshots(day, i)
            rows = frame[frame.SecurityID == SYMBOL]
            frames.append(pd.DataFrame({'total_turnover': rows.TotalValueTrade.to_numpy() / 10000},
                                       index=pd.DatetimeIndex(rows.MDTime)))
        expected = build_references(pd.concat(frames), DAYS, .75)
        self.assertEqual(self.bundle['tick_baseline']['references'][SYMBOL], expected)
        path = self.root / 'tick-baseline.json'
        publish(path, self.bundle['tick_baseline'])
        refs, status = load_references(path, DAY, DAYS[-1], .75)
        self.assertEqual(status, 'ready')
        self.assertEqual(refs[SYMBOL], expected)

    def test_index_return_references_match_existing_evidence_formula(self):
        frames = []
        for i, day in enumerate(DAYS):
            rows = snapshots(day, i)
            rows = rows[rows.SecurityID == INDEX]
            frame = pd.DataFrame({'close': rows.LastPx.to_numpy() / 10000},
                                 index=pd.MultiIndex.from_arrays([['000016.XSHG'] * len(rows), rows.MDTime]))
            frames.append(frame)
        expected = PushSession.index_thresholds(None, pd.concat(frames))
        actual = {(s, slot): value for s, slot, value in self.bundle['minute_baseline']['index']}
        self.assertEqual(actual, expected)

    def test_missing_history_remains_missing_and_formal_monitor_is_not_ready(self):
        config = {**self.config, 'prepare_tick_references': False}
        value = preopen.prepare(Session(missing=DAYS[-6:]), DAY, config, self.root)
        coverage = value['coverage'][SYMBOL]
        self.assertFalse(coverage['formal_ready'])
        self.assertFalse(coverage['tick_available'])
        windows = value['minute_baseline']['baseline'][SYMBOL]['windows']
        self.assertTrue(all(refs[0][0] is None for _, _, refs in windows))
        self.assertEqual(value['tick_baseline']['references'], {})

    def test_duplicate_timestamps_fail_preparation(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate DDB snapshot'):
            preopen.prepare(Session(duplicate=True), DAY, self.config, self.root)

    def test_invalid_cumulative_turnover_is_rejected(self):
        session = Session()
        run = session.run
        def reset_cash(sql):
            frame = run(sql)
            if "'MDSnapshot'" in sql:
                frame.loc[1, 'TotalValueTrade'] = 0
            return frame
        with patch.object(session, 'run', side_effect=reset_cash):
            with self.assertRaisesRegex(ValueError, 'Invalid DDB cumulative'):
                preopen.prepare(session, DAY, self.config, self.root)

    def test_previous_missing_fields_are_not_filled_from_older_rows(self):
        session = Session()
        run = session.run
        def missing_nav(sql):
            frame = run(sql)
            if "'fund_etf_nr'" in sql:
                frame.loc[0, 'NAV'] = 0
            return frame
        with patch.object(session, 'run', side_effect=missing_nav):
            _, previous = preopen.daily_inputs(session, DAYS, [SYMBOL])
        self.assertIsNone(previous[SYMBOL]['nav'])
        self.assertEqual(previous[SYMBOL]['net_inflow'], 0.)

    def test_calendar_rejects_target_day_leakage_and_short_history(self):
        with patch.object(self.session, 'run', return_value=pd.DataFrame({'TRADE_DT': pd.to_datetime([DAY])})):
            with self.assertRaisesRegex(ValueError, 'cutoff'):
                preopen.prior_calendar(self.session, DAY)
        with patch.object(self.session, 'run', return_value=pd.DataFrame({'TRADE_DT': pd.to_datetime(DAYS[1:])})):
            with self.assertRaisesRegex(ValueError, '60 observed'):
                preopen.prior_calendar(self.session, DAY)

    def test_bundle_reader_rejects_changed_quantile_day_and_truncated_references(self):
        path = self.root / 'validated.json'
        publish(path, self.bundle)
        self.assertEqual(preopen.load_preopen(path, DAY, DAYS[-1], [SYMBOL], .75)['day'], DAY)
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            preopen.load_preopen(path, DAY, DAYS[-1], [SYMBOL], .9)
        with self.assertRaisesRegex(ValueError, 'Stale'):
            preopen.load_preopen(path, '2026-11-27', DAYS[-1], [SYMBOL], .75)
        value = deepcopy(self.bundle)
        value['minute_baseline']['baseline'][SYMBOL]['windows'].pop()
        publish(path, value)
        with self.assertRaisesRegex(ValueError, 'reference layout'):
            preopen.load_preopen(path, DAY, DAYS[-1], [SYMBOL], .75)
        value = deepcopy(self.bundle)
        value['tick_baseline']['references'] = {}
        publish(path, value)
        with self.assertRaisesRegex(ValueError, 'Tick reference inventory'):
            preopen.load_preopen(path, DAY, DAYS[-1], [SYMBOL], .75)

    def test_archive_inventory_preserves_supplier_and_does_not_accept_future_data(self):
        path = self.root / 'market.json'
        publish(path, {'provider': 'RQData', 'rows': [{'date': '2026-11-20'}]})
        result = preopen.archive_status(path, DAYS[-1])
        self.assertEqual((result['provider'], result['status']), ('RQData', 'older'))
        publish(path, {'provider': 'RQData', 'rows': [{'date': DAY}]})
        self.assertEqual(preopen.archive_status(path, DAYS[-1])['status'], 'after_cutoff')
        publish(path, {'provider': 'RQData', 'rows': [{'date': '2026-09-30', 'published': DAY}]})
        self.assertEqual(preopen.archive_status(path, DAYS[-1])['status'], 'after_cutoff')

    def test_paths_do_not_depend_on_cwd_and_no_daily_factor_or_secret_is_embedded(self):
        self.assertEqual(preopen.resolve_path(self.config['output'], self.root, DAY),
                         self.root / '20261126' / 'monitor-preopen.json')
        data = json.dumps(self.bundle)
        self.assertNotIn('daily_factor', data)
        self.assertNotIn('password', data.lower())

    def test_cli_check_is_offline_and_failed_generation_preserves_previous_file(self):
        spec = importlib.util.spec_from_file_location('prepare_monitor_preopen',
                    preopen.ROOT / 'scripts/prepare-monitor-preopen.py')
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        config_path, output = self.root / 'site.json', self.root / 'command.json'
        publish(config_path, self.config)
        publish(output, self.bundle)
        args = ['prepare', '--day', DAY, '--config', str(config_path), '--output', str(output)]
        with patch('sys.argv', args + ['--check']), patch.object(script, 'connect') as connect, redirect_stdout(io.StringIO()):
            self.assertEqual(script.main(), 0)
            connect.assert_not_called()
        before = output.read_bytes()
        session = Session(duplicate=True)
        with patch('sys.argv', args), patch.object(script, 'connect', return_value=session), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                script.main()
        self.assertTrue(session.closed)
        self.assertEqual(output.read_bytes(), before)
        partial = deepcopy(self.bundle)
        partial['coverage'][SYMBOL]['formal_ready'] = False
        session = Session()
        with patch('sys.argv', args), patch.object(script, 'connect', return_value=session), \
                patch.object(script, 'prepare', return_value=partial), redirect_stdout(io.StringIO()):
            self.assertEqual(script.main(), 2)
        self.assertTrue(session.closed)


if __name__ == '__main__':
    unittest.main()
