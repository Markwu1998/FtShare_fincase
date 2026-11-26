"""Independent Aces monitor preparation from the existing DDB tables."""
from datetime import date, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import re

from . import ddb_replay
from .burst import END, WINDOWS, RULE_VERSION, make_baseline, quantile
from .collect import read
from .model import SLOTS, finite, iso_utc
from .provider import CHINA, number
from .tick_factors import (FACTOR_VERSION, REFERENCE_SECONDS, reference_matrix,
                           references_from_matrix)

ROOT = Path(__file__).resolve().parents[1]
INDEX_DAILY = "loadTable('dfs://WIND_AIndexEODPrices','AIndexEODPrices')"


def resolve_path(value: str, root: Path, day: str) -> Path:
    """Resolve site paths independently of the launcher's working directory."""
    path = Path(value.format(date=day.replace('-', ''), day=day))
    return path if path.is_absolute() else root / path


def prior_calendar(session, day: str) -> list:
    """Read observed index sessions strictly before the requested monitoring day."""
    target = date.fromisoformat(day)
    first, last = target - timedelta(days=240), target - timedelta(days=1)
    frame = session.run(
        f"select distinct TRADE_DT from {INDEX_DAILY} where S_INFO_WINDCODE='000001.SH' "
        f"and TRADE_DT between {ddb_replay.dot(str(first))} : {ddb_replay.dot(str(last))}")
    days = sorted({str(value)[:10] for value in frame['TRADE_DT']})
    if any(not first.isoformat() <= value <= last.isoformat() for value in days):
        raise ValueError('DDB calendar contains dates outside the historical cutoff')
    if len(days) < 60:
        raise ValueError('Need 60 observed DDB trading dates before the monitoring day')
    return days[-60:]


def daily_inputs(session, days: list, symbols: list) -> tuple:
    """Read prior ETF cash turnover and T-1 reference values, with no stale fill."""
    codes = {ddb_replay.bare(symbol): symbol for symbol in symbols}
    frame = session.run(
        'select TICKER_SYMBOL,TRADE_DATE,CLOSE_PRICE,TURNOVER_VOL,TURNOVER_VALUE from ' + ddb_replay.DAILY +
        f" where TICKER_SYMBOL in {sorted(codes)!r} and TRADE_DATE between "
        f"{ddb_replay.dot(days[0])} : {ddb_replay.dot(days[-1])}")
    funds = session.run(
        'select TICKER_SYMBOL,TRADE_DATE,SHARES,NAV,NET_INFLOW from ' + ddb_replay.FUNDS +
        f" where TICKER_SYMBOL in {sorted(codes)!r} and TRADE_DATE={ddb_replay.dot(days[-1])}")
    daily, previous, seen = {}, {s: {'date': days[-1]} for s in symbols}, set()
    for row in frame.to_dict('records'):
        symbol, stamp = codes.get(str(row['TICKER_SYMBOL'])), str(row['TRADE_DATE'])[:10]
        if symbol is None or stamp not in days:
            raise ValueError('DDB daily response outside requested symbols/dates')
        if (symbol, stamp) in seen:
            raise ValueError('Duplicate DDB daily rows')
        seen.add((symbol, stamp))
        amount = number(row['TURNOVER_VALUE'])
        daily.setdefault(stamp, {})[symbol] = amount if amount is not None and amount >= 0 else None
        if stamp == days[-1]:
            previous[symbol].update(close=number(row['CLOSE_PRICE']), volume=number(row['TURNOVER_VOL']),
                                    turnover=amount)
    seen.clear()
    for row in funds.to_dict('records'):
        symbol = codes.get(str(row['TICKER_SYMBOL']))
        if symbol is None or str(row['TRADE_DATE'])[:10] != days[-1]:
            raise ValueError('DDB fund response outside prior trading date')
        if symbol in seen:
            raise ValueError('Duplicate DDB fund rows')
        seen.add(symbol)
        previous[symbol].update(shares=number(row['SHARES']), nav=number(row['NAV']),
                                net_inflow=number(row['NET_INFLOW']))
    for values in previous.values():
        for key in ('close', 'volume', 'turnover', 'shares', 'nav', 'net_inflow'):
            value = values.get(key)
            if (value is None or (key in ('close', 'shares', 'nav') and value <= 0)
                    or (key in ('volume', 'turnover') and value < 0)):
                values[key] = None
    return daily, previous


def history_inputs(session, days: list, symbols: list, indexes: list,
                   tick_enabled: bool, progress=None) -> tuple:
    """Process one day at a time; retain minute arrays and reduced five-second matrices."""
    import numpy as np
    import pandas as pd
    minutes, tick_rows, index_prices = {}, {s: [] for s in symbols}, {}
    codes = sorted(set(symbols + indexes))
    for day in days:
        frame = session.run(
            'select MDDate,SecurityID,MDTime,TotalValueTrade,LastPx from ' + ddb_replay.SNAPSHOT +
            f" where MDDate={ddb_replay.dot(day)} and SecurityID in {codes!r}")
        if len(frame):
            if any(str(value)[:10] != day for value in frame['MDDate']):
                raise ValueError('DDB snapshot response outside prior trading date')
            if not set(frame['SecurityID'].astype(str)).issubset(codes):
                raise ValueError('DDB snapshot response contains unexpected symbols')
        groups = dict(tuple(frame.groupby('SecurityID'))) if len(frame) else {}
        for symbol in symbols + indexes:
            group = groups.get(symbol)
            normalized = None
            if group is not None:
                stamps = [ddb_replay.combined(row.MDDate, row.MDTime).replace(tzinfo=None)
                          for row in group.itertuples()]
                normalized = pd.DataFrame({
                    'total_turnover': group['TotalValueTrade'].to_numpy(dtype=float) / ddb_replay.PRICE_SCALE,
                    'close': group['LastPx'].to_numpy(dtype=float) / ddb_replay.PRICE_SCALE,
                }, index=pd.DatetimeIndex(stamps)).sort_index()
                normalized = normalized.between_time('09:30', '15:00:59')
                if normalized.index.duplicated().any():
                    raise ValueError(f'Duplicate DDB snapshot timestamps: {day} {symbol}')
            if symbol in symbols:
                if normalized is not None and len(normalized):
                    cash = normalized.total_turnover.to_numpy()
                    if not np.isfinite(cash).all() or (cash < 0).any() or (np.diff(cash) < 0).any():
                        raise ValueError(f'Invalid DDB cumulative turnover: {day} {symbol}')
                    seconds = [t.hour * 3600 + t.minute * 60 + t.second + t.microsecond / 1e6
                               for t in normalized.index]
                    minutes.setdefault(day, {})[symbol] = ddb_replay.minute_amounts_from_seconds(seconds, cash)
                if tick_enabled:
                    matrix = (reference_matrix(normalized, [day]) if normalized is not None and len(normalized)
                              else np.full((1, len(REFERENCE_SECONDS)), np.nan))
                    tick_rows[symbol].append(matrix[0])
            elif normalized is not None and len(normalized):
                # Match the minute-end close convention used by the existing evidence rules.
                for slot in SLOTS:
                    end = pd.Timestamp(day + ' ' + slot)
                    offset = normalized.index.searchsorted(end, side='right') - 1
                    if offset >= 0 and end - normalized.index[offset] <= pd.Timedelta(seconds=10):
                        close = number(normalized.iloc[offset]['close'])
                        if close is not None and close > 0:
                            index_prices.setdefault(symbol, {})[day + 'T' + slot] = close
        if progress:
            progress(day)
    return minutes, {s: np.array(rows) for s, rows in tick_rows.items()} if tick_enabled else {}, index_prices


def index_references(prices: dict) -> list:
    """Same two-minute return P95 and 55-day minimum as PushSession.index_thresholds."""
    samples = {}
    for symbol, values in prices.items():
        for stamp, close in values.items():
            now = datetime.fromisoformat(stamp)
            slot = now.strftime('%H:%M')
            prior = values.get((now - timedelta(minutes=2)).isoformat(timespec='minutes'))
            if prior and (slot >= '13:03' or '09:33' <= slot <= '11:30'):
                samples.setdefault((symbol, slot), []).append(close / prior - 1)
    return [[s.replace('.SH', '.XSHG').replace('.SZ', '.XSHE'), slot, quantile(values, .95)]
            for (s, slot), values in samples.items() if len(values) >= 55]


def archive_status(path: Path, as_of: str) -> dict:
    """Inventory existing archives without relabeling their supplier or readiness."""
    result = {'path': str(path.resolve()), 'status': 'missing'}
    if not path.is_file():
        return result
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
        rows = value.get('rows', [])
        dates = value.get('history', {}).get('dates', []) or [r['date'] for r in rows]
        latest = max(dates) if dates else None
        published = max((r['published'] for r in rows if r.get('published')), default=None)
        result.update(provider=value.get('provider'), latest_date=latest, sha256=sha256(raw).hexdigest())
        if published:
            result['latest_published'] = published
        result['status'] = ('unknown_date' if latest is None else
                            'after_cutoff' if latest > as_of or (published and published > as_of) else
                            'current' if latest == as_of else 'older')
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        result['status'] = 'invalid'
    return result


def prepare(session, day: str, config: dict, root: Path = ROOT, progress=None) -> dict:
    """Build a single monitoring bundle, independent of IndexArb daily_factors.json."""
    date.fromisoformat(day)
    funds = read(resolve_path(config['universe_file'], root, day))['funds']
    by_symbol = {fund['symbol']: fund for fund in funds}
    rules = read(resolve_path(config['burst_config_file'], root, day))
    symbols = rules.get('symbols') or read(resolve_path(config['basket_file'], root, day))['symbols']
    if not symbols or len(set(symbols)) != len(symbols) or any(s not in by_symbol for s in symbols):
        raise ValueError('Monitor symbols must be unique ETFs in the configured universe')
    q = rules.get('amount_window_quantile', .75)
    if not finite(q) or not 0 < q < 1:
        raise ValueError('amount_window_quantile must be between 0 and 1')
    indexes = sorted({by_symbol[s]['tracking_index_symbol'] for s in symbols
                      if by_symbol[s].get('tracking_index_symbol')})
    if any(not re.fullmatch(r'\d{6}\.(SH|SZ)', s) for s in symbols + indexes):
        raise ValueError('Expected exchange-qualified SH/SZ symbols')
    days = prior_calendar(session, day)
    daily, previous = daily_inputs(session, days, symbols)
    enabled = config.get('prepare_tick_references', True)
    if not isinstance(enabled, bool):
        raise ValueError('prepare_tick_references must be a boolean')
    minutes, matrices, prices = history_inputs(session, days, symbols, indexes, enabled, progress)
    baseline = make_baseline(days, minutes, daily, day, q, symbols)
    refs = {s: references_from_matrix(matrices[s], q) for s in symbols} if enabled else {}
    coverage = {}
    for symbol, (totals, windows) in baseline.items():
        valid = sum(all(finite(v) and v > 0 for v in window_refs[0]) for window_refs in windows.values())
        expected = sum(1 for length in WINDOWS for i in range(END + 1)
                       if i + 1 - length >= (120 if i >= 120 else 0))
        counts = {length: len(values) for length, values in refs.get(symbol, {}).items()}
        expected_ticks = {str(length): sum(
            t - REFERENCE_SECONDS[max(0, i - length // 5 + 1)] == length - 5 and i >= length // 5 - 1
            for i, t in enumerate(REFERENCE_SECONDS)) for length in (5, 30, 60)}
        coverage[symbol] = {
            'daily_samples': [sum(finite(daily.get(d, {}).get(symbol)) for d in days[-n:]) for n in (20, 60)],
            'minute_windows': valid, 'expected_minute_windows': expected,
            'formal_ready': all(finite(v) and v > 0 for v in totals) and valid == expected,
            'tick_slots': counts, 'expected_tick_slots': expected_ticks,
            'tick_available': enabled and all(counts.get(str(k), 0) > 0 for k in (5, 30, 60)),
            'tick_complete': enabled and counts == expected_ticks,
            'missing_previous_fields': [key for key in ('close', 'volume', 'turnover', 'shares', 'nav', 'net_inflow')
                                        if previous[symbol].get(key) is None],
        }
    identity = {'day': day, 'as_of': days[-1], 'symbols': list(symbols), 'quantile': q, 'rule_version': RULE_VERSION}
    index = index_references(prices)
    return {
        'schema_version': 1, 'kind': 'aces-monitor-preopen', 'day': day, 'as_of': days[-1],
        'created_at': iso_utc(datetime.now(CHINA)), 'provider': 'DDB', 'unit': 'CNY',
        'target_calendar_check': 'caller_supplied', 'calendar': days, 'symbols': list(symbols),
        'parameters': {'amount_window_quantile': q, 'minute_driver': 'tick',
                       'futures_evidence': rules.get('futures_evidence', True)},
        'minute_baseline': {'identity': identity, 'ready': any(c['formal_ready'] for c in coverage.values()),
                            'baseline': {s: {'daily': totals, 'windows': [[k, i, r] for (k, i), r in w.items()]}
                                         for s, (totals, w) in baseline.items()}, 'index': index},
        'tick_baseline': {'version': FACTOR_VERSION, 'enabled': enabled, 'day': day, 'as_of': days[-1], 'quantile': q,
                          'calendar': days, 'references': refs},
        'previous_daily': previous,
        'instruments': [{key: by_symbol[s].get(key) for key in
                         ('symbol', 'name', 'bucket', 'tracking_index_symbol', 'listed_date')} for s in symbols],
        'coverage': coverage,
        'index_reference_codes': sorted({row[0] for row in index}),
        'archives': {name: archive_status(resolve_path(path, root, day), days[-1])
                     for name, path in config.get('archives', {}).items()},
        'sources': {'calendar': INDEX_DAILY, 'daily': ddb_replay.DAILY, 'funds': ddb_replay.FUNDS,
                    'historical_tick': ddb_replay.SNAPSHOT, 'snapshot_amount_divisor': ddb_replay.PRICE_SCALE,
                    'realtime': 'Aces EVENT_TICK / MatsFileGateway (SQLite)', 'iopv': 'IndexArb PCF'},
    }


def load_preopen(path: Path, day: str, as_of: str, symbols: list, q: float) -> dict:
    """Validate the bundle identity before a future Aces consumer adopts it."""
    value = read(path)
    if not isinstance(value, dict):
        raise ValueError('Invalid monitor preparation object')
    identity = {'day': day, 'as_of': as_of, 'symbols': list(symbols), 'quantile': q, 'rule_version': RULE_VERSION}
    if (value.get('schema_version'), value.get('kind'), value.get('day'), value.get('as_of')) != (
            1, 'aces-monitor-preopen', day, as_of) or not as_of < day:
        raise ValueError('Stale or incompatible monitor preparation')
    if value.get('unit') != 'CNY' or value.get('symbols') != list(symbols):
        raise ValueError('Monitor preparation unit/symbol mismatch')
    if value.get('minute_baseline', {}).get('identity') != identity:
        raise ValueError('Monitor preparation symbols/quantile/rule mismatch')
    calendar = value.get('calendar', [])
    if len(calendar) != 60 or calendar != sorted(set(calendar)) or calendar[-1] != as_of:
        raise ValueError('Invalid historical preparation calendar')
    baseline = value['minute_baseline'].get('baseline', {})
    if set(baseline) != set(symbols) or set(value.get('coverage', {})) != set(symbols):
        raise ValueError('Incomplete preparation symbol inventory')
    expected_windows = {(k, i) for k in WINDOWS for i in range(END + 1)}
    for item in baseline.values():
        windows = item.get('windows', [])
        if (len(item.get('daily', [])) != 2 or len(windows) != len(expected_windows)
                or {(k, i) for k, i, refs in windows} != expected_windows
                or any(len(refs) != 2 or any(len(pair) != 2 for pair in refs) for k, i, refs in windows)):
            raise ValueError('Invalid minute reference layout')
    tick = value.get('tick_baseline', {})
    if (tick.get('version'), tick.get('day'), tick.get('as_of'), tick.get('quantile')) != (
            FACTOR_VERSION, day, as_of, q):
        raise ValueError('Incompatible Tick references')
    if tick.get('calendar') != calendar:
        raise ValueError('Incompatible Tick calendar')
    references = tick.get('references')
    if not isinstance(references, dict) or (tick.get('enabled') and set(references) != set(symbols)):
        raise ValueError('Incomplete Tick reference inventory')
    for refs in references.values():
        if not isinstance(refs, dict) or any(not isinstance(refs.get(k), dict) for k in ('5', '30', '60')):
            raise ValueError('Invalid Tick reference layout')
    return value
