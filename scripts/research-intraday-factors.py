"""Read-only RQData research. Writes only the selected research output directory.

Production collectors, thresholds and archives are not changed by this script.
Run fetch with the project's protected RQDATAC2_CONF environment; analyze offline.
"""
import argparse
import contextlib
from datetime import datetime
import io
import json
from pathlib import Path
import sys
import warnings

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from etf_backend.burst import BurstDay, END, make_baseline, detect
from etf_backend.model import BASKET, SLOTS, eligible, fund_flows
from etf_backend.provider import fund_history


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def read_frame(path):
    with np.load(path, allow_pickle=False) as data:
        frame = pd.DataFrame({k: data[k] for k in data.files if k != 'ts'},
                             index=pd.to_datetime(data['ts']))
    return frame.sort_index()


def store_frame(path, frame):
    if frame is None or not len(frame):
        raise ValueError('Empty provider response')
    index = pd.DatetimeIndex(frame.index.get_level_values(-1))
    if index.tz is not None:
        index = index.tz_convert('Asia/Shanghai').tz_localize(None)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, ts=index.asi8, **{str(k): frame[k].to_numpy(dtype=float) for k in frame.columns})
    return {'rows': len(frame), 'duplicates': int(index.duplicated().sum()),
            'first': str(index.min()), 'last': str(index.max()), 'fields': list(frame.columns)}


def connect():
    import rqdatac
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        rqdatac.init(connect_timeout=8, timeout=90)
    return rqdatac


def fetch(args):
    meta_path = args.out / 'fetch-metadata.json'
    old = {}
    if meta_path.exists():
        old = load(meta_path)
        if any(old.get(k) != getattr(args, k.replace('-', '_')) for k in ('start', 'end', 'warm_start')):
            raise ValueError('Use a separate output directory for a different evidence period')
    api = connect()
    calendar = [str(d) for d in api.get_trading_dates(args.warm_start, args.end)]
    save(args.out / 'calendar.json', calendar)
    codes = [s.replace('.SH', '.XSHG').replace('.SZ', '.XSHE') for s in BASKET]
    actual = [x.order_book_id for x in api.instruments(codes) if x is not None]
    if set(actual) != set(codes):
        raise ValueError('Basket contains unverified instruments')
    metadata = {'source': 'RQData get_price / rqdatac_fund', 'prepared_at': datetime.now().isoformat(),
                'timezone': 'Asia/Shanghai', 'start': args.start, 'end': args.end, 'warm_start': args.warm_start,
                'basket': BASKET, 'frames': old.get('frames', {})}
    for symbol, code in zip(BASKET, codes):
        for frequency, fields in [('1m', ['close', 'total_turnover', 'volume']), ('1d', ['close', 'total_turnover', 'volume'])]:
            path = args.out / 'raw' / frequency / (symbol + '.npz')
            if path.exists():
                print('reuse', frequency, symbol, flush=True)
                continue
            frame = api.get_price(code, start_date=args.warm_start, end_date=args.end,
                                  frequency=frequency, fields=fields, adjust_type='none')
            metadata['frames'][str(path.relative_to(args.out))] = store_frame(path, frame)
            save(args.out / 'fetch-metadata.json', metadata)
            print('fetched', frequency, symbol, len(frame), flush=True)
    fund_path = args.out / 'raw/fund-records.json'
    if not fund_path.exists():
        universe = load(ROOT / 'config/universe.json')['funds']
        records = {}
        # This overlap supplies the previous-day share baseline for the first study day.
        first = str((pd.Timestamp(args.start) - pd.Timedelta(days=14)).date())
        for i in range(0, len(universe), 40):
            batch = universe[i:i + 40]
            records.update(fund_history([f['symbol'] for f in batch], first, args.end)['funds'])
            print('fetched fund records', min(i + 40, len(universe)), '/', len(universe), flush=True)
        save(fund_path, {'universe': universe, 'records': records})
    save(args.out / 'fetch-metadata.json', metadata)


def fetch_ticks(args):
    api = connect()
    calendar = load(args.out / 'calendar.json')
    first = calendar[-args.tick_days]
    for symbol in args.tick_symbols:
        code = symbol.replace('.SH', '.XSHG').replace('.SZ', '.XSHE')
        instrument = api.instruments(code)
        if instrument is None or instrument.order_book_id != code:
            raise ValueError('Unverified tick instrument')
        path = args.out / 'raw/tick' / (symbol + '.npz')
        if path.exists():
            previous = load(path.with_suffix('.json'))
            if previous['start'] != first or previous['end'] != args.end:
                raise ValueError('Use a separate output directory for a different Tick period')
            print('reuse ticks', symbol, flush=True)
            continue
        frame = api.get_price(code, start_date=first, end_date=args.end, frequency='tick',
                              fields=['last', 'total_turnover', 'volume'], adjust_type='none')
        meta = store_frame(path, frame)
        save(path.with_suffix('.json'), {**meta, 'source': 'RQData historical L1 snapshots',
                                        'received_timestamps_available': False, 'start': first, 'end': args.end})
        print('fetched tick', symbol, meta, flush=True)


def window_union(events, confirmed_only=True):
    """Amount accounting uses the union of qualifying windows, never peak sums."""
    result = set()
    for event in events:
        for signal in event['updates']:
            if not confirmed_only or signal['level'] >= 2:
                result.update(range(SLOTS.index(signal['start']), SLOTS.index(signal['end']) + 1))
    return sorted(result)


def ratio(numerator, denominator):
    if numerator is None or denominator is None or not np.isfinite(numerator) or not np.isfinite(denominator) or denominator <= 0:
        return None
    return float(numerator / denominator)


def market_data(out):
    minutes, daily, quality = {}, {}, []
    for symbol in BASKET:
        frame = read_frame(out / 'raw/1m' / (symbol + '.npz'))
        if frame.index.duplicated().any():
            raise ValueError('Duplicate minute keys')
        daily_frame = read_frame(out / 'raw/1d' / (symbol + '.npz'))
        daily_frame.index = daily_frame.index.strftime('%Y-%m-%d')
        for day, rows in frame.groupby(frame.index.strftime('%Y-%m-%d')):
            slots = dict(zip(rows.index.strftime('%H:%M'), rows.total_turnover))
            values = [float(slots[s]) if s in slots and np.isfinite(slots[s]) else None for s in SLOTS]
            minutes.setdefault(day, {})[symbol] = values
            valid = all(x is not None and x >= 0 for x in values)
            amount = float(daily_frame.loc[day, 'total_turnover']) if day in daily_frame.index else None
            daily.setdefault(day, {})[symbol] = amount
            discrepancy = abs(sum(values) - amount) if valid and amount is not None else None
            quality.append({'symbol': symbol, 'date': day, 'valid_minutes': sum(x is not None for x in values),
                            'daily_amount': amount, 'difference_cny': discrepancy,
                            'complete': valid and discrepancy is not None and discrepancy <= max(2., amount * 1e-8)})
    return minutes, daily, quality


def analyze(args):
    calendar = load(args.out / 'calendar.json')
    days = [d for d in calendar if args.start <= d <= args.end]
    minutes, daily, quality = market_data(args.out)
    funds = load(args.out / 'raw/fund-records.json')
    metadata = {f['symbol']: f for f in funds['universe']}
    flows = {s: fund_flows(f, funds['records'][s], calendar) for s, f in metadata.items()}
    day_rows, event_rows, rings = [], [], []
    profiles = []
    for number, day in enumerate(days):
        baseline = make_baseline(calendar, minutes, daily, day, .75)
        detector = BurstDay(day, baseline, .75, replay=True, baseline_as_of=max(d for d in calendar if d < day))
        for i, slot in enumerate(SLOTS[:END + 1]):
            for symbol in BASKET:
                value = minutes.get(day, {}).get(symbol, [None] * 240)[i]
                detector.add(symbol, slot, value)
        report = detector.payload()
        save(args.out / 'replay' / (day + '.json'), report)
        by_symbol = {s: [e for e in report['events'] if e['symbol'] == s] for s in BASKET}
        gross, excess, baseline_cash, live_cash = 0., 0., 0., 0.
        for symbol in BASKET:
            values = np.array([np.nan if x is None else x for x in minutes.get(day, {}).get(symbol, [None] * 240)])
            events = by_symbol[symbol]
            union = window_union(events)
            refs = np.array([max(baseline[symbol][1].get((1, i), [[None, None]])[0])
                             if all(x is not None for x in baseline[symbol][1].get((1, i), [[None, None]])[0])
                             else np.nan for i in range(240)])
            cash = float(values[union].sum()) if union else 0.
            base = float(refs[union].sum()) if union and np.isfinite(refs[union]).all() else (0. if not union else None)
            positive_excess = float(np.maximum(values[union] - refs[union], 0).sum()) if base is not None else None
            ends = {SLOTS.index(s['end']) for e in events for s in e['updates'] if s['level'] >= 2}
            after = float(values[sorted(ends)].sum()) if ends else 0.
            gross += cash
            live_cash += after
            if base is not None:
                excess += positive_excess
                baseline_cash += base
            record = flows[symbol].get(day, {})
            event_rows.append({'date': day, 'symbol': symbol, 'bucket': metadata[symbol]['bucket'],
                               'events': len(events), 'confirmed_events': sum(e['level'] >= 2 for e in events),
                               'confirmed_union_minutes': len(union), 'gross_cny': cash,
                               'historical_p75_reference_cny': base, 'positive_excess_cny': positive_excess,
                               'confirmation_end_minutes_cny': after, 'netflow_yi': record.get('flow')})
            if np.isfinite(values).all():
                profiles.append([symbol, day, float(values[:30].sum()), float(values[30:90].sum()),
                                 float(values[90:120].sum()), float(values[120:180].sum()), float(values[180:237].sum())])
            # Sequential comparison is restricted to a continuous session and non-overlapping prior 20m.
            for i in list(range(20, 120)) + list(range(140, 237)):
                prior, current = values[i - 20:i], values[i]
                ref_prior, ref_current = refs[i - 20:i], refs[i]
                raw = ratio(current, np.mean(prior)) if np.isfinite(prior).all() else None
                normalized = ratio(ratio(current, ref_current), ratio(np.sum(prior), np.sum(ref_prior))) if np.isfinite(ref_prior).all() and np.isfinite(prior).all() else None
                rings.append({'symbol': symbol, 'date': day, 'slot': SLOTS[i], 'raw': raw, 'season_adjusted': normalized})
        eligible_symbols = [s for s, f in metadata.items() if eligible(f) <= day and
                            (not f.get('delisted_date') or day < f['delisted_date'])]
        core_values = [flows[s].get(day, {}).get('flow') for s in BASKET]
        broad_values = [flows[s].get(day, {}).get('flow') for s in eligible_symbols]
        core = sum(core_values) if all(v is not None for v in core_values) else None
        broad = sum(broad_values) if all(v is not None for v in broad_values) else None
        day_rows.append({'date': day, 'confirmed_union_turnover_yi': gross / 1e8,
                         'positive_excess_yi': excess / 1e8, 'historical_p75_reference_yi': baseline_cash / 1e8,
                         'confirmation_end_turnover_yi': live_cash / 1e8,
                         'core_netflow_yi': core, 'broad_netflow_yi': broad,
                         'core_netflow_to_anomaly_turnover': ratio(core, gross / 1e8),
                         'core_flow_valid': sum(v is not None for v in core_values), 'core_flow_expected': len(BASKET),
                         'broad_flow_valid': sum(v is not None for v in broad_values), 'broad_flow_expected': len(eligible_symbols),
                         'events': len(report['events']), 'confirmed_events': sum(e['level'] >= 2 for e in report['events'])})
        if number % 5 == 0:
            print('analyzed', day, number + 1, '/', len(days), flush=True)
    save(args.out / 'daily-comparison.json', day_rows)
    save(args.out / 'symbol-daily-comparison.json', event_rows)
    save(args.out / 'quality.json', quality)
    save(args.out / 'ring-comparison.json', rings)
    save(args.out / 'intraday-profile.json', profiles)
    print('analysis complete', len(days), 'days', flush=True)


GRID = np.r_[np.arange(9 * 3600 + 30 * 60 + 5, 11 * 3600 + 30 * 60 + 1, 5),
             np.arange(13 * 3600 + 5, 14 * 3600 + 57 * 60 + 1, 5)]
MINUTE_ENDS = np.array([int(s[:2]) * 3600 + int(s[3:]) * 60 for s in SLOTS])


def rolling_sum(values, length):
    one = np.ndim(values) == 1
    values = np.atleast_2d(values)
    sums = np.c_[np.zeros(len(values)), np.cumsum(np.nan_to_num(values), axis=1)]
    missing = np.c_[np.zeros(len(values)), np.cumsum(~np.isfinite(values), axis=1)]
    result = np.full(values.shape, np.nan)
    result[:, length - 1:] = sums[:, length:] - sums[:, :-length]
    invalid = missing[:, length:] - missing[:, :-length] > 0
    result[:, length - 1:][invalid] = np.nan
    result[:, 1440:1440 + length - 1] = np.nan  # no lunch bridge
    return result[0] if one else result


def resample_day(frame, day):
    """Use left limits for provider-aligned research; retain right limits for audit.

    Historical snapshots have no reception times; these are market-time features.
    Missing fresh endpoints stay missing, including the afternoon opening boundary.
    """
    frame = frame[~frame.index.duplicated(keep='last')]
    stamps = frame.index.asi8
    base = pd.Timestamp(day).value
    cash = frame.total_turnover.to_numpy(float)
    def lookup(seconds, side='left'):
        target = base + np.array(seconds, dtype=np.int64) * 1_000_000_000
        idx = np.searchsorted(stamps, target, side=side) - 1
        safe = np.maximum(idx, 0)
        valid = (idx >= 0) & ((target - stamps[safe]) <= 6_000_000_000)
        return np.where(valid, cash[safe], np.nan)
    end, start = lookup(GRID), lookup(GRID - 5)
    amount = end - start
    amount[amount < 0] = np.nan
    minute_indices = np.searchsorted(MINUTE_ENDS, GRID, side='left')
    before = lookup(MINUTE_ENDS[minute_indices] - 60)
    before[minute_indices == 0] = 0
    partial = end - before
    partial[partial < 0] = np.nan
    minute_total = lookup(MINUTE_ENDS[:237]) - lookup(np.r_[9 * 3600 + 30 * 60, MINUTE_ENDS[1:237] - 60])
    first = lookup([MINUTE_ENDS[0]])[0]
    minute_total[0] = first
    right_total = lookup(MINUTE_ENDS[:237], 'right') - lookup(
        np.r_[9 * 3600 + 30 * 60, MINUTE_ENDS[1:237] - 60], 'right')
    right_total[0] = lookup([MINUTE_ENDS[0]], 'right')[0]
    seconds = (stamps - base) / 1e9
    phase = np.where(seconds < 12 * 3600, 0, 1)
    diff = np.diff(seconds)
    intraday = (seconds[1:] >= 9.5 * 3600) & (seconds[1:] <= 14.95 * 3600) & (phase[1:] == phase[:-1])
    intervals = diff[intraday]
    return amount, partial, minute_total, {
        'ticks': len(frame), 'valid_5s_buckets': int(np.isfinite(amount).sum()),
        'expected_5s_buckets': len(GRID), 'cumulative_regressions': int((np.diff(cash) < -1e-6).sum()),
        'interval_median_seconds': float(np.median(intervals)) if len(intervals) else None,
        'gaps_gt_10s': int((intervals > 10).sum()),
    }, right_total


def historical_p75(matrix):
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        recent = np.nanquantile(matrix[-20:], .75, axis=0)
        long = np.nanquantile(matrix[-60:], .75, axis=0)
    values = np.maximum(recent, long)
    values[(np.isfinite(matrix[-20:]).sum(axis=0) < 19) | (np.isfinite(matrix[-60:]).sum(axis=0) < 55) | (values <= 0)] = np.nan
    return values


def episode_starts(mask):
    indices = np.flatnonzero(mask)
    if not len(indices):
        return []
    return [int(indices[0])] + [int(right) for left, right in zip(indices[:-1], indices[1:])
                              if GRID[right] - GRID[left] > 30]


def analyze_ticks(args):
    minutes, daily, _ = market_data(args.out)
    calendar = load(args.out / 'calendar.json')
    rows, matches, quality = [], [], []
    for symbol in args.tick_symbols:
        frame = read_frame(args.out / 'raw/tick' / (symbol + '.npz'))
        groups = {str(day): values for day, values in frame.groupby(frame.index.strftime('%Y-%m-%d'))}
        days = sorted(groups)
        if len(days) <= 60:
            raise ValueError('Tick study needs 60 baseline days followed by evaluation days')
        sampled = {day: resample_day(groups[day], day) for day in days}
        matrix = np.array([sampled[d][0] for d in days])
        sums30 = rolling_sum(matrix, 6)
        sums60 = rolling_sum(matrix, 12)
        for offset in range(60, len(days)):
            day = days[offset]
            p5 = historical_p75(matrix[offset - 60:offset])
            p30 = historical_p75(sums30[offset - 60:offset])
            p60 = historical_p75(sums60[offset - 60:offset])
            day_history = [daily[d][symbol] for d in calendar if d < day and d in daily and symbol in daily[d]][-60:]
            day_median = max(np.median(day_history[-20:]), np.median(day_history))
            a5, a30, a60 = matrix[offset], sums30[offset], sums60[offset]
            z5, z30, z60 = a5 / p5, a30 / p30, a60 / p60
            hits = np.where(np.isfinite(z5), (z5 >= 3).astype(float), np.nan)
            occupancy30, occupancy60 = rolling_sum(hits, 6) / 6, rolling_sum(hits, 12) / 12
            masks = {
                '30s_R5_share1pct': (z30 >= 5) & (a30 / day_median >= .01),
                '30s_R5_persist4of6_share1pct': (z30 >= 5) & (occupancy30 >= 4 / 6) & (a30 / day_median >= .01),
                '30s_R3_persist4of6_share1pct': (z30 >= 3) & (occupancy30 >= 4 / 6) & (a30 / day_median >= .01),
                '60s_R3_persist9of12_share2pct': (z60 >= 3) & (occupancy60 >= 9 / 12) & (a60 / day_median >= .02),
            }
            baseline = make_baseline(calendar, minutes, daily, day, .75, symbols=[symbol])
            actual = list(minutes[day][symbol])
            old_candidate = np.zeros(len(GRID), dtype=bool)
            for j, second in enumerate(GRID):
                index = int(np.searchsorted(MINUTE_ENDS, second, side='left'))
                if index > END or not np.isfinite(sampled[day][1][j]):
                    continue
                values = actual[:]
                values[index] = float(sampled[day][1][j])
                old_candidate[j] = detect(values, index, baseline[symbol]) is not None
            masks['existing_aligned_tick_candidate'] = old_candidate
            report = load(args.out / 'replay' / (day + '.json'))
            events = [e for e in report['events'] if e['symbol'] == symbol and e['level'] >= 2]
            reference_minutes = np.array(minutes[day][symbol][:237], dtype=float)
            valid = np.isfinite(sampled[day][2]) & np.isfinite(reference_minutes)
            errors = np.abs(sampled[day][2][valid] - reference_minutes[valid])
            right_valid = np.isfinite(sampled[day][4]) & np.isfinite(reference_minutes)
            right_errors = np.abs(sampled[day][4][right_valid] - reference_minutes[right_valid])
            quality.append({'symbol': symbol, 'date': day, **sampled[day][3],
                            'minute_pairs': int(valid.sum()), 'minute_pairs_within_2yuan': int((errors <= 2).sum()),
                            'minute_max_difference_cny': float(errors.max()) if len(errors) else None,
                            'right_closed_minute_pairs': int(right_valid.sum()),
                            'right_closed_pairs_within_2yuan': int((right_errors <= 2).sum()),
                            'baseline_5s_valid': int(np.isfinite(p5).sum())})
            for method, mask in masks.items():
                starts = episode_starts(mask)
                rows.append({'symbol': symbol, 'date': day, 'method': method, 'episodes': len(starts),
                             'confirmed_reference_events': len(events), 'trigger_grid_points': int(mask.sum())})
                for event in events:
                    confirmations = [s for s in event['updates'] if s['level'] >= 2]
                    first = confirmations[0]
                    start = MINUTE_ENDS[SLOTS.index(first['start'])] - 60
                    confirm = MINUTE_ENDS[SLOTS.index(first['end'])]
                    early = np.flatnonzero(mask & (GRID >= start) & (GRID <= confirm))
                    matches.append({'symbol': symbol, 'date': day, 'event_id': event['id'], 'method': method,
                                    'confirmation': first['end'], 'early_match': bool(len(early)),
                                    'market_time_lead_seconds': float(confirm - GRID[early[0]]) if len(early) else None})
            features = args.out / 'tick-features' / (symbol + '-' + day + '.npz')
            features.parent.mkdir(exist_ok=True)
            np.savez_compressed(features, seconds=GRID, amount5=a5, amount30=a30, z5=z5, z30=z30,
                                persistence30=occupancy30, day_share30=a30 / day_median, old_candidate=old_candidate)
            if offset % 5 == 0:
                print('tick study', symbol, day, flush=True)
    save(args.out / 'tick-trial-daily.json', rows)
    save(args.out / 'tick-trial-matches.json', matches)
    save(args.out / 'tick-quality.json', quality)
    print('tick study complete', len(quality), 'symbol-days', flush=True)


def summarize(args):
    """Aggregate only complete paired days; keep the underlying audit files."""
    days = pd.DataFrame(load(args.out / 'daily-comparison.json'))
    quality = pd.DataFrame(load(args.out / 'quality.json'))
    paired = days.dropna(subset=['core_netflow_yi'])
    rings = pd.DataFrame(load(args.out / 'ring-comparison.json'))
    profile = pd.DataFrame(load(args.out / 'intraday-profile.json'),
                           columns=['symbol', 'date', 'open', 'midam', 'lateam', 'earlypm', 'latepm'])
    intensities = profile.iloc[:, 2:].div([30, 60, 30, 60, 57])
    trials = pd.DataFrame(load(args.out / 'tick-trial-daily.json'))
    matches = pd.DataFrame(load(args.out / 'tick-trial-matches.json'))
    tick_quality = pd.DataFrame(load(args.out / 'tick-quality.json'))
    events, naive, peaks = [], 0., 0.
    for day in days.date:
        for event in load(args.out / 'replay' / (day + '.json'))['events']:
            confirmations = [u for u in event['updates'] if u['level'] >= 2]
            if confirmations:
                events.append({'date': day, 'symbol': event['symbol'], 'confirmation': confirmations[0]['end']})
                naive += sum(u['amount'] for u in confirmations)
                peaks += event['peak']['amount']
    method_rows = []
    dates = sorted(trials.date.unique())
    for period, selected in [('all', dates), ('first10', dates[:10]), ('last10', dates[10:])]:
        for method in sorted(trials.method.unique()):
            daily_rows = trials[(trials.method == method) & trials.date.isin(selected)]
            event_rows = matches[(matches.method == method) & matches.date.isin(selected)]
            leads = event_rows.market_time_lead_seconds.dropna()
            method_rows.append({'period': period, 'method': method, 'episodes': int(daily_rows.episodes.sum()),
                                'references': len(event_rows), 'early_matches': int(event_rows.early_match.sum()),
                                'median_lead_seconds': float(leads.median()) if len(leads) else None})
    ring_rows = []
    for begin, end in [('09:51', '10:00'), ('10:01', '11:00'), ('11:01', '11:30'),
                       ('13:21', '14:00'), ('14:01', '14:57')]:
        selected = rings[(rings.slot >= begin) & (rings.slot <= end)]
        ring_rows.append({'start': begin, 'end': end, 'rows': len(selected),
                          'raw_ge3_fraction': float((selected.raw >= 3).mean()),
                          'adjusted_ge3_fraction': float((selected.season_adjusted >= 3).mean())})
    confirmations = pd.DataFrame(events)
    time_rows = []
    for begin, end in [('09:31', '10:00'), ('10:01', '11:30'), ('13:01', '14:00'),
                       ('14:01', '14:30'), ('14:31', '14:57')]:
        time_rows.append({'start': begin, 'end': end,
                          'events': int(((confirmations.confirmation >= begin) & (confirmations.confirmation <= end)).sum())})
    save(args.out / 'summary.json', {
        'study_days': len(days), 'paired_days': len(paired), 'minute_symbol_days': len(quality),
        'complete_minute_symbol_days': int(quality.complete.sum()),
        'max_minute_daily_difference_cny': float(quality.difference_cny.max()),
        'all_days_anomaly_turnover_yi': float(days.confirmed_union_turnover_yi.sum()),
        'paired_anomaly_turnover_yi': float(paired.confirmed_union_turnover_yi.sum()),
        'paired_netflow_yi': float(paired.core_netflow_yi.sum()),
        'naive_overlapping_window_sum_yi': naive / 1e8, 'peak_window_sum_yi': peaks / 1e8,
        'intraday_relative_intensity_median': intensities.div(intensities.midam, axis=0).median().to_dict(),
        'ring': ring_rows, 'event_confirmation_time': time_rows, 'tick_methods': method_rows,
        'tick_quality': tick_quality.groupby('symbol').sum(numeric_only=True).to_dict(orient='index'),
    })
    save(args.out / 'event-first-confirmation.json', events)
    print('saved', args.out / 'summary.json')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['fetch', 'analyze', 'fetch-ticks', 'analyze-ticks', 'summarize'])
    parser.add_argument('--out', type=Path, default=ROOT / '.local/intraday-research-20261121')
    parser.add_argument('--start', default='2026-09-21')
    parser.add_argument('--end', default='2026-11-20')
    parser.add_argument('--warm-start', default='2026-06-01')
    parser.add_argument('--tick-days', type=int, default=80)
    parser.add_argument('--tick-symbols', nargs='+', default=['510300.SH', '588000.SH', '159915.SZ'])
    args = parser.parse_args()
    {'fetch': fetch, 'analyze': analyze, 'fetch-ticks': fetch_ticks,
     'analyze-ticks': analyze_ticks, 'summarize': summarize}[args.mode](args)


if __name__ == '__main__':
    main()
