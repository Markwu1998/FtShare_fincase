"""Collect and validate daily market crowding independently of the ETF archive."""

from bisect import bisect_left, bisect_right, insort
from collections import defaultdict
from datetime import date, datetime, timedelta
from math import ceil
from pathlib import Path

from . import provider
from .collect import publish, read
from .model import finite, iso_utc

DEFAULT_PATH = "data/private/rqdata-crowding.json"
LEVELS = (1, 3, 5)
INDEXES = {
    "000300.XSHG": "沪深300",
    "000905.XSHG": "中证500",
    "000852.XSHG": "中证1000",
    "399006.XSHE": "创业板指",
    "000688.XSHG": "科创50",
}
LARGE, SMALL = "000300.XSHG", "000852.XSHG"


def data_by_day(api, symbols, start, end, *, capital=False):
    result = defaultdict(dict)
    symbols = sorted(symbols)
    field = "market_cap_3" if capital else "total_turnover"
    for offset in range(0, len(symbols), 350):
        batch = symbols[offset:offset + 350]
        frame = (api.get_factor(batch, field, start_date=start, end_date=end) if capital else
                 api.get_price(batch, start_date=start, end_date=end, fields=[field], adjust_type="none"))
        if frame is None:
            continue
        for (symbol, stamp), value in frame[field].items():
            result[provider.local_time(stamp).date().isoformat()][symbol] = provider.number(value)
    return result


def turnover_metrics(universe, amounts, suspended):
    ranked = sorted((amounts[s] for s in universe if s not in suspended
                     and amounts.get(s) is not None and amounts[s] > 0), reverse=True)
    covered = sum(s in suspended or (amounts.get(s) is not None and amounts[s] >= 0) for s in universe)
    total = sum(ranked)
    tiers = {}
    for level in LEVELS:
        count = ceil(len(ranked) * level / 100)
        amount = sum(ranked[:count])
        tiers[str(level)] = {"count": count, "amount": amount / 1e8 if total and covered == len(universe) else None,
                             "share": 100 * amount / total if total and covered == len(universe) else None}
    return {"expected": len(universe), "covered": covered, "suspended": len(universe & suspended),
            "traded": len(ranked), "total": total / 1e8 if total and covered == len(universe) else None, "tiers": tiers}


def basket_total(symbols, values, suspended=frozenset(), positive=False):
    known = [0 if s in suspended else values.get(s) for s in symbols]
    if known and all(v is not None and (v > 0 if positive else v >= 0) for v in known):
        return sum(known)
    return None


def ratio(numerator, denominator):
    return numerator / denominator if numerator is not None and denominator else None


def collect_rows(api, days):
    start, end = days[0], days[-1]
    pools = {}
    for day in days:
        instruments = api.all_instruments("CS", date=day)
        pools[day] = set(instruments.loc[instruments["exchange"].isin(("XSHG", "XSHE")), "order_book_id"])
    if any(not pool for pool in pools.values()):
        raise ValueError("Empty Shanghai/Shenzhen stock universe")
    members = {
        code: {provider.local_time(d).date().isoformat(): set(ids) for d, ids in (
            api.index_components(code, start_date=start, end_date=end) or {}).items()}
        for code in (LARGE, SMALL)
    }
    style_symbols = {s for index in members.values() for ids in index.values() for s in ids}
    all_symbols = set.union(*pools.values(), style_symbols)
    cash = data_by_day(api, all_symbols, start, end)
    caps = data_by_day(api, style_symbols, start, end, capital=True)
    closes = defaultdict(dict)
    for code, stamp, close, _ in provider.bars(api, {code: code for code in INDEXES}, start, end, "1d"):
        day = datetime.fromtimestamp(stamp / 1000, provider.CHINA).date().isoformat()
        closes[day][code] = close
    unresolved = sorted({s for day, pool in pools.items() for s in pool if not cash[day].get(s)}
                        | {s for code in members for day, ids in members[code].items()
                           for s in ids if not cash[day].get(s)})
    suspensions = defaultdict(set)
    for offset in range(0, len(unresolved), 350):
        frame = api.is_suspended(unresolved[offset:offset + 350], start_date=start, end_date=end)
        if frame is None:
            continue
        for stamp, row in frame.iterrows():
            day = provider.local_time(stamp).date().isoformat()
            suspensions[day].update(row.index[row.eq(True)])
    for day in days:
        baskets = [members[code].get(day, set()) for code in (LARGE, SMALL)]
        complete = [ids if len(ids) == count else set() for ids, count in zip(baskets, (300, 1000))]
        yield {
            "date": day, "concentration": turnover_metrics(pools[day], cash[day], suspensions[day]),
            "style": {"capital": ratio(*(basket_total(ids, caps[day], positive=True) for ids in complete)),
                      "turnover": ratio(*(basket_total(ids, cash[day], suspensions[day]) for ids in complete)),
                      "members": [len(ids) for ids in baskets]},
            "indexes": closes[day],
        }


def add_percentiles(rows):
    samples = {str(level): [] for level in LEVELS}
    left = 0
    for i, row in enumerate(rows):
        current = date.fromisoformat(row["date"])
        try:
            cutoff = current.replace(year=current.year - 5)
        except ValueError:
            cutoff = current.replace(year=current.year - 5, day=28)
        while left < i and rows[left]["date"] < cutoff.isoformat():
            for level, tier in rows[left]["concentration"]["tiers"].items():
                if tier["share"] is not None:
                    samples[level].pop(bisect_left(samples[level], tier["share"]))
            left += 1
        for level, tier in row["concentration"]["tiers"].items():
            sample = samples[level]
            share = tier["share"]
            if share is not None:
                insort(sample, share)
            tier["sample"] = len(sample)
            tier["percentile"] = 100 * bisect_right(sample, share) / len(sample) if share is not None else None


def validate_snapshot(snapshot):
    rows = snapshot["rows"]
    dates = [row["date"] for row in rows]
    if snapshot["schema"] != 1 or snapshot["provider"] != "RQData" or not dates or dates != sorted(set(dates)):
        raise ValueError("Invalid crowding snapshot")
    for row in rows:
        date.fromisoformat(row["date"])
        market, style = row["concentration"], row["style"]
        if (set(market["tiers"]) != {str(level) for level in LEVELS}
                or not isinstance(row["indexes"], dict)
                or any(value is not None and not finite(value) for value in (
                    market["total"], style["capital"], style["turnover"], *row["indexes"].values()))
                or not 0 <= market["traded"] + market["suspended"] <= market["covered"] <= market["expected"]):
            raise ValueError("Invalid crowding row")
        for tier in market["tiers"].values():
            if tier["count"] < 0 or tier["sample"] < 0 or any(
                    value is not None and not finite(value)
                    for value in (tier["amount"], tier["share"], tier["percentile"])):
                raise ValueError("Invalid crowding tier")
    return snapshot


def update(api, path, start, end):
    path = Path(path)
    previous = validate_snapshot(read(path))["rows"] if path.is_file() else []
    # Revisit recent sessions so a late upstream correction can fill a gap.
    start = start or (previous[max(0, len(previous) - 5)]["date"] if previous else
                      (date.fromisoformat(end) - timedelta(days=366)).isoformat())
    end = min(end, (datetime.now(provider.CHINA).date() - timedelta(days=1)).isoformat())
    days = [str(day) for day in api.get_trading_dates(start, end)] if start <= end else []
    if not days:
        raise ValueError("No completed trading sessions requested")
    by_day = {row["date"]: row for row in previous}
    for offset in range(0, len(days), 22):
        for row in collect_rows(api, days[offset:offset + 22]):
            old = by_day.get(row["date"])
            if old:
                if old["concentration"]["total"] is not None and row["concentration"]["total"] is None:
                    row["concentration"] = old["concentration"]
                for field in ("capital", "turnover"):
                    if row["style"][field] is None:
                        row["style"][field] = old["style"][field]
                row["indexes"] = {**old["indexes"], **{k: v for k, v in row["indexes"].items() if v is not None}}
            by_day[row["date"]] = row
    rows = [by_day[day] for day in sorted(by_day)]
    if not any(row["concentration"]["total"] is not None for row in rows) or not any(
            row["style"]["capital"] is not None and row["style"]["turnover"] is not None for row in rows):
        raise ValueError("RQData crowding coverage is insufficient")
    add_percentiles(rows)
    snapshot = {"schema": 1, "provider": "RQData", "built_at": iso_utc(datetime.now(provider.CHINA)),
                "scope": ["上交所", "深交所"], "indexes": INDEXES, "rows": rows}
    publish(path, validate_snapshot(snapshot))
    return snapshot
