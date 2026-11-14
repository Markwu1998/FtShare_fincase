"""Publish validated RQData history and live snapshots without Node.js."""
import argparse
import contextlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import sleep
import io

from . import provider
from .model import (BASKET, CATEGORIES, COLORS, FLOW_POLICY, FORMULA, INDEX, SLOTS, before, eligible, finite,
                    fund_flows, iso_utc, minute_basket, stamp, trading_minute)

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def publish(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
        pending = Path(file.name)
        try:
            json.dump(value, file, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except Exception:
            file.close()
            pending.unlink()
            raise
    try:
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


def history_snapshot(market, records, universe, config, end, closes=None):
    funds = universe["funds"]
    members = {f["symbol"]: f for f in funds}
    if len(members) != len(funds) or {f["symbol"] for f in funds if f.get("is_core_basket")} != set(BASKET):
        raise ValueError("Invalid core ETF membership")
    calendar = market["calendar"]
    if len(calendar) < 2 or calendar != sorted(set(calendar)):
        raise ValueError("Invalid trading calendar")
    flow_start, market_start = config["flow_start"], config["market_start"]
    by_fund = {symbol: fund_flows(fund, records[symbol], calendar) for symbol, fund in members.items()}
    broad = {"dates": [], "cats": CATEGORIES, "colors": COLORS, "series": {c: [] for c in CATEGORIES},
             "coverage": {c: {"valid": [], "expected": []} for c in CATEGORIES}, "total": [], "issues": {}}
    metrics = {name: {"total": [], "series": {c: [] for c in CATEGORIES},
                      "valid": {c: [] for c in CATEGORIES}, "latest": None, "members": []}
               for name in ("size", "premium")}
    core = {}
    for day in calendar[1:]:
        if day < min(flow_start, market_start):
            continue
        present = {symbol: by_fund[symbol][day] for symbol, fund in members.items() if eligible(fund) <= day}
        core_rows = [present[s] for s in BASKET if s in present]
        values = [r["flow"] for r in core_rows if finite(r["flow"])]
        core[day] = (sum(values) if values and len(values) == len(core_rows) else None, len(values), len(core_rows))
        if day < flow_start:
            continue
        broad["dates"].append(day)
        categories = {c: [] for c in CATEGORIES}
        valid = {c: [] for c in CATEGORIES}
        details = {name: [] for name in metrics}
        daily_closes = (closes or {}).get(day, {})
        for symbol, row in present.items():
            fund = members[symbol]
            bucket = fund["bucket"]
            categories[bucket].append(symbol)
            units, nav, close = row["shares"], row["nav"], daily_closes.get(symbol)
            values = {
                "size": units * nav / 1e8 if finite(units) and finite(nav) and nav > 0 else None,
                "premium": (close / nav - 1) * 100 if finite(close) and finite(nav) and nav > 0 else None,
            }
            for name, value in values.items():
                if value is not None:
                    details[name].append({"symbol": symbol, "category": bucket, "value": value})
            if finite(row["flow"]):
                valid[bucket].append(row["flow"])
            else:
                broad["issues"].setdefault(day, []).append({"symbol": symbol, "name": fund["name"], "bucket": bucket,
                                                            "reasons": row["issues"]})
        totals = []
        for bucket in CATEGORIES:
            expected, count = len(categories[bucket]), len(valid[bucket])
            total = sum(valid[bucket]) if expected == count else None
            broad["coverage"][bucket]["expected"].append(expected)
            broad["coverage"][bucket]["valid"].append(count)
            broad["series"][bucket].append(total)
            totals.append(total)
        broad["total"].append(sum(totals) if all(finite(x) for x in totals) else None)
        for name, metric in metrics.items():
            grouped = {c: [] for c in CATEGORIES}
            for row in details[name]:
                grouped[row["category"]].append(row["value"])
            for bucket, values in grouped.items():
                metric["valid"][bucket].append(len(values))
                complete = len(values) == len(categories[bucket])
                metric["series"][bucket].append(
                    (sum(values) if complete and values else None) if name == "size"
                    else (sum(values) / len(values) if values else None))
            values = details[name]
            metric["total"].append(
                (sum(r["value"] for r in values) if values and len(values) == len(present) else None)
                if name == "size" else (sum(r["value"] for r in values) / len(values) if values else None))
            if values:
                metric["latest"] = day
                metric["members"] = values

    cash, index = {}, {}
    for symbol, ms, close, amount in market["daily"]:
        day = stamp(ms).date().isoformat()
        if symbol == INDEX:
            if finite(close):
                index[day] = close
        elif finite(amount) and amount >= 0:
            cash.setdefault(day, {})[symbol] = amount / 1e8
    dates = [d for d in calendar if d >= min(flow_start, market_start)]
    amounts, minutes = {}, {}
    for day in dates:
        expected = [s for s in BASKET if eligible(members[s]) <= day]
        row = cash.get(day, {})
        amounts[day] = sum(row[s] for s in expected) if day >= market_start and expected and all(s in row for s in expected) else None
    groups = {}
    for row in market["minutes"]:
        groups.setdefault(stamp(row[1]).date().isoformat(), []).append(row)
    limit = config["minute_days"]
    minute_dates = [d for d in dates if market_start <= d < end]
    for day in minute_dates[-limit:] if limit else ():
        if any(eligible(members[s]) > day for s in BASKET):
            continue
        amounts_by_minute, _, totals = minute_basket(groups.get(day, []))
        minutes[day] = {"bars": amounts_by_minute, "cumulative": totals}
        if totals[-1] is None or amounts[day] is None or abs(totals[-1] - amounts[day]) > 1e-6:
            minutes[day]["quality"] = "incomplete"
    latest = next((d for d in reversed(broad["dates"]) if d >= market_start and core[d][0] is not None
                   and core[d][1] == len(BASKET)), None)
    if not (latest and index and any(finite(v) for v in amounts.values())
            and (not limit or any(not row.get("quality") for row in minutes.values()))):
        raise ValueError("RQData data did not pass snapshot coverage checks")
    built = iso_utc(datetime.now(timezone.utc))
    coverage = [core.get(d, (None, 0, len(BASKET))) for d in dates]
    return {"schema": 1, "provider": "RQData", "sources": ["RQData"], "built_at": built, "formula": FORMULA,
            "flow_policy": FLOW_POLICY, "universe_policy": {"policy": universe["policy"], "eligibility": "max(inception_date, listed_date)"},
            "universe": funds, "categories": CATEGORIES, "colors": COLORS, "calendar": calendar, "minute_slots": SLOTS,
            "history": {"dates": dates, "amount": [amounts[d] for d in dates], "idx": [index.get(d) for d in dates],
                        "netflow": [r[0] if d >= market_start else None for d, r in zip(dates, coverage)],
                        "flow_coverage": [r[1] for r in coverage], "flow_expected": [r[2] for r in coverage]},
            "broad": broad, "broad_metrics": metrics, "minutes": minutes, "latest_flow_date": latest, "latest_market_date": max(index),
            "core_fund_daily": {"date": latest, "captured_at": built,
                                "rows": [{"symbol": s, **by_fund[s][latest]} for s in BASKET]},
            "source_audit": {"provider": "RQData", "fund_provider": "RQData",
                             "missing_days": sum(d <= latest and value is None for d, value in zip(broad["dates"], broad["total"]))}}


def live_snapshot(result, funds, date, replay):
    if date not in result["calendar"]:
        return None
    rows = [row for row in result["minutes"] if stamp(row[1]).date().isoformat() == date]
    if any(symbol in BASKET and finite(amount) and amount > 0 and stamp(ms).strftime("%H:%M") not in SLOTS
           for symbol, ms, _, amount in rows):
        raise ValueError("RQData minute axis mismatch")
    bars, coverage, cumulative = minute_basket(rows)
    index = {stamp(ms).strftime("%H:%M"): close for symbol, ms, close, _ in rows if symbol == INDEX}
    previous = {s: close for s, _, close, _ in result["previous"]}
    quotes = {s: (ms, close, amount) for s, ms, close, amount in result["daily"]}
    complete = [i for i, slot in enumerate(SLOTS) if finite(cumulative[i]) and finite(index.get(slot))]
    if not complete or not finite(previous.get(INDEX)):
        return None
    names = {row["symbol"]: row["name"] for row in funds}
    table = []
    for symbol in BASKET:
        ms, close, amount = quotes.get(symbol, (None, None, None))
        if not (finite(close) and finite(amount) and amount >= 0):
            return None
        prior = previous.get(symbol)
        table.append({"symbol": symbol, "code": symbol[:6], "name": names[symbol], "price": close, "amount": amount / 1e8,
                      "pct": (close / prior - 1) * 100 if finite(prior) and prior > 0 else None})
    minute = SLOTS[complete[-1]]
    end = datetime.fromisoformat(date + "T" + minute).replace(tzinfo=provider.CHINA).timestamp()
    observed = end if replay else min(end, *(quotes[s][0] / 1000 for s in BASKET))
    value = {"provider": "RQData", "formula": FORMULA, "warnings": [], "date": date, "slots": SLOTS,
             "fetched_at": iso_utc(datetime.fromtimestamp(observed, timezone.utc)), "bars": bars,
             "coverage": coverage, "closes": [index.get(s) for s in SLOTS], "pre_close": previous[INDEX],
             "cumulative": cumulative, "table": table, "amount": sum(row["amount"] for row in table),
             "last_complete_minute": minute, "expected_flow_date": result["calendar"][-2],
             "trading_dates": result["calendar"], "daily_batches": 0}
    if replay:
        value["replay"] = True
    return value


def live_run(api, funds, path, replay=None):
    now = datetime.now(provider.CHINA)
    if not replay and not trading_minute(now):
        print("outside_trading_session", flush=True)
        return
    date = replay or now.date().isoformat()
    as_of = replay + "T15:05:00" if replay else now.isoformat()
    key = "live:rqdata:snapshot"
    cache = read(path) if Path(path).exists() else {"schema": 1, "entries": {}}
    prior = json.loads(cache["entries"].get(key, "null")) or {}
    checked = iso_utc(datetime.now(timezone.utc))
    try:
        rows = provider.collect(api, {"kind": "replay" if replay else "live", "as_of": as_of, "symbols": BASKET + [INDEX]})
        value = live_snapshot(rows, funds, date, replay)
        old = prior.get("value", {})
        if not value or (not replay and old.get("date") == date and
                         (old["last_complete_minute"] > value["last_complete_minute"] or old["fetched_at"] > value["fetched_at"])):
            raise ValueError("incomplete_snapshot")
    except Exception:
        if prior and not replay:
            cache["entries"][key] = json.dumps({**prior, "checked_at": checked, "last_error": "upstream_error"})
            publish(path, cache)
        raise
    cache["entries"][key] = json.dumps({"value": value, "checked_at": checked}, ensure_ascii=False)
    publish(path, cache)
    print(json.dumps({"date": date, "through": value["last_complete_minute"], "replay": bool(replay)}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["history", "live", "crowding"])
    parser.add_argument("--end", type=lambda s: datetime.fromisoformat(s).date().isoformat(),
                        default=datetime.now(provider.CHINA).date().isoformat())
    parser.add_argument("--seed", default=os.environ.get("FTSHARE_SEED_FILE", "data/private/rqdata-seed.json"))
    parser.add_argument("--cache", default=os.environ.get("FTSHARE_LIVE_CACHE_FILE", "data/private/rqdata-live.json"))
    parser.add_argument("--config", default=os.environ.get("MARKET_DATA_CONFIG_FILE"))
    parser.add_argument("--start", type=lambda s: datetime.fromisoformat(s).date().isoformat())
    parser.add_argument("--crowding-file", default=os.environ.get("CROWDING_FILE"))
    parser.add_argument("--crowding-module", choices=["daily", "industry", "funds", "financing"], default="daily")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--replay", type=lambda s: datetime.fromisoformat(s).date().isoformat())
    args = parser.parse_args()
    if args.replay and (args.mode != "live" or args.watch or Path(args.cache).resolve() ==
                        Path(os.environ.get("FTSHARE_LIVE_CACHE_FILE", "data/private/rqdata-live.json")).resolve()):
        parser.error("Replay requires live mode and an explicit separate --cache")
    try:
        import rqdatac
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rqdatac.init(connect_timeout=8, timeout=40)
        if args.mode == "crowding":
            from .crowding import DEFAULT_PATH, update
            from .crowding_extra import update_extra
            path = ROOT / (args.crowding_file or DEFAULT_PATH)
            snapshot = (update(rqdatac, path, args.start, args.end) if args.crowding_module == "daily" else
                        update_extra(rqdatac, args.crowding_module, path, args.start, args.end))
            print(json.dumps({"days": len(snapshot["rows"]), "through": snapshot["rows"][-1]["date"]}))
            return
        universe = read(ROOT / "config/universe.json")
        if args.mode == "history":
            previous = read(args.seed) if Path(args.seed).is_file() else None
            config = read(ROOT / "config/collection.json")
            if args.config:
                config.update(read(args.config))
            if config.get("provider", "RQData") != "RQData" or config.get("fund_history") is False or (
                    type(config["minute_days"]) is not int or config["minute_days"] < 0):
                raise ValueError("Invalid RQData collection configuration")
            recoveries = {fund["symbol"]: fund["flow_evidence"] for fund in (previous or {}).get("universe", [])
                          if fund.get("flow_evidence")}
            overrides = ROOT / "data/private/flow-recoveries.json"
            if overrides.is_file():
                recoveries.update(read(overrides)["funds"])
            for fund in universe["funds"]:
                if fund["symbol"] in recoveries:
                    fund["flow_evidence"] = recoveries[fund["symbol"]]
            start = before(min(config["flow_start"], config["market_start"]), 14)
            market = provider.collect(rqdatac, {"kind": "history", "symbols": BASKET + [INDEX], "start": start,
                                                "end": args.end, "market_start": config["market_start"],
                                                "minute_days": config["minute_days"]})
            funds = provider.fund_history([f["symbol"] for f in universe["funds"]], start, args.end)["funds"]
            close_end = min(args.end, before(datetime.now(provider.CHINA).date().isoformat(), 1))
            closes = provider.traded_closes(rqdatac, list(funds), config["flow_start"], close_end)
            snapshot = history_snapshot(market, funds, universe, config, args.end, closes)
            if any(metric["latest"] is None for metric in snapshot["broad_metrics"].values()):
                raise ValueError("RQData broad metrics contain no valid data")
            if previous is not None:
                validate_replacement(previous, snapshot)
            publish(args.seed, snapshot)
            print(json.dumps({"market_through": snapshot["latest_market_date"], "flow_through": snapshot["latest_flow_date"],
                              "minute_days": len(snapshot["minutes"])}))
        else:
            while True:
                try:
                    live_run(rqdatac, universe["funds"], args.cache, args.replay)
                except Exception:
                    if not args.watch:
                        raise
                    print("RQData live collection failed; retaining last good snapshot", flush=True)
                if not args.watch:
                    break
                sleep(60)
    except Exception as error:
        parser.exit(1, f"RQData {args.mode} failed ({type(error).__name__}). Check configuration and data coverage.\n")


def validate_replacement(previous, current):
    if (previous.get("provider"), previous.get("formula"), previous.get("minute_slots")) != (
            current["provider"], current["formula"], current["minute_slots"]):
        raise ValueError("Incompatible previous snapshot")
    def members(seed):
        return sorted((f["symbol"], f["bucket"], f["is_core_basket"], eligible(f)) for f in seed["universe"])
    if members(previous) != members(current):
        raise ValueError("Replacement changes configured historical membership")
    for name, columns in (("history", ["amount", "idx", "netflow"]), ("broad", ["total"])):
        old, new = previous[name], current[name]
        for column in columns:
            old_days = {d for d, v in zip(old["dates"], old[column]) if finite(v)}
            new_days = {d for d, v in zip(new["dates"], new[column]) if finite(v)}
            if not old_days <= new_days:
                raise ValueError("Replacement would lose previously valid historical data")
    for column in CATEGORIES:
        old, new = previous["broad"], current["broad"]
        old_days = {d for d, v in zip(old["dates"], old["series"][column]) if finite(v)}
        new_days = {d for d, v in zip(new["dates"], new["series"][column]) if finite(v)}
        if not old_days <= new_days:
            raise ValueError("Replacement would lose previously valid category data")
    for name, prior in previous.get("broad_metrics", {}).items():
        metric = current["broad_metrics"][name]
        for old, new in [(prior["total"], metric["total"])] + [
                (prior["series"][c], metric["series"][c]) for c in CATEGORIES]:
            old_days = {d for d, v in zip(previous["broad"]["dates"], old) if finite(v)}
            new_days = {d for d, v in zip(current["broad"]["dates"], new) if finite(v)}
            if not old_days <= new_days:
                raise ValueError("Replacement would lose previously valid broad metrics")
    for day, row in current["minutes"].items():
        if row.get("quality") and day in previous["minutes"] and not previous["minutes"][day].get("quality"):
            current["minutes"][day] = previous["minutes"][day]
    current["minutes"] = {**previous["minutes"], **current["minutes"]}


if __name__ == "__main__":
    main()
