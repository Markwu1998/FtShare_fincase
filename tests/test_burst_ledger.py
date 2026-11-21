from datetime import datetime, timedelta
import importlib.util
from pathlib import Path
import tempfile
import unittest

from etf_backend.burst import BurstDay
from etf_backend.burst_ledger import BurstLedger, read_journal
from etf_backend.provider import CHINA
from etf_backend.tick_minutes import TickMinutes
from etf_backend.tick_factors import candidates, load_references

DAY, SYMBOL = '2026-11-20', '159915.SZ'


def clock(value):
    return datetime.fromisoformat(DAY + 'T' + value).replace(tzinfo=CHINA)


class LedgerTest(unittest.TestCase):
    def test_summary_counts_first_confirmation_once_and_keeps_recovery_label(self):
        spec = importlib.util.spec_from_file_location('journal_summary',
            Path(__file__).resolve().parents[1] / 'scripts/summarize-burst-journal.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'events.sqlite3'
            ledger = BurstLedger(path, {'day': DAY})
            for slot, level in [('09:34', 1), ('09:35', 2), ('09:36', 3)]:
                ledger.append('formal', SYMBOL, DAY + 'T' + slot + ':00+08:00', 'received',
                              {'event_id': 'same-event', 'signal': {'level': level}, 'recovered': True})
            ledger.close()
            report = module.summarize(path)
            self.assertEqual(report['confirmed_events'], 1)
            self.assertEqual(report['first_confirmation_10m_bins'], {'09:30': 1})
            self.assertEqual(report['recovered_events'], 1)

    def test_restart_dedup_pagination_and_immediate_windows_file_release(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'journal.sqlite3'
            for detected in ['first', 'restart']:
                ledger = BurstLedger(path, {'day': DAY})
                ledger.append('formal', SYMBOL, '09:35', 'received', {'signal': 2, 'detected_at': detected})
                ledger.append('candidate', SYMBOL, '09:34', 'received', {'amount': 100})
                ledger.close()
            rows = read_journal(path)
            self.assertEqual(len(rows), 3)
            self.assertEqual(len(read_journal(path, symbol=SYMBOL, kind='formal')), 1)
            self.assertEqual(read_journal(path, after=rows[0]['seq'], limit=1)[0]['seq'], rows[1]['seq'])
            path.unlink()  # catches open SQLite handles on Windows

    def test_every_formal_hit_is_observed_but_union_amount_counts_each_minute_once(self):
        refs = {(k, i): ([1, 1], [1, 1]) for k in (1, 2, 5, 10) for i in range(240)}
        detector = BurstDay(DAY, {SYMBOL: ([1000, 1000], refs)}, .75, symbols=[SYMBOL])
        calls = []
        detector.on_signal = lambda *args: calls.append(args)
        for i, value in enumerate([1, 1, 1, 1, 120, 120]):
            detector.add(SYMBOL, f'09:{31 + i:02d}', value)
        result = detector.accounting()
        self.assertEqual(result['gross'], 244)
        self.assertEqual(result['covered_minutes'], 6)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(detector.events[0]['updates']), 1)  # existing alert semantics
        detector.add(SYMBOL, '09:36', 999999)
        self.assertEqual(detector.accounting(), result)


class BoundaryV2Test(unittest.TestCase):
    def test_exact_minute_tick_is_next_minute_and_accepts_after_seal(self):
        agg = TickMinutes(DAY, [SYMBOL], [SYMBOL])
        agg.add(SYMBOL, clock('09:30:57'), 1, 100)
        agg.add(SYMBOL, clock('09:31:00'), 1, 110)
        self.assertEqual(agg.advance(clock('09:31:02'))[0]['amount'], 100)
        agg.add(SYMBOL, clock('09:31:57'), 1, 150)
        self.assertEqual(agg.advance(clock('09:32:02'))[0]['amount'], 50)

    def test_closing_endpoint_and_afternoon_anchor_include_final_morning_snapshot(self):
        agg = TickMinutes(DAY, [SYMBOL], [SYMBOL])
        agg.add(SYMBOL, clock('11:28:57'), 1, 80)
        agg.add(SYMBOL, clock('11:29:57'), 1, 100)
        agg.advance(clock('11:30:02'))
        agg.add(SYMBOL, clock('11:30:03'), 1, 110)
        closing = agg.advance(clock('11:30:06'))[0]
        self.assertEqual(closing['amount'], 30)
        # A later lunch heartbeat must not hide the valid finalized AM anchor.
        agg.add(SYMBOL, clock('11:30:09'), 1, 110)
        agg.add(SYMBOL, clock('13:00:57'), 1, 140)
        self.assertEqual(agg.advance(clock('13:01:02'))[0]['amount'], 30)
        self.assertEqual(agg.advance(clock('13:01:03')), [])

    def test_new_mode_rejects_retroactive_pre_boundary_tick(self):
        agg = TickMinutes(DAY, [SYMBOL], [SYMBOL])
        agg.add(SYMBOL, clock('09:30:57'), 1, 100)
        agg.advance(clock('09:31:02'))
        self.assertTrue(agg.add(SYMBOL, clock('09:31:00'), 1, 110))
        self.assertFalse(agg.add(SYMBOL, clock('09:30:59'), 1, 105))


class FactorTest(unittest.TestCase):
    def setup_ticks(self):
        agg = TickMinutes(DAY, [SYMBOL], [SYMBOL])
        opening = clock('09:30:00')
        refs = {'5': {}, '30': {}, '60': {}}
        for i in range(13):
            t = opening + timedelta(seconds=i * 5)
            agg.add(SYMBOL, t - timedelta(milliseconds=1), 1, i * 10)
            for length in refs:
                refs[length][t.strftime('%H:%M:%S')] = int(length) / 5
        return agg, {SYMBOL: refs}, {SYMBOL: ([1000, 1000], {})}

    def test_subminute_persistence_is_provisional_and_does_not_mutate_formal_state(self):
        agg, refs, baseline = self.setup_ticks()
        output = candidates(agg, baseline, refs, SYMBOL, clock('09:31:01'))
        self.assertEqual([v['window_seconds'] for v in output], [30, 60])
        self.assertEqual(output[0]['amount'], 60)
        self.assertEqual(output[0]['persistence'], 1)
        self.assertTrue(all(v['provisional'] for v in output))
        self.assertEqual(candidates(agg, baseline, {}, SYMBOL, clock('09:31:01')), [])
        self.assertEqual(candidates(agg, baseline, refs, SYMBOL, clock('12:01:00')), [])

    def test_missing_history_does_not_fall_back_to_minute_references(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'missing.json'
            self.assertEqual(load_references(path, DAY, '2026-11-19', .75), ({}, 'missing'))
            path.write_text('{"day":"2026-11-21"}', encoding='utf-8')
            self.assertEqual(load_references(path, DAY, '2026-11-19', .75), ({}, 'stale_or_incompatible'))
            path.write_text('[]', encoding='utf-8')
            self.assertEqual(load_references(path, DAY, '2026-11-19', .75), ({}, 'invalid'))


if __name__ == '__main__':
    unittest.main()
