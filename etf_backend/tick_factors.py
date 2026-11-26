"""Provisional 30/60-second persistence signals; never confirm formal events."""
from bisect import bisect_left
from datetime import timedelta
import json
from pathlib import Path

from .model import finite

FACTOR_VERSION = 'tick-persistence-v1'
REFERENCE_SECONDS = (*range(34205, 41401, 5), *range(46805, 53821, 5))


def load_references(path, day, as_of, q):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}, 'missing'
    except (OSError, ValueError):
        return {}, 'invalid'
    if not isinstance(value, dict):
        return {}, 'invalid'
    if (value.get('day'), value.get('as_of'), value.get('quantile'), value.get('version')) != (
            day, as_of, q, FACTOR_VERSION) or not as_of or as_of >= day:
        return {}, 'stale_or_incompatible'
    if not isinstance(value.get('references'), dict):
        return {}, 'invalid'
    if any(not isinstance(refs, dict) or any(not isinstance(refs.get(k), dict) for k in ('5', '30', '60'))
           for refs in value['references'].values()):
        return {}, 'invalid'
    return value['references'], 'ready'


def candidates(aggregator, baseline, references, symbol, now):
    if symbol not in references or symbol not in baseline:
        return []
    second = now.hour * 3600 + now.minute * 60 + now.second
    end = second // 5 * 5
    opening = 9 * 3600 + 30 * 60 if second < 12 * 3600 else 13 * 3600
    closing = 11 * 3600 + 30 * 60 if second < 12 * 3600 else 14 * 3600 + 57 * 60
    if not opening + 30 <= second <= closing:
        return []
    anchor = now.replace(hour=0, minute=0, second=0, microsecond=0)
    times, rows = aggregator.times[symbol], aggregator.rows[symbol]
    totals = {}
    for t in range(max(opening, end - 60), end + 1, 5):
        stamp = anchor + timedelta(seconds=t)
        index = bisect_left(times, stamp) - 1
        if index >= 0 and stamp - times[index] <= timedelta(seconds=6):
            totals[t] = rows[index][2]
    refs = references[symbol]
    daily = baseline[symbol][0]
    if not all(finite(v) and v > 0 for v in daily):
        return []
    denominator = max(daily)
    result = []
    for length, multiple, occupancy, share in [(30, 5, 4 / 6, .01), (60, 3, 9 / 12, .02)]:
        start = end - length
        if start < opening:
            continue
        last_minute = bisect_left(aggregator.ends, anchor + timedelta(seconds=end))
        if any((symbol, i) in aggregator.bad for i in range(max(0, last_minute - 1), last_minute + 1)):
            continue
        deltas, ratios = [], []
        for t in range(start + 5, end + 1, 5):
            before, after = totals.get(t - 5), totals.get(t)
            slot = (anchor + timedelta(seconds=t)).strftime('%H:%M:%S')
            reference = refs.get('5', {}).get(slot)
            if not all(finite(v) for v in [before, after, reference]) or after < before or reference <= 0:
                break
            deltas.append(after - before)
            ratios.append((after - before) / reference)
        if len(deltas) != length // 5:
            continue
        slot = (anchor + timedelta(seconds=end)).strftime('%H:%M:%S')
        reference = refs.get(str(length), {}).get(slot)
        if not finite(reference) or reference <= 0:
            continue
        amount = sum(deltas)
        persistence = sum(v >= 3 for v in ratios) / len(ratios)
        if amount / reference >= multiple and persistence >= occupancy and amount / denominator >= share:
            result.append({'symbol': symbol, 'method': f'{length}s-persistence', 'provisional': True,
                           'as_of': (anchor + timedelta(seconds=end)).isoformat(),
                           'observed_at': now.isoformat(), 'rule_version': FACTOR_VERSION,
                           'window_seconds': length, 'amount': amount, 'ratio': amount / reference,
                           'daily_share': amount / denominator, 'persistence': persistence})
    return result


def build_references(frame, calendar, q):
    """Offline preparation from 60 prior trading days of actual historical Tick."""
    if len(calendar) != 60 or not finite(q) or not 0 < q < 1:
        raise ValueError('Expected exactly 60 prior trading dates and a valid quantile')
    return references_from_matrix(reference_matrix(frame, calendar), q)


def reference_matrix(frame, calendar):
    """Reduce actual Tick history to five-second deltas, retaining gaps as NaN."""
    import numpy as np
    import pandas as pd
    grid = np.array(REFERENCE_SECONDS)
    groups = {d: g for d, g in frame.groupby(frame.index.strftime('%Y-%m-%d'))}
    matrix = []
    for day in calendar:
        if day not in groups:
            raise ValueError('Missing Tick trading date: ' + day)
        rows = groups[day].sort_index()
        if rows.index.duplicated().any():
            raise ValueError('Duplicate Tick timestamps')
        cash = rows.total_turnover.to_numpy(float)
        if not np.isfinite(cash).all() or (np.diff(cash) < 0).any():
            raise ValueError('Invalid cumulative turnover')
        def lookup(seconds):
            target = pd.Timestamp(day).value + seconds.astype(np.int64) * 1_000_000_000
            offsets = np.searchsorted(rows.index.asi8, target, side='left') - 1
            safe = np.maximum(offsets, 0)
            return np.where((offsets >= 0) & (target - rows.index.asi8[safe] <= 6_000_000_000), cash[safe], np.nan)
        matrix.append(lookup(grid) - lookup(grid - 5))
    return np.array(matrix)


def references_from_matrix(matrix, q):
    """Use the same 20/60-day quantiles for batch and streamed preparation."""
    import numpy as np
    import warnings
    if matrix.shape != (60, len(REFERENCE_SECONDS)) or not finite(q) or not 0 < q < 1:
        raise ValueError('Expected exactly 60 prior trading dates and a valid quantile')
    grid = np.array(REFERENCE_SECONDS)
    output = {}
    for length in (5, 30, 60):
        n = length // 5
        windows = np.full(matrix.shape, np.nan)
        for i in range(n - 1, len(grid)):
            if grid[i] - grid[i - n + 1] == (n - 1) * 5:
                windows[:, i] = matrix[:, i - n + 1:i + 1].sum(axis=1)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', RuntimeWarning)
            ref = np.maximum(np.nanquantile(windows[-20:], q, axis=0), np.nanquantile(windows, q, axis=0))
        valid = (np.isfinite(windows[-20:]).sum(axis=0) >= 19) & (np.isfinite(windows).sum(axis=0) >= 55)
        output[str(length)] = {f'{t // 3600:02d}:{t // 60 % 60:02d}:{t % 60:02d}': float(v)
                               for t, v, ok in zip(grid, ref, valid) if ok and np.isfinite(v) and v > 0}
    return output
