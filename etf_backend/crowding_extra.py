"""Independent archives for daily industry, quarterly holdings and daily financing."""
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from . import provider
from .collect import publish, read
from .crowding import data_by_day, ratio
from .model import finite, iso_utc

MODULES = ("industry", "funds", "financing")
INDEXES = {"000300.XSHG": "沪深300", "000905.XSHG": "中证500"}
FINANCE_INDEXES = {"000001.XSHG": "上证指数", **INDEXES, "000852.XSHG": "中证1000", "399006.XSHE": "创业板指"}
EXCLUDED = {"银行", "非银金融", "石油石化"}
GDP = "国内生产总值GDP(现价)_全国_当期值_季"


def day(value):
    return str(value)[:10]


def batches(items, size):
    ordered = sorted(items)
    for offset in range(0, len(ordered), size):
        yield ordered[offset:offset + size]


def industries(api, symbols, as_of):
    result = {}
    for batch in batches(symbols, 400):
        frame = api.get_instrument_industry(batch, source="sws", level=1, date=as_of)
        if frame is not None:
            result.update({s: n for s, n in frame["first_industry_name"].items() if isinstance(n, str) and n})
    return result


def shares(totals):
    total = sum(totals.values())
    return {key: 100 * value / total for key, value in totals.items()} if total > 0 else {}


def week_key(d):
    return date.fromisoformat(d).isocalendar()[:2]


def shsz_symbol(symbol):
    code, _, exchange = symbol.partition(".")
    if len(code) == 6 and code.isdigit():
        if exchange in {"SH", "XSHG"}:
            return code + ".XSHG"
        if exchange in {"SZ", "XSHE"}:
            return code + ".XSHE"
        if not exchange and code.startswith(("60", "68")):
            return code + ".XSHG"
        if not exchange and code.startswith(("00", "30")):
            return code + ".XSHE"
    return None


def industry_rows(api, start, end):
    """Daily industry crowding, with weekly-cached industry labels.

    Index membership and prices are evaluated every trading day. The SW level-1
    classification is cached by ISO week because it changes infrequently; this
    keeps a one-year rebuild practical while still producing daily rows.
    """
    days = [day(d) for d in api.get_trading_dates(start, end)]
    if not days:
        return
    members = {}
    for idx in INDEXES:
        raw = {day(d): set(ids) for d, ids in
               (api.index_components(idx, start_date=start, end_date=end) or {}).items()}
        by_day, last = {}, set()
        for d in days:
            if d in raw:
                last = set(raw[d])
            by_day[d] = set(last)
        members[idx] = by_day
    symbols = {s for by_day in members.values() for ids in by_day.values() for s in ids}
    capitals = data_by_day(api, symbols, start, end, capital=True)
    cash = data_by_day(api, symbols, start, end)
    missing_cash = {(d, s) for d in days for ids in (by_day[d] for by_day in members.values())
                    for s in ids if cash.get(d, {}).get(s) is None}
    for batch in batches({s for _, s in missing_cash}, 350):
        frame = api.is_suspended(batch, start_date=start, end_date=end)
        if frame is None:
            continue
        for d, row in frame.iterrows():
            for s in row.index[row.eq(True)]:
                cash.setdefault(day(d), {}).setdefault(s, 0)
                if cash[day(d)][s] is None:
                    cash[day(d)][s] = 0
    mapping = {}
    for as_of in days:
        key = week_key(as_of)
        if key not in mapping:
            mapping[key] = industries(api, symbols, as_of)
        labels = mapping[key]
        data = {}
        for idx, count in zip(members, (300, 500)):
            ids = members[idx].get(as_of, set())
            totals, turnover = defaultdict(float), defaultdict(float)
            classified = sum(s in labels for s in ids)
            capital_valid = len(ids) == classified == count
            turnover_valid = len(ids) == count
            for s in ids:
                name = labels.get(s)
                if name in EXCLUDED:
                    continue
                amount = cash.get(as_of, {}).get(s)
                if name and amount is not None and amount >= 0:
                    turnover[name] += amount
                else:
                    turnover_valid = False
                value = capitals.get(as_of, {}).get(s)
                if name and value is not None and value > 0:
                    totals[name] += value
                else:
                    capital_valid = False
            data[idx] = {"capital": shares(totals) if capital_valid else {},
                         "turnover": shares(turnover) if turnover_valid else {},
                         "classified": classified, "expected": count}
        yield {"date": as_of, "indexes": data}


def _holdings(fund, batch, period):
    """Fetch holdings, isolating codes the installed fund SDK does not recognise."""
    try:
        return fund.get_holdings(batch, date=period)
    except ValueError:
        if len(batch) == 1:
            return None
        middle = len(batch) // 2
        frames = [frame for frame in (_holdings(fund, batch[:middle], period),
                                      _holdings(fund, batch[middle:], period))
                  if frame is not None and not frame.empty]
        if not frames:
            return None
        import pandas as pd
        return pd.concat(frames)


def fund_rows(api, start, end):
    from rqdatac_fund import fund

    for year in range(date.fromisoformat(start).year, date.fromisoformat(end).year + 1):
        for suffix in ("03-31", "06-30", "09-30", "12-31"):
            period = f"{year}-{suffix}"
            if not start <= period <= end:
                continue
            pool = {code for kind in ("1101", "1201")
                    for code in (fund.get_category({"fund_type": [kind]}, date=period) or [])}
            main = {}
            for batch in batches(pool, 350):
                relations = fund.get_related_code(batch)
                if relations is None:
                    continue
                for row in relations.itertuples(index=False):
                    if (row.type == "multi_share" and day(row.effective_date) <= period
                            and (day(row.cancel_date) in {"None", "NaT", "0000-00-00"} or day(row.cancel_date) > period)):
                        main[row.related_code] = row.main_code
            pool = {main.get(code, code) for code in pool}
            positions = defaultdict(list)
            releases = {}
            for batch in batches(pool, 200):
                frame = _holdings(fund, batch, period)
                if frame is None or frame.empty:
                    continue
                for row in frame.reset_index().itertuples(index=False):
                    if day(row.date) != period or not period <= day(row.release_date) <= end or row.type != "Stock":
                        continue
                    code = row.fund_id
                    release = day(row.release_date)
                    if code not in releases or release < releases[code]:
                        positions[code].clear()
                        releases[code] = release
                    value = provider.number(row.market_value)
                    if release == releases[code] and value is not None and value > 0:
                        positions[code].append((value, row.order_book_id, str(row.category).lower()))
            top = [p for holdings in positions.values() for p in sorted(set(holdings), reverse=True)[:10]]
            sample = [(value, shsz_symbol(s)) for value, s, market in top if market == "ashare"]
            mapping = industries(api, {symbol for _, symbol in sample if symbol}, period)
            weights = defaultdict(float)
            for value, symbol in sample:
                weights[mapping.get(symbol, "未分类")] += value
            if weights:
                yield {"date": period, "published": max(releases.values()), "funds": sum(bool(v) for v in positions.values()),
                       "expected": len(pool), "weights": shares(weights)}


def released_values(events, days, trailing_gdp=False):
    known = {}
    position = 0
    events = sorted(events)
    result = {}
    for d in days:
        while position < len(events) and events[position][0] <= d:
            info, period, value = events[position]
            known[period] = (info, value)
            position += 1
        if known:
            latest = sorted(known)[-4:] if trailing_gdp else [max(known)]
            if trailing_gdp:
                quarters = [int(p[:4]) * 4 + (int(p[5:7]) - 1) // 3 for p in latest]
                if len(quarters) != 4 or quarters[-1] - quarters[0] != 3:
                    continue
            result[d] = (sum(known[p][1] for p in latest), latest[-1], max(known[p][0] for p in latest))
    return result


def financing_rows(api, start, end):
    warmup = date.fromisoformat(start) - timedelta(days=390)
    warmup = (warmup - timedelta(days=warmup.weekday())).isoformat()
    days = [str(d) for d in api.get_trading_dates(warmup, end)]
    margin = defaultdict(dict)
    frame = api.get_securities_margin(["XSHE", "XSHG"], start_date=warmup, end_date=end, fields=["margin_balance"])
    if frame is not None:
        for (exchange, d), value in frame["margin_balance"].items():
            if (value := provider.number(value)) is not None and value >= 0:
                margin[day(d)][exchange] = value
    totals = {d: sum(v.values()) for d, v in margin.items() if set(v) == {"XSHE", "XSHG"}}
    m2 = api.econ.get_money_supply(start_date=warmup, end_date=end)
    gdp_start = (date.fromisoformat(start) - timedelta(days=500)).isoformat()
    gdp = api.econ.get_factors(GDP, gdp_start, end)
    m2_values = released_values([(day(info), day(row.effective_date), row.m2 * 1e6)
                                 for info, row in m2.iterrows() if finite(row.m2) and row.m2 > 0], days) if m2 is not None else {}
    gdp_values = released_values([(day(row.info_date), day(row.end_date), row.value * 1e8)
                                  for row in gdp.reset_index().itertuples(index=False)
                                  if finite(row.value) and row.value > 0], days, True) if gdp is not None else {}
    weekly = defaultdict(list)
    for d in days:
        session = date.fromisoformat(d)
        weekly[session - timedelta(days=session.weekday())].append(d)
    yoy = {}
    for monday, current in weekly.items():
        prior = weekly.get(monday - timedelta(weeks=52), [])
        if prior and all(d in totals for d in current + prior) and monday + timedelta(days=4) <= date.fromisoformat(end):
            yoy[current[-1]] = (sum(totals[d] for d in current) / len(current)
                                 / (sum(totals[d] for d in prior) / len(prior)) - 1) * 100
    index_data = defaultdict(dict)
    for symbol, stamp, close, _ in provider.bars(api, {key: key for key in FINANCE_INDEXES}, start, end, "1d"):
        index_data[day(datetime.fromtimestamp(stamp / 1000, provider.CHINA))][symbol] = close
    available = [(i, d) for i, d in enumerate(days) if d >= start]
    for offset in range(0, len(available), 22):
        segment = available[offset:offset + 22]
        pools = {}
        for _, d in segment:
            frame = api.all_instruments("CS", date=d)
            pools[d] = set(frame.loc[frame["exchange"].isin(("XSHE", "XSHG")), "order_book_id"])
        market = defaultdict(dict)
        fields = ["a_share_market_val_3", "a_share_market_val_in_circulation"]
        for batch in batches(set.union(*pools.values()), 350):
            frame = api.get_factor(batch, fields, start_date=segment[0][1], end_date=segment[-1][1])
            if frame is not None:
                for row in frame.reset_index().itertuples(index=False):
                    market[day(row.date)][row.order_book_id] = tuple(provider.number(getattr(row, k)) for k in fields)
        for i, d in segment:
            balance = totals.get(d)
            past = totals.get(days[i - 20]) if i >= 20 else None
            change = (balance - past) / 20 if balance is not None and past is not None else None
            found = [market[d].get(s, (None, None)) for s in pools[d]]
            cap, floating = [sum(values) if values and all(v is not None and v > 0 for v in values) else None
                             for values in (list(zip(*found)) if found else ([], []))]
            m2_row, gdp_row = m2_values.get(d), gdp_values.get(d)
            yield {"date": d, "balance": ratio(balance, 1e8), "change": ratio(change, 1e8),
                   "change_share": ratio(change, balance / 100 if balance else None), "weekly_yoy": yoy.get(d),
                   "capital_share": ratio(balance, cap / 100 if cap else None),
                   "floating_share": ratio(balance, floating / 100 if floating else None),
                   "m2_share": ratio(balance, m2_row[0] / 100 if m2_row else None),
                   "gdp_share": ratio(cap, gdp_row[0] / 100 if gdp_row else None),
                   "m2_date": m2_row[2] if m2_row else None, "gdp_date": gdp_row[2] if gdp_row else None,
                   "indexes": index_data[d]}


def path_for(module, base):
    base = Path(base)
    return base.with_name(f"{base.stem}-{module}.json")


def validate_extra(data, module):
    dates = [date.fromisoformat(row["date"]) for row in data["rows"]]
    if data["module"] != module or data["provider"] != "RQData" or not dates or dates != sorted(set(dates)):
        raise ValueError("Invalid crowding module archive")
    for row in data["rows"]:
        if module == "funds" and (not row["weights"] or not 0 < row["funds"] <= row["expected"]
                                  or row["published"] < row["date"]):
            raise ValueError("Invalid fund report")
        if module == "industry" and set(row["indexes"]) != set(INDEXES):
            raise ValueError("Invalid industry coverage")
        if module == "financing" and any(row[k] is not None and not finite(row[k])
                                          for k in ("balance", "change", "weekly_yoy", "gdp_share", "m2_share")):
            raise ValueError("Invalid financing value")
    return data


def _is_daily(rows):
    if len(rows) < 3:
        return True
    gaps = [(date.fromisoformat(b["date"]) - date.fromisoformat(a["date"])).days
            for a, b in zip(rows, rows[1:])]
    return sum(1 for gap in gaps if gap <= 3) >= max(1, len(gaps) // 2)


def update_extra(api, module, base, start, end):
    path = path_for(module, base)
    previous = validate_extra(read(path), module)["rows"] if path.is_file() else []
    # The old industry archive was weekly; rebuild it as daily rather than
    # mixing weekly and daily rows in one chart.
    if module == "industry" and previous and not _is_daily(previous):
        previous = []
    needs = {"industry": (370, 21), "funds": (450, 220), "financing": (370, 35)}
    initial, overlap = needs[module]
    end = min(end, (datetime.now(provider.CHINA).date() - timedelta(days=1)).isoformat())
    start = start or (date.fromisoformat(previous[-1]["date"] if previous else end)
                      - timedelta(days=overlap if previous else initial)).isoformat()
    collect = {"industry": industry_rows, "funds": fund_rows, "financing": financing_rows}[module]
    rows = {r["date"]: r for r in previous}
    fresh = list(collect(api, start, end))
    if not fresh and not previous:
        raise ValueError("RQData returned no crowding module observations")
    if previous and module in {"industry", "financing"}:
        last = previous[-1]["date"]
        pending = [str(day) for day in api.get_trading_dates(
            (date.fromisoformat(last) + timedelta(days=1)).isoformat(), end) if str(day) > last]
        fresh_dates = {row["date"] for row in fresh}
        if pending and not any(day in fresh_dates for day in pending):
            raise ValueError(f"{module} returned no fresh observations after {last}")
    for r in fresh:
        old = rows.get(r["date"])
        if old and module == "funds" and (r["funds"] < old["funds"] or r["published"] < old["published"]):
            continue
        if old and module == "industry":
            for idx, values in r["indexes"].items():
                for metric in ("capital", "turnover"):
                    if not values[metric]:
                        values[metric] = old["indexes"][idx][metric]
        if old and module == "financing":
            r = {**old, **{key: value for key, value in r.items() if value is not None}}
        rows[r["date"]] = r
    snapshot = {"provider": "RQData", "module": module, "built_at": iso_utc(datetime.now(provider.CHINA)),
                "rows": [rows[d] for d in sorted(rows)]}
    if module == "industry":
        snapshot.update({"price_frequency": "daily", "label_frequency": "weekly"})
    if module == "financing":
        snapshot["indexes"] = FINANCE_INDEXES
    publish(path, validate_extra(snapshot, module))
    return snapshot
