import contextlib
from datetime import datetime, timedelta, timezone
import io
import json
import math
import sys

CHINA = timezone(timedelta(hours=8))


def number(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def local_time(value):
    stamp = value.to_pydatetime() if hasattr(value, "to_pydatetime") else datetime.fromisoformat(str(value))
    return stamp.astimezone(CHINA) if stamp.tzinfo else stamp.replace(tzinfo=CHINA)


def bars(api, mapping, start, end, frequency):
    frame = api.get_price(
        list(mapping), start_date=start, end_date=end, frequency=frequency,
        fields=["close", "total_turnover"], adjust_type="none",
    )
    if frame is None or frame.empty:
        return []
    rows = []
    seen = set()
    for (code, stamp), row in zip(frame.index, frame.itertuples(index=False)):
        stamp = local_time(stamp)
        key = (code, stamp)
        if key in seen:
            raise ValueError("Duplicate bar")
        seen.add(key)
        rows.append([mapping[code], int(stamp.timestamp() * 1000), number(row.close), number(row.total_turnover)])
    return rows


def fund_history(symbols, start, end):
    from rqdatac_fund import fund

    output = {symbol: {"shares": [], "navs": [], "splits": []} for symbol in symbols}
    codes = {symbol.split(".")[0]: symbol for symbol in symbols}
    ids = list(codes)
    for i in range(0, len(ids), 40):
        batch = ids[i:i + 40]
        shares = fund.get_daily_units(batch, start_date=start, end_date=end)
        navs = fund.get_nav(batch, start_date=start, end_date=end,
                            fields=["unit_net_value", "adjusted_net_value"], expect_df=True)
        splits = fund.get_split(batch)
        if shares is not None:
            for (code, date), units in shares["units"].items():
                units = number(units)
                if units is not None and units > 0:
                    output[codes[code]]["shares"].append([str(date.date()), units])
        if navs is not None:
            for (code, date), row in zip(navs.index, navs.itertuples(index=False)):
                nav, adjusted = number(row.unit_net_value), number(row.adjusted_net_value)
                if nav is not None and nav > 0:
                    output[codes[code]]["navs"].append([str(date.date()), nav, adjusted / nav if adjusted else None])
        if splits is not None and not splits.empty:
            for (code, date), ratio in splits["split_ratio"].items():
                ratio = number(ratio)
                if ratio is not None and ratio > 0:
                    output[codes[code]]["splits"].append([str(date.date()), ratio])
    return {"funds": output}


def collect(api, request):
    if request["kind"] == "fund_history":
        return fund_history(request["symbols"], request["start"], request["end"])
    mapping = {symbol.replace(".SH", ".XSHG").replace(".SZ", ".XSHE"): symbol for symbol in request["symbols"]}
    if request["kind"] == "history":
        start, end = request["start"], request["end"]
        calendar = [str(day) for day in api.get_trading_dates(start, end)]
        # Publish completed sessions only. A same-day bar can still be provisional.
        last_closed = str(datetime.now(CHINA).date() - timedelta(days=1))
        daily_end = min(end, last_closed)
        daily = bars(api, mapping, start, daily_end, "1d") if start <= daily_end else []
        minute_days = request["minute_days"]
        minute_dates = [day for day in calendar if request["market_start"] <= day < end and day <= last_closed]
        minutes = bars(api, {code: symbol for code, symbol in mapping.items() if symbol != "000001.SH"},
                       minute_dates[-minute_days:][0], minute_dates[-1], "1m") if minute_days and minute_dates else []
        return {"calendar": calendar, "daily": daily, "minutes": minutes}

    now = local_time(request["as_of"])
    day = now.date()
    calendar = [str(date) for date in api.get_trading_dates(day - timedelta(days=14), day)]
    if str(day) not in calendar:
        return {"calendar": calendar}
    minutes = [row for row in bars(api, mapping, day, day, "1m") if row[1] <= now.timestamp() * 1000]
    prev = bars(api, mapping, calendar[-2], calendar[-2], "1d")
    if request["kind"] == "replay":
        daily = bars(api, mapping, day, day, "1d")
        return {"calendar": calendar, "minutes": minutes, "daily": daily, "previous": prev}
    ticks = api.current_snapshot(list(mapping))
    daily = []
    for tick in ticks or []:
        if tick is None:
            continue
        stamp = local_time(tick.datetime)
        if stamp.date() == day and stamp <= now + timedelta(seconds=5):
            daily.append([mapping[tick.order_book_id], int(stamp.timestamp() * 1000), number(tick.last), number(tick.total_turnover)])
    return {"calendar": calendar, "minutes": minutes, "daily": daily, "previous": prev}


def main():
    stage = "sdk_missing"
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            import rqdatac
            request = json.loads(sys.argv[1])
            stage = "authentication"
            rqdatac.init(connect_timeout=8, timeout=40)
            stage = "query_failed"
            result = collect(rqdatac, request)
    except Exception:
        result = {"error": stage}
    print(json.dumps(result, allow_nan=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
