"""DDB inputs for the single-day Tick replay.

Produces the same normalized files as the RQData path (``baseline.json``,
``etf/*.csv``, ``futures/*.csv``, ``contracts.json`` and ``fund-flow.json``)
so ``scripts/replay-burst-day.py`` can reuse every downstream step unchanged.
"""
from bisect import bisect_right
from datetime import date, datetime, time, timedelta
import argparse
import csv
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from .burst import RULE_VERSION, make_baseline
from .collect import publish, read
from .model import BASKET, SLOTS
from .provider import CHINA, number

SNAPSHOT = "loadTable('dfs://MD','MDSnapshot')"
FUTURES = "loadTable('dfs://futures_tick','futures_tick_data')"
FUNDS = "loadTable('dfs://hermes_etf_data','fund_etf_nr')"
DAILY = "loadTable('dfs://hermes_etf_data','mkt_fundd')"

ETF_FIELDS = ["last", "total_turnover", "volume"]
FUTURES_FIELDS = [*ETF_FIELDS, "open_interest", "b1", "a1"]
PRICE_SCALE = 10000  # MDSnapshot prices and turnover are stored ×10000.
OPEN, FINAL = time(9, 30), time(15, 0, 59)
OPEN_SECOND, FINAL_SECOND = 9 * 3600 + 30 * 60, 15 * 3600 + 59
CLOSE_GRACE = timedelta(seconds=5)
MAX_AGE = timedelta(seconds=10)


def connect():
    """Open the DDB session with the same environment settings as DDBAdapter."""
    from .sources import DDBAdapter
    return DDBAdapter().session()


def dot(day):
    return day.replace("-", ".")


def bare(symbol):
    return symbol.split(".")[0]


def as_datetime(value):
    """Convert a DDB date/time cell to a naive datetime."""
    stamp = value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
    return stamp if isinstance(stamp, datetime) else datetime.fromisoformat(str(stamp))


def combined(day_value, time_value):
    """Combine a DDB trading date with its time-of-day column.

    DolphinDB stores these time columns as ``1970-01-01 HH:MM:SS``, so the
    snapshot date must come from ``MDDate``/``TradeDate`` instead.
    """
    base, clock = as_datetime(day_value), as_datetime(time_value)
    return datetime.combine(base.date(), clock.time()).replace(tzinfo=CHINA)


def first_level(value):
    """Return the first level of a five-level DDB price queue."""
    try:
        return number(value[0]) if value is not None and len(value) else None
    except (TypeError, IndexError):
        return None


def trading_dates(session, day):
    """DDB trading calendar from the snapshot table, ending exactly at ``day``."""
    first = (date.fromisoformat(day) - timedelta(days=160)).isoformat()
    frame = session.run(f"select distinct MDDate from {SNAPSHOT} "
                        f"where MDDate between {dot(first)} : {dot(day)}")
    dates = sorted({str(value)[:10] for value in frame["MDDate"].tolist()})
    if not dates or dates[-1] != day:
        raise ValueError("Requested date is not a DDB trading day")
    return dates


def minute_amounts(day, values):
    """Turn sorted ``(timestamp, cumulative turnover)`` rows into minute amounts."""
    if not values:
        return [None] * len(SLOTS)
    seconds = [stamp.hour * 3600 + stamp.minute * 60 + stamp.second + stamp.microsecond / 1e6
               for stamp, _ in values]
    return minute_amounts_from_seconds(seconds, [cash for _, cash in values])


def minute_amounts_from_seconds(seconds, cash):
    """Minute amounts from sorted ``(second of day, cumulative turnover)`` rows."""
    amounts = []
    for index, slot in enumerate(SLOTS):
        end = time.fromisoformat(slot)
        end_second = end.hour * 3600 + end.minute * 60 + end.second
        offset = bisect_right(seconds, end_second + CLOSE_GRACE.total_seconds()) - 1
        if offset < 0 or end_second - seconds[offset] > MAX_AGE.total_seconds():
            amounts.append(None)
            continue
        current = cash[offset]
        if index == 0:
            amounts.append(current)  # first minute keeps the opening auction
            continue
        start_second = end_second - 60
        if start_second == 13 * 3600:
            start_second = 11 * 3600 + 30 * 60  # no ticks over lunch
        previous_offset = bisect_right(seconds, start_second + CLOSE_GRACE.total_seconds()) - 1
        previous = (None if previous_offset < 0 or
                    start_second - seconds[previous_offset] > MAX_AGE.total_seconds()
                    else cash[previous_offset])
        amounts.append(current - previous if previous is not None and current >= previous else None)
    return amounts


def prior_minutes(session, days, symbols):
    """Aggregate prior-day snapshot turnover into per-slot minute amounts (CNY)."""
    frame = session.run(
        "select MDDate,SecurityID,MDTime,TotalValueTrade from " + SNAPSHOT +
        f" where MDDate between {dot(days[0])} : {dot(days[-1])} and SecurityID in {sorted(symbols)!r}")
    records = frame[["MDDate", "SecurityID", "MDTime", "TotalValueTrade"]].copy()
    records["day"] = records["MDDate"].dt.strftime("%Y-%m-%d")
    clock = records["MDTime"].dt
    records["second"] = (clock.hour * 3600 + clock.minute * 60 + clock.second
                         + clock.microsecond / 1e6)
    records["cash"] = records["TotalValueTrade"] / PRICE_SCALE
    records = records[records["day"].isin(set(days))
                      & (records["cash"] >= 0)
                      & (records["second"] >= OPEN_SECOND)
                      & (records["second"] <= FINAL_SECOND)]
    records.sort_values(["day", "SecurityID", "second"], inplace=True)
    output = {}
    for (day, symbol), group in records.groupby(["day", "SecurityID"], sort=False):
        output.setdefault(day, {})[str(symbol)] = minute_amounts_from_seconds(
            group["second"].tolist(), group["cash"].tolist())
    return output


def daily_turnover(session, days, symbols):
    """Daily turnover in CNY from the DDB daily market table."""
    codes = {bare(symbol): symbol for symbol in symbols}
    frame = session.run(
        "select TICKER_SYMBOL,TRADE_DATE,TURNOVER_VALUE from " + DAILY +
        f" where TICKER_SYMBOL in {sorted(codes)!r} and TRADE_DATE between {dot(days[0])} : {dot(days[-1])}")
    output = {}
    for row in frame.to_dict("records"):
        symbol, day = codes.get(str(row["TICKER_SYMBOL"])), str(row["TRADE_DATE"])[:10]
        value = number(row["TURNOVER_VALUE"])
        if symbol and day in days and value is not None and value >= 0:
            output.setdefault(day, {})[symbol] = value
    return output


def prepare_baseline(session, day, symbols, q, path):
    """Build and publish a baseline.json identical to the RQData version."""
    history = trading_dates(session, day)[-61:-1]
    if len(history) < 60:
        raise ValueError("Need 60 DDB trading days before the replay date")
    minutes = prior_minutes(session, history, symbols)
    daily = daily_turnover(session, history, symbols)
    write_baseline(day, history[-1], history, minutes, daily, q, symbols, path)


def write_baseline(day, as_of, days, minutes, daily, q, symbols, path):
    """Publish one baseline file from already aggregated history."""
    baseline = make_baseline(days, minutes, daily, day, q, symbols)
    publish(path, {
        "identity": {"day": day, "as_of": as_of, "symbols": list(symbols), "quantile": q,
                     "rule_version": RULE_VERSION},
        "baseline": {symbol: {"daily": totals, "windows": [[k, i, refs] for (k, i), refs in windows.items()]}
                     for symbol, (totals, windows) in baseline.items()},
    })


def prepare_baselines(session, days, symbols, q, directory):
    """Write one baseline.json per replay day from a single shared history aggregation."""
    days = sorted(dict.fromkeys(days))
    calendar = trading_dates(session, days[-1])
    missing = [day for day in days if day not in calendar]
    if missing:
        raise ValueError("Not DDB trading days: " + ", ".join(missing))
    first, last = calendar.index(days[0]), calendar.index(days[-1])
    history = calendar[max(0, first - 60):last]
    if len(history) < 60:
        raise ValueError("Need 60 DDB trading days before the earliest replay date")
    minutes = prior_minutes(session, history, symbols)
    daily = daily_turnover(session, history, symbols)
    for day in days:
        as_of = calendar[calendar.index(day) - 1]
        write_baseline(day, as_of, history, minutes, daily, q, symbols, Path(directory) / f"{day}.json")


def _etf_frame(session, day, symbols):
    return session.run("select MDDate,SecurityID,MDTime,LastPx,TotalVolumeTrade,TotalValueTrade from " + SNAPSHOT +
                       f" where MDDate={dot(day)} and SecurityID in {sorted(symbols)!r}")


def _futures_frame(session, day, codes):
    return session.run(
        "select TradeDate,SecurityID,TradeTime,LastPrice,Volume,Turnover,OpenInt,BidPrice,AskPrice from " + FUTURES +
        f" where TradeDate={dot(day)} and SecurityID in {sorted(codes)!r}")


def etf_rows(frame, day):
    """Normalize ETF snapshots into ``ingestable`` per-symbol CSV rows."""
    output = {}
    for row in frame.to_dict("records"):
        stamp = combined(row["MDDate"], row["MDTime"])
        last, cash = number(row["LastPx"]), number(row["TotalValueTrade"])
        if (stamp.date().isoformat() != day or not OPEN <= stamp.time() <= FINAL
                or last is None or last <= 0 or cash is None or cash < 0):
            continue
        symbol = str(row["SecurityID"]).strip()
        output.setdefault(symbol, []).append([
            symbol, stamp.isoformat(), last / PRICE_SCALE, cash / PRICE_SCALE,
            number(row["TotalVolumeTrade"])])
    for rows in output.values():
        rows.sort(key=lambda item: item[1])
    return output


def futures_rows(frame, day):
    """Normalize futures ticks; DDB already reports prices and amounts in CNY."""
    output = {}
    for row in frame.to_dict("records"):
        stamp = combined(row["TradeDate"], row["TradeTime"])
        last, cash = number(row["LastPrice"]), number(row["Turnover"])
        volume, oi = number(row["Volume"]), number(row["OpenInt"])
        if (stamp.date().isoformat() != day or not OPEN <= stamp.time() <= FINAL
                or any(value is None or value < 0 for value in (last, cash, volume, oi)) or last <= 0):
            continue
        code = str(row["SecurityID"]).strip()
        output.setdefault(code, []).append([
            code, stamp.isoformat(), last, cash, volume, oi,
            first_level(row.get("BidPrice")), first_level(row.get("AskPrice"))])
    for rows in output.values():
        rows.sort(key=lambda item: item[1])
    return output


def contracts_for(session, day, products):
    """Pick the nearest four listed contracts per product from the tick table."""
    frame = session.run(f"select distinct SecurityID from {FUTURES} where TradeDate={dot(day)}")
    codes = [str(value) for value in frame["SecurityID"].tolist()]
    result = {}
    for product in sorted(products):
        matched = sorted(code for code in codes if re.fullmatch(product + r"[0-9]{4}", code))
        if len(matched) < 4:
            raise ValueError(f"DDB has fewer than four {product} contracts on {day}")
        result[product] = matched[:4]
    return result


def write_csv(target, fields, rows):
    """Write one normalized Tick CSV atomically."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", newline="", dir=target.parent, delete=False) as file:
        writer = csv.writer(file)
        writer.writerow(["symbol", "datetime", *fields])
        writer.writerows(rows)
    Path(file.name).replace(target)


def write_inputs(session, day, symbols, products, directory):
    """Write contracts.json plus one CSV per ETF and futures contract."""
    directory = Path(directory)
    manifest = directory / "contracts.json"
    if manifest.is_file():
        cached = read(manifest)
        if cached["date"] != day:
            raise ValueError("Cached contract manifest is for another date")
        contracts = cached["products"]
    else:
        contracts = contracts_for(session, day, products)
        publish(manifest, {"date": day, "products": contracts})
    etf = etf_rows(_etf_frame(session, day, symbols), day)
    for symbol in symbols:
        target = directory / "etf" / (symbol + ".csv")
        if target.is_file():
            continue
        write_csv(target, ETF_FIELDS, etf.get(symbol, []))
        print("DDB ETF ticks", symbol, len(etf.get(symbol, [])), flush=True)
    codes = sorted(code for values in contracts.values() for code in values)
    futures = futures_rows(_futures_frame(session, day, codes), day) if codes else {}
    for code in codes:
        target = directory / "futures" / (code + ".csv")
        if target.is_file():
            continue
        write_csv(target, FUTURES_FIELDS, futures.get(code, []))
        print("DDB futures ticks", code, len(futures.get(code, [])), flush=True)


def fund_input(session, day, symbols):
    """Build fund-flow.json inputs from ``fund_etf_nr`` for the last two sessions."""
    history = trading_dates(session, day)
    if len(history) < 2:
        raise ValueError("Previous trading day is unavailable for fund flows")
    previous = history[-2]
    codes = {bare(symbol): symbol for symbol in sorted(symbols)}
    frame = session.run(
        "select TICKER_SYMBOL,TRADE_DATE,SHARES,NAV,SPLIT_RATIO,NET_INFLOW from " + FUNDS +
        f" where TICKER_SYMBOL in {sorted(codes)!r} and TRADE_DATE between {dot(previous)} : {dot(day)}")
    rows = [(codes[str(row["TICKER_SYMBOL"])], str(row["TRADE_DATE"])[:10], row)
            for row in frame.to_dict("records") if str(row["TICKER_SYMBOL"]) in codes]
    rows.sort(key=lambda item: (item[0], item[1]))
    records = {symbol: {"shares": [], "navs": [], "splits": [], "unverified_split_dates": [],
                        "split_source": "DDB SPLIT_RATIO with NET_INFLOW reconciliation"}
               for symbol in codes.values()}
    factors = {}
    for symbol, stamp, row in rows:
        ratio, expected = number(row["SPLIT_RATIO"]), number(row["NET_INFLOW"])
        if ratio is None and expected is None:
            records[symbol]["unverified_split_dates"].append(stamp)
        if ratio is not None and ratio <= 0:
            raise ValueError(f"Invalid DDB split ratio: {symbol} {stamp}")
        if ratio is not None and ratio != 1 and expected is None:
            raise ValueError(f"DDB split requires NET_INFLOW reconciliation: {symbol} {stamp}")
        factors[symbol] = factors.get(symbol, 1.0) * (ratio or 1.0)
        shares, nav = number(row["SHARES"]), number(row["NAV"])
        if shares is not None and shares > 0:
            records[symbol]["shares"].append([stamp, shares])
        if nav is not None and nav > 0:
            records[symbol]["navs"].append([stamp, nav, factors[symbol]])
        if ratio is not None and ratio > 0:
            records[symbol]["splits"].append([stamp, ratio])
    return {"date": day, "previous_date": previous, "provider": "DDB", "funds": records}


def main():
    """Write the baseline files needed by a multi-day DDB replay."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", required=True, help="Comma-separated replay dates")
    parser.add_argument("--out", type=Path, required=True, help="Directory for <date>.json baselines")
    parser.add_argument("--symbols", nargs="+", help="ETF codes; defaults to the core basket")
    parser.add_argument("--quantile", type=float, default=.75)
    args = parser.parse_args()
    days = [item.strip() for item in args.days.split(",") if item.strip()]
    session = connect()
    try:
        prepare_baselines(session, days, args.symbols or BASKET, args.quantile, args.out)
    finally:
        session.close()
    print(f"Wrote {len(days)} baselines into {args.out}")


if __name__ == "__main__":
    main()
