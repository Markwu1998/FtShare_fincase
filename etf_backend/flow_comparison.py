"""Import daily ETF direction research into the read-only dashboard archive."""
import argparse
import csv
from datetime import date, datetime
from pathlib import Path
import sys
import time

from .collect import ROOT, publish, read
from .model import BASKET, finite

DEFAULT_PATH = ROOT / "data/private/etf-flow-comparison.json"
SEED_PATH = ROOT / "data/private/rqdata-seed.json"
DDB_SEED_PATH = ROOT / "data/private/ddb-seed.json"
REPLAY_ROOT = ROOT / "data/private/flow-replay"
SEED_PATHS = (DDB_SEED_PATH, SEED_PATH)
METHOD = "rqdata-l1-midpoint-band-v1"
BAND = .1  # Direction band as a fraction of the quoted spread.
COLUMNS = ["day", "symbol", "gross_yi", "active_buy_yi", "active_sell_yi", "net_flow_yi", "flow_issues"]


def validate_archive(value):
    if value["schema_version"] != 1 or value["unit"] != "CNY_1e8" or value["method"] != METHOD:
        raise ValueError("Incompatible ETF flow comparison archive")
    seen = set()
    dates = set()
    if not value["rows"]:
        raise ValueError("ETF flow comparison is empty")
    for row in value["rows"]:
        day, symbol = row["day"], row["symbol"]
        key = day, symbol
        if date.fromisoformat(day).isoformat() != day or symbol not in BASKET or key in seen:
            raise ValueError(f"Invalid or duplicate ETF-day: {day} {symbol}")
        seen.add(key)
        dates.add(day)
        buy, sell, flow, gross = (row[field] for field in ("active_buy_yi", "active_sell_yi", "net_flow_yi", "gross_yi"))
        if (not finite(gross) or gross < 0 or (buy is None and (sell is not None or gross == 0))
                or (buy is not None and (not finite(buy) or not finite(sell)
                                         or min(buy, sell) < 0 or buy + sell > gross + 1e-8))
                or (flow is not None and not finite(flow)) or not isinstance(row["flow_issues"], str)):
            raise ValueError(f"Invalid comparison amounts: {day} {symbol}")
    if len(seen) != len(dates) * len(BASKET):
        raise ValueError("Every covered date must contain all configured core ETFs")
    return value


def import_rows(source):
    with source.open(encoding="utf-8-sig", newline="") as stream:
        rows = [{"day": row["day"], "symbol": row["symbol"], "flow_issues": row["flow_issues"],
                 **{field: float(row[field]) if row[field] else None
                    for field in ("gross_yi", "active_buy_yi", "active_sell_yi", "net_flow_yi")}}
                for row in csv.DictReader(stream)]
    rows.sort(key=lambda row: (row["day"], row["symbol"]))
    return validate_archive({"schema_version": 1, "method": METHOD, "unit": "CNY_1e8", "rows": rows})


def archive_from_rows(rows):
    rows = sorted(rows, key=lambda row: (row["day"], row["symbol"]))
    return validate_archive({"schema_version": 1, "method": METHOD, "unit": "CNY_1e8", "rows": rows})


def seed_core(day):
    """Per-symbol daily net flow in CNY for *day*; DDB is preferred."""
    for path in SEED_PATHS:
        try:
            value = read(path)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        core = value.get("core_fund_daily") or {}
        if core.get("date") != day:
            continue
        rows = {row["symbol"]: row["flow"] * 1e8 for row in core.get("rows") or []
                if row.get("flow") is not None and finite(row["flow"])}
        if len(rows) == len(BASKET):
            return rows
    return {}


def target_day(wait=0):
    """Return yesterday's flow date once all core ETF shares/NAV are present."""
    deadline = time.monotonic() + max(wait, 0)
    today = datetime.now().date().isoformat()
    last_error = None
    while True:
        for seed_path in SEED_PATHS:
            try:
                value = read(seed_path)
                core = value.get("core_fund_daily") or {}
                day = core.get("date")
                rows = core.get("rows") or []
                flows = [row for row in rows if row.get("flow") is not None and finite(row["flow"])]
                if day and day < today and len(flows) == len(BASKET):
                    return day
                last_error = f"{seed_path.name} core fund flow {day or '无'}，有效 {len(flows)}/{len(BASKET)}"
            except (OSError, ValueError, KeyError, TypeError) as error:
                last_error = f"{seed_path.name} 未就绪：{type(error).__name__}"
        if time.monotonic() >= deadline:
            raise ValueError("上一交易日份额/净值未就绪：" + (last_error or "无数据"))
        time.sleep(15)


def merge_archive(rows):
    existing = {}
    if DEFAULT_PATH.exists():
        old = read(DEFAULT_PATH)
        validate_archive(old)
        existing = {(row["day"], row["symbol"]): row for row in old["rows"]}
    for row in rows:
        existing[row["day"], row["symbol"]] = row
    return archive_from_rows(list(existing.values()))


def clock_label(second):
    """Return the ``HH:MM`` minute that contains a second-of-day value."""
    minute = int(second) // 60
    return f"{minute // 60:02d}:{minute % 60:02d}"


def window_turnover(report, symbol):
    """Confirmed-window minutes and their summed turnover in CNY for one symbol."""
    rows = [row for row in (report.get("accounting") or {}).get("minutes") or [] if row[0] == symbol]
    return {row[1] for row in rows}, sum(row[2] for row in rows)


def direction_split(rows, minutes):
    """Split window turnover into estimated active buy and sell amounts (CNY)."""
    total = buy = sell = 0.0
    previous = None
    for second, cash, volume, bid, ask in rows:
        if previous is not None and clock_label(second) in minutes:
            delta_cash = cash - previous[1]
            delta_volume = volume - previous[2]
            if delta_cash > 0 and delta_volume > 0:
                amount = delta_cash / 10000  # DDB turnover is stored ×10000.
                total += amount
                first_bid, first_ask = previous[3], previous[4]
                if first_bid and first_ask and first_ask >= first_bid:
                    average = delta_cash / delta_volume
                    middle = (first_bid + first_ask) / 2
                    band = BAND * (first_ask - first_bid)
                    if average > middle + band:
                        buy += amount
                    elif average < middle - band:
                        sell += amount
        previous = (second, cash, volume, bid, ask)
    return total, buy, sell


def day_quotes(session, day, symbols):
    """DDB snapshots with first-level quotes, keyed by symbol."""
    from .ddb_replay import (FINAL_SECOND, OPEN_SECOND, SNAPSHOT, dot, number)

    frame = session.run(
        "select MDDate,MDTime,SecurityID,TotalValueTrade,TotalVolumeTrade,BuyPriceQueue[0] as BidPx,"
        "SellPriceQueue[0] as AskPx from " + SNAPSHOT +
        f" where MDDate={dot(day)} and SecurityID in {sorted(symbols)!r}")
    output = {}
    for stamp, symbol, cash, volume, bid, ask in zip(
            frame["MDTime"], frame["SecurityID"], frame["TotalValueTrade"],
            frame["TotalVolumeTrade"], frame["BidPx"], frame["AskPx"]):
        second = stamp.hour * 3600 + stamp.minute * 60 + stamp.second + stamp.microsecond / 1e6
        cash, volume = number(cash), number(volume)
        if cash is None or cash < 0 or volume is None or not OPEN_SECOND <= second <= FINAL_SECOND:
            continue
        output.setdefault(str(symbol), []).append((second, cash, volume, number(bid), number(ask)))
    for rows in output.values():
        rows.sort()
    return output


def replay_reports(root):
    """Replay reports keyed by date, skipping dates without every core ETF."""
    root = Path(root)
    reports = {}
    for path in sorted([*root.glob("replay/????-??-??.json"), *root.glob("*/replay/????-??-??.json")]):
        value = read(path)
        if value.get("date") != path.stem or not set(BASKET).issubset(value.get("symbols") or []):
            print(f"Skip {path}: replay does not cover every core ETF", file=sys.stderr)
            continue
        reports[value["date"]] = value
    return reports


def build_rows(root, session=None):
    """Build the ETF-day CSV rows from day-replay archives and DDB quotes."""
    from .ddb_replay import connect

    reports = replay_reports(root)
    if not reports:
        raise ValueError("No complete day-replay archive found under " + str(root))
    owned = session is None
    session = session or connect()
    rows = []
    try:
        for day in sorted(reports):
            report, quotes = reports[day], day_quotes(session, day, BASKET)
            flow = (report.get("daily_flow") or {}).get("by_symbol") or {}
            seed = seed_core(day)
            for symbol in BASKET:
                minutes, gross = window_turnover(report, symbol)
                total, buy, sell = direction_split(quotes.get(symbol, []), minutes) if minutes else (0, 0, 0)
                item = flow.get(symbol) or {}
                value = item.get("flow_cny")
                issues = ";".join(item.get("issues") or ()) if item else "flow_missing"
                if value is None and symbol in seed:
                    value = seed[symbol]
                    issues = ""
                rows.append({
                    "day": day, "symbol": symbol,
                    "gross_yi": max(gross, total) / 1e8,
                    "active_buy_yi": buy / 1e8, "active_sell_yi": sell / 1e8,
                    "net_flow_yi": value / 1e8 if finite(value) else None,
                    "flow_issues": issues,
                })
    finally:
        if owned:
            session.close()
    return rows


def write_rows(path, rows):
    """Write comparison rows in the layout accepted by ``import_rows``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({**row,
                             "gross_yi": round(row["gross_yi"], 6),
                             "active_buy_yi": round(row["active_buy_yi"], 6),
                             "active_sell_yi": round(row["active_sell_yi"], 6),
                             "net_flow_yi": "" if row["net_flow_yi"] is None else round(row["net_flow_yi"], 6)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, nargs="?", help="ETF-day CSV to import")
    parser.add_argument("--from-replay", type=Path, help="Build the ETF-day CSV from day-replay outputs")
    parser.add_argument("--out", type=Path, help="Output CSV path used with --from-replay")
    parser.add_argument("--day", type=lambda value: date.fromisoformat(value).isoformat(),
                        help="Target trading day for --merge")
    parser.add_argument("--wait", type=int, default=0,
                        help="Seconds to wait for prior-day shares/NAV before building")
    parser.add_argument("--merge", action="store_true",
                        help="Merge the target day into the existing dashboard archive")
    args = parser.parse_args()
    try:
        if args.merge:
            target = args.day or target_day(args.wait)
            root = args.from_replay or REPLAY_ROOT / target
            if not root.is_dir():
                raise ValueError(f"{target} replay 未生成：{root}")
            rows = [row for row in build_rows(root) if row["day"] == target]
            if len(rows) != len(BASKET):
                raise ValueError(f"{target} replay 覆盖 {len(rows)}/{len(BASKET)} 只核心 ETF")
            missing = [row["symbol"] for row in rows if not finite(row["net_flow_yi"])]
            if missing:
                raise ValueError(f"{target} 日末净申赎 pending：{','.join(missing[:5])}...")
            value = merge_archive(rows)
            publish(DEFAULT_PATH, value)
            print(f"Merged {target}: {len(rows)} ETF-days; archive {len(value['rows'])} rows")
            return
        if args.from_replay:
            rows = build_rows(args.from_replay)
            target = args.out or ROOT / ".local" / f"etf-flow-{rows[0]['day']}-{rows[-1]['day']}.csv"
            write_rows(target, rows)
            print(f"Built {len(rows)} ETF-days into {target}")
            return
        if args.csv is None:
            parser.error("Supply the CSV to import, or use --from-replay")
        value = import_rows(args.csv)
        publish(DEFAULT_PATH, value)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"ETF flow comparison failed: {error}\n")
    print(f"Imported {len(value['rows'])} ETF-days into {DEFAULT_PATH}")


if __name__ == "__main__":
    main()
