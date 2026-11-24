"""Offline validation with real historical Tick and explicitly simulated reception.

Requires the local licensed research cache, never connects to a market provider.
Market time + 100ms is a scenario, not measured live network latency.
"""
import argparse
from datetime import datetime, timedelta
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from etf_backend.burst import BurstDay, make_baseline, replay_day
from etf_backend.provider import CHINA
from etf_backend.tick_minutes import TickMinutes, replay_tick_day


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input-dir', type=Path, default=ROOT / '.local/intraday-research-20261121')
    parser.add_argument('--out', type=Path, default=ROOT / '.local/tick-upgrade/validation.json')
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('research', ROOT / 'scripts/research-intraday-factors.py')
    research = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(research)
    minutes, daily, _ = research.market_data(args.input_dir)
    calendar = research.load(args.input_dir / 'calendar.json')
    results, factor_results = [], []
    for symbol in ['510300.SH', '588000.SH', '159915.SZ']:
        frame = research.read_frame(args.input_dir / 'raw/tick' / (symbol + '.npz'))
        groups = list(frame.groupby(frame.index.strftime('%Y-%m-%d')))[-20:]
        for day, ticks in groups:
            baseline = make_baseline(calendar, minutes, daily, day, .75, [symbol])
            expected = BurstDay(day, baseline, .75, replay=True, symbols=[symbol])
            for slot, amount in zip(research.SLOTS, minutes[day][symbol]):
                expected.add(symbol, slot, amount)
            agg = TickMinutes(day, [symbol], [symbol])
            actual = BurstDay(day, baseline, .75, replay=True, symbols=[symbol])
            minute_rows = {}
            def advance(now):
                for row in agg.advance(now):
                    slot = row['end'].strftime('%H:%M')
                    minute_rows[slot] = row
                    if row['valid']:
                        actual.add(symbol, slot, row['amount'])
            for stamp, price, cash in zip(ticks.index, ticks['last'], ticks.total_turnover):
                market = stamp.to_pydatetime().replace(tzinfo=CHINA)
                advance(market + timedelta(milliseconds=100))
                agg.add(symbol, market, float(price), float(cash))
            advance(datetime.fromisoformat(day + 'T15:00:06').replace(tzinfo=CHINA))
            matched, missing, differences = 0, [], []
            for i, slot in enumerate(research.SLOTS[:237]):
                row = minute_rows[slot]
                if not row['valid']:
                    missing.append(slot)
                elif abs(row['amount'] - minutes[day][symbol][i]) <= 2:
                    matched += 1
                else:
                    differences.append({'slot': slot, 'tick': row['amount'], 'bar': minutes[day][symbol][i]})
            signature = lambda detector: sorted((e['id'], e['level'], e['peak']['end']) for e in detector.events)
            results.append({'date': day, 'symbol': symbol, 'matched': matched, 'missing': missing,
                            'differences': differences, 'event_signatures_equal': signature(actual) == signature(expected),
                            'tick_events': len(actual.events), 'bar_events': len(expected.events),
                            'union_difference_cny': actual.accounting()['gross'] - expected.accounting()['gross']})
            print('validated', symbol, day, matched, '/237', flush=True)
        day, ticks = groups[-1]
        baseline_path = ROOT / '.local/tick-upgrade/baselines' / (day + '.json')
        references = research.load(baseline_path)['references']
        records = ([symbol, stamp.to_pydatetime().replace(tzinfo=CHINA).isoformat(),
                    (stamp.to_pydatetime().replace(tzinfo=CHINA) + timedelta(milliseconds=100)).isoformat(),
                    float(price), float(cash)] for stamp, price, cash in zip(ticks.index, ticks['last'], ticks.total_turnover))
        report = replay_tick_day(day, baseline, .75, records, [symbol], calendar[calendar.index(day) - 1],
                                 boundary_mode='provider-v2', factor_references=references)
        research.save(args.out.parent / ('tick-replay-' + symbol + '.json'), report)
        factor_results.append({'symbol': symbol, 'date': day, 'candidates': len(report['tick_candidate_history']),
                               'formal_events': len(report['events'])})
    invariance = []
    for day in ['2026-10-22', '2026-11-04', '2026-11-20']:
        old = research.load(args.input_dir / 'replay' / (day + '.json'))
        new = replay_day(day, calendar, minutes, daily, .75)
        invariance.append({'date': day, 'events_equal': old['events'] == new['events'],
                           'union_matches_research': abs(new['accounting']['gross'] - next(
                               row['confirmed_union_turnover_yi'] * 1e8 for row in research.load(
                                   args.input_dir / 'daily-comparison.json') if row['date'] == day)) <= 2})
    summary = {'source': 'actual RQData historical snapshots', 'reception': 'simulated market + 100ms',
               'boundary_mode': 'provider-v2', 'symbol_days': len(results),
               'matched_minutes': sum(r['matched'] for r in results), 'expected_minutes': len(results) * 237,
               'missing_minutes': sum(len(r['missing']) for r in results),
               'mismatched_minutes': sum(len(r['differences']) for r in results),
               'equal_event_symbol_days': sum(r['event_signatures_equal'] for r in results),
               'minute_rule_invariance': invariance, 'factor_replay': factor_results, 'details': results}
    research.save(args.out, summary)
    if not all(row['events_equal'] and row['union_matches_research'] for row in invariance):
        raise AssertionError('Existing minute rule or turnover accounting changed')
    print('saved', args.out, flush=True)


if __name__ == '__main__':
    main()
