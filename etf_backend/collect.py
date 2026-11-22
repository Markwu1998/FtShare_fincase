"""Publish validated RQData history and live snapshots without Node.js."""
import argparse
import contextlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
import io

from . import provider
from .model import (BASKET, CATEGORIES, COLORS, FLOW_POLICY, FORMULA, INDEX, SLOTS, before, eligible, finite,
                    fund_flows, iso_utc, minute_basket, stamp, trading_minute)
from .sources import build_source, load_config as load_source_config, source_capabilities, source_entry

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


def history_snapshot(market, records, universe, config, end, closes=None, source="RQData"):
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
        present = {symbol: by_fund[symbol][day] for symbol, fund in members.items()
                   if eligible(fund) <= day and (not fund.get("delisted_date") or day < fund["delisted_date"])}
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
            first_day = day == eligible(fund)
            reasons = row.get("issues") or []
            # A fund's first session has no attributable share change yet. Keep it
            # in size/premium metrics but do not let it blank the category flow.
            baseline_missing = "listing_baseline_missing" in reasons or (
                first_day and "previous_share_missing" in reasons)
            if not baseline_missing:
                categories[bucket].append(symbol)
            units, nav, close = row["shares"], row["nav"], daily_closes.get(symbol)
            values = {
                "size": units * nav / 1e8 if finite(units) and finite(nav) and nav > 0 else None,
                "premium": (close / nav - 1) * 100 if finite(close) and finite(nav) and nav > 0 else None,
            }
            for name, value in values.items():
                if value is not None:
                    details[name].append({"symbol": symbol, "category": bucket, "value": value})
            if baseline_missing:
                continue
            if finite(row["flow"]):
                valid[bucket].append(row["flow"])
            else:
                broad["issues"].setdefault(day, []).append({"symbol": symbol, "name": fund["name"], "bucket": bucket,
                                                            "reasons": reasons})
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
    source_id = "ddb" if source == "DDB" else "rqdata"
    return {"schema": 1, "provider": source, "sources": [source], "built_at": built, "formula": FORMULA,
            "source": {"id": source_id, "label": source, "kind": "daily", "capabilities": [],
                       "realtime_source": {"id": "rqdata", "label": "RQData"},
                       "built_at": built, "latest_market_date": max(index), "latest_flow_date": latest,
                       "readiness": "ready"},
            "flow_policy": FLOW_POLICY, "universe_policy": {"policy": universe["policy"], "eligibility": "max(inception_date, listed_date)"},
            "universe": funds, "categories": CATEGORIES, "colors": COLORS, "calendar": calendar, "minute_slots": SLOTS,
            "history": {"dates": dates, "amount": [amounts[d] for d in dates], "idx": [index.get(d) for d in dates],
                        "netflow": [r[0] if d >= market_start else None for d, r in zip(dates, coverage)],
                        "flow_coverage": [r[1] for r in coverage], "flow_expected": [r[2] for r in coverage]},
            "broad": broad, "broad_metrics": metrics, "minutes": minutes, "latest_flow_date": latest, "latest_market_date": max(index),
            "core_fund_daily": {"date": latest, "captured_at": built,
                                "rows": [{"symbol": s, **by_fund[s][latest]} for s in BASKET]},
            "source_audit": {"provider": source, "fund_provider": source, "daily_source": source,
                             "realtime_source": "RQData",
                             "missing_days": sum(d <= latest and value is None for d, value in zip(broad["dates"], broad["total"]))}}


def live_snapshot(result, funds, date, replay, close=False, partial=False):
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
    if (not complete and not partial) or not finite(previous.get(INDEX)):
        return None
    names = {row["symbol"]: row["name"] for row in funds}
    table = []
    for symbol in BASKET:
        ms, close, amount = quotes.get(symbol, (None, None, None))
        if not (finite(close) and finite(amount) and amount >= 0) and not partial:
            return None
        prior = previous.get(symbol)
        table.append({"symbol": symbol, "code": symbol[:6], "name": names[symbol], "price": close,
                      "amount": amount / 1e8 if finite(amount) and amount >= 0 else None,
                      "quote_at": iso_utc(stamp(ms)) if ms is not None else None,
                      "pct": (close / prior - 1) * 100 if finite(close) and finite(prior) and prior > 0 else None})
    minute = SLOTS[complete[-1]] if complete else None
    end = datetime.fromisoformat(date + "T" + (minute or "09:30")).replace(tzinfo=provider.CHINA).timestamp()
    observed = end
    value = {"provider": "RQData", "formula": FORMULA, "warnings": [], "date": date, "slots": SLOTS,
             "fetched_at": iso_utc(datetime.fromtimestamp(observed, timezone.utc)), "bars": bars,
             "coverage": coverage, "closes": [index.get(s) for s in SLOTS], "pre_close": previous[INDEX],
             "cumulative": cumulative, "table": table,
             "amount": sum(row["amount"] for row in table) if all(finite(row["amount"]) for row in table) else None,
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
    parser.add_argument("mode", choices=["history", "live", "crowding", "burst-replay"])
    parser.add_argument("--end", type=lambda s: datetime.fromisoformat(s).date().isoformat(),
                        default=datetime.now(provider.CHINA).date().isoformat())
    parser.add_argument("--seed", default=None)
    parser.add_argument("--source", choices=["rqdata", "ddb"], default=os.environ.get("FTSHARE_DAILY_SOURCE", "rqdata"))
    parser.add_argument("--source-config", default=os.environ.get("FTSHARE_SOURCE_CONFIG"))
    parser.add_argument("--cache", default=os.environ.get("FTSHARE_LIVE_CACHE_FILE", "data/private/rqdata-live.json"))
    parser.add_argument("--config", default=os.environ.get("MARKET_DATA_CONFIG_FILE"))
    parser.add_argument("--start", type=lambda s: datetime.fromisoformat(s).date().isoformat())
    parser.add_argument("--crowding-file", default=os.environ.get("CROWDING_FILE"))
    parser.add_argument("--crowding-module", choices=["daily", "industry", "funds", "financing"], default="daily")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--close", action="store_true",
                        help="One-shot end-of-day snapshot: rebuild today's full minute cache without the replay flag")
    parser.add_argument("--recent", action="store_true",
                        help="Re-collect the recent overlap window and merge it into the existing history snapshot")
    parser.add_argument("--burst-dir", default=os.environ.get("ETF_BURST_DIR", "data/private/etf-burst"))
    parser.add_argument("--burst-config", default=os.environ.get("ETF_BURST_CONFIG", "config/etf-burst.json"))
    parser.add_argument('--tick-boundary', choices=['legacy-right', 'provider-v2'], default='legacy-right')
    parser.add_argument('--tick-factor-baseline', help='Prepared Tick baseline file for a single replay day')
    parser.add_argument("--burst-input", help="Use a schema_version=2 turnover/CNY export for offline replay")
    parser.add_argument("--replay", type=lambda s: datetime.fromisoformat(s).date().isoformat())
    args = parser.parse_args()
    if args.replay and (args.mode != "live" or args.watch or Path(args.cache).resolve() ==
                        Path(os.environ.get("FTSHARE_LIVE_CACHE_FILE", "data/private/rqdata-live.json")).resolve()):
        parser.error("Replay requires live mode and an explicit separate --cache")
    if args.close and (args.mode != "live" or args.watch or args.replay):
        parser.error("--close is a one-shot live operation and cannot be combined with --watch or --replay")
    try:
        from .burst import query_history, replay_day
        universe = read(ROOT / "config/universe.json")
        if args.mode in {"live", "burst-replay"}:
            burst_dir = ROOT / args.burst_dir
            burst_config = read(ROOT / args.burst_config)
            q = burst_config.get("amount_window_quantile", burst_config.get("volume_window_quantile", 0.75))
            symbols = burst_config.get("symbols") or BASKET
            minute_driver = burst_config.get("minute_driver", "provider")
            if minute_driver not in {"provider", "tick"}:
                raise ValueError("minute_driver must be provider or tick")
            if not isinstance(symbols, list) or not symbols or not all(isinstance(s, str) for s in symbols):
                raise ValueError("etf-burst symbols must be a non-empty list of symbols")
            unknown = [symbol for symbol in symbols if symbol not in {fund["symbol"] for fund in universe["funds"]}]
            if unknown:
                raise ValueError("Unknown etf-burst symbols: " + ", ".join(unknown))
            if not finite(q) or not 0 < q < 1:
                raise ValueError("amount_window_quantile must be between 0 and 1")
        if args.mode == "burst-replay" and args.burst_input:
            from .burst import SLOT_INDEX
            from .burst import validate_metric
            source = validate_metric(read(args.burst_input))
            minutes, daily = {}, {}
            for symbol, day, slot, volume in source["1m"]:
                minutes.setdefault(day, {}).setdefault(symbol, [None] * len(SLOTS))[SLOT_INDEX[slot]] = volume
            for symbol, day, _, volume in source["1d"]:
                daily.setdefault(day, {})[symbol] = volume
            ticks = source.get("ticks")
            calendar = sorted(set(daily) | {str(row[1])[:10] for row in ticks or []})
            run_burst_replay(args, burst_dir, q, calendar, minutes, daily, replay_day, symbols, ticks=ticks)
            return
        api = None
        if not (args.mode == "history" and args.source == "ddb"):
            import rqdatac
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rqdatac.init(connect_timeout=8, timeout=40)
            api = rqdatac
        if args.mode == "burst-replay":
            start = args.start or args.end
            warm = before(start, 130)
            end = min(args.end, before(datetime.now(provider.CHINA).date().isoformat(), 1))
            calendar = [str(day) for day in rqdatac.get_trading_dates(warm, end)]
            minutes, daily = query_history(rqdatac, warm, end, symbols)
            run_burst_replay(args, burst_dir, q, calendar, minutes, daily, replay_day, symbols)
            return
        if args.mode == "crowding":
            from .crowding import DEFAULT_PATH, update
            from .crowding_extra import update_extra
            path = ROOT / (args.crowding_file or DEFAULT_PATH)
            snapshot = (update(rqdatac, path, args.start, args.end) if args.crowding_module == "daily" else
                        update_extra(rqdatac, args.crowding_module, path, args.start, args.end))
            print(json.dumps({"days": len(snapshot["rows"]), "through": snapshot["rows"][-1]["date"]}))
            return
        if args.close:
            day = datetime.now(provider.CHINA).date().isoformat()
            rows = provider.collect(rqdatac, {"kind": "replay", "as_of": day + "T15:05:00",
                                              "symbols": BASKET + [INDEX]})
            value = live_snapshot(rows, universe["funds"], day, False, close=True)
            if not value:
                raise ValueError("Close snapshot is incomplete")
            cache = read(args.cache) if Path(args.cache).exists() else {"schema": 1, "entries": {}}
            cache["entries"]["live:rqdata:snapshot"] = json.dumps(
                {"value": value, "checked_at": iso_utc(datetime.now(timezone.utc))}, ensure_ascii=False)
            publish(args.cache, cache)
            print(json.dumps({"date": value["date"], "through": value["last_complete_minute"], "close": True}))
        elif args.mode == "history":
            source_config = load_source_config(args.source_config)
            entry = source_entry(source_config, args.source)
            seed_path = Path(args.seed) if args.seed else ROOT / entry["seed"]
            if args.source == "rqdata" and os.environ.get("FTSHARE_SEED_FILE") and not args.seed:
                seed_path = ROOT / os.environ["FTSHARE_SEED_FILE"]
            previous = read(seed_path) if seed_path.is_file() else None
            config = read(ROOT / "config/collection.json")
            if args.config:
                config.update(read(args.config))
            if config.get("fund_history") is False or (
                    type(config["minute_days"]) is not int or config["minute_days"] < 0):
                raise ValueError("Invalid collection configuration")
            recoveries = {fund["symbol"]: fund["flow_evidence"] for fund in (previous or {}).get("universe", [])
                          if fund.get("flow_evidence")}
            overrides = ROOT / "data/private/flow-recoveries.json"
            if overrides.is_file():
                recoveries.update(read(overrides)["funds"])
            for fund in universe["funds"]:
                if fund["symbol"] in recoveries:
                    fund["flow_evidence"] = recoveries[fund["symbol"]]
            recent = bool(args.recent)
            if recent:
                if previous is None:
                    raise ValueError("--recent requires an existing snapshot; run one full history first")
                anchor = previous["latest_market_date"] or previous["latest_flow_date"]
                cutoff = max(config["flow_start"], config["market_start"], before(anchor, 14))
                # Only the overlap window is re-queried; merge_history keeps older days.
                config = {**config, "flow_start": cutoff, "market_start": cutoff}
            start = before(min(config["flow_start"], config["market_start"]), 14)
            adapter = build_source(args.source, source_config, api=api, universe=universe,
                                   collection_config=config)
            target = min(args.end, before(datetime.now(provider.CHINA).date().isoformat(), 1))
            target = adapter.target_date(target)
            status = adapter.status(target)
            if not status.ready:
                raise ValueError(f"{adapter.label} source not ready: {status.reason}")
            market, fund_records, closes = adapter.collect_history(start, args.end, config["minute_days"])
            funds = fund_records["funds"]
            minute_days = getattr(adapter, "snapshot_minute_days", None)
            snapshot_config = config if minute_days is None else {**config, "minute_days": minute_days}
            snapshot = history_snapshot(market, funds, universe, snapshot_config, args.end, closes,
                                        source=adapter.label)
            minute_seed = entry.get("minute_seed")
            if minute_seed:
                minute_path = ROOT / minute_seed
                if minute_path.is_file():
                    snapshot["minutes"] = read(minute_path).get("minutes", {})
            if any(metric["latest"] is None for metric in snapshot["broad_metrics"].values()):
                raise ValueError(f"{adapter.label} broad metrics contain no valid data")
            if recent:
                snapshot = merge_history(previous, snapshot)
            snapshot = preserve_previous(previous, snapshot)
            if previous is not None:
                validate_replacement(previous, snapshot)
            snapshot["source"]["capabilities"] = source_capabilities(source_config, args.source)
            publish(seed_path, snapshot)
            print(json.dumps({"source": adapter.label, "market_through": snapshot["latest_market_date"],
                              "flow_through": snapshot["latest_flow_date"], "minute_days": len(snapshot["minutes"]),
                              "recent": recent, "broad_days": len(snapshot["broad"]["dates"])}))
        elif args.replay:
            live_run(rqdatac, universe["funds"], args.cache, args.replay)
        else:
            from .realtime import watch
            watch(rqdatac, universe["funds"], args.cache, burst_dir, q, args.watch, symbols, minute_driver,
                  futures_enabled=burst_config.get("futures_evidence", False) is True)
    except Exception as error:
        if os.environ.get("ETF_DEBUG") == "1":
            import traceback
            traceback.print_exc()
            try:
                with open(ROOT / ".local/last-error.txt", "w", encoding="utf-8") as handle:
                    traceback.print_exc(file=handle)
            except OSError:
                pass
        parser.exit(1, f"{args.source.upper()} {args.mode} failed ({type(error).__name__}). Check configuration and data coverage.\n")


def run_burst_replay(args, burst_dir, q, calendar, minutes, daily, replay_day, symbols, ticks=None):
    dates = [day for day in calendar if (args.start or args.end) <= day <= args.end]
    if not dates:
        raise ValueError("No requested replay dates in turnover data")
    count = elevated = 0
    for day in dates:
        if ticks is None:
            archive = replay_day(day, calendar, minutes, daily, q, symbols)
        else:
            from .burst import make_baseline
            from .tick_minutes import replay_tick_day
            from .tick_factors import load_references
            previous = max((d for d in calendar if d < day), default=None)
            refs = None
            if args.tick_factor_baseline:
                refs, state = load_references(args.tick_factor_baseline, day, previous, q)
                if state != 'ready':
                    raise ValueError('Tick replay baseline is ' + state)
            archive = replay_tick_day(day, make_baseline(calendar, minutes, daily, day, q, symbols), q, ticks,
                                      symbols, previous, boundary_mode=args.tick_boundary, factor_references=refs)
        if archive["through"] is None:
            raise ValueError(f"Missing replay minutes for {day}")
        publish(burst_dir / "replay" / f"{day}.json", archive)
        count += len(archive["events"])
        elevated += sum(e["level"] >= 2 for e in archive["events"])
    print(json.dumps({"days": len(dates), "events": count, "confirmed_or_extreme": elevated}), flush=True)


def merge_history(previous, current):
    """Splice a recent re-collection into an existing snapshot and keep older days.

    ``current`` must cover a contiguous window whose first date is later than the
    oldest date in ``previous``. Days before that cutoff are kept verbatim; days in
    the window are replaced by the fresh values (including upstream corrections).
    """
    if (previous.get("provider"), previous.get("formula"), previous.get("minute_slots")) != (
            current["provider"], current["formula"], current["minute_slots"]):
        raise ValueError("Incompatible previous snapshot")
    if "broad_metrics" not in previous:
        raise ValueError("Previous snapshot has no broad_metrics; run one full history first")
    cutoff = current["history"]["dates"][0]
    keep = [i for i, day in enumerate(previous["history"]["dates"]) if day < cutoff]
    merged = {**previous, "history": {}, "broad": {}}
    merged["history"] = {"dates": [previous["history"]["dates"][i] for i in keep] + current["history"]["dates"]}
    for key in ("amount", "idx", "netflow", "flow_coverage", "flow_expected"):
        merged["history"][key] = [previous["history"][key][i] for i in keep] + current["history"][key]
    bkeep = [i for i, day in enumerate(previous["broad"]["dates"]) if day < cutoff]
    broad = {**current["broad"]}
    broad["dates"] = [previous["broad"]["dates"][i] for i in bkeep] + current["broad"]["dates"]
    broad["total"] = [previous["broad"]["total"][i] for i in bkeep] + current["broad"]["total"]
    broad["series"] = {c: [previous["broad"]["series"][c][i] for i in bkeep] + current["broad"]["series"][c]
                       for c in CATEGORIES}
    broad["coverage"] = {c: {key: [previous["broad"]["coverage"][c][key][i] for i in bkeep]
                             + current["broad"]["coverage"][c][key] for key in ("valid", "expected")}
                         for c in CATEGORIES}
    issues = {day: rows for day, rows in previous["broad"]["issues"].items() if day < cutoff}
    issues.update(current["broad"]["issues"])
    broad["issues"] = issues
    merged["broad"] = broad
    metrics = {}
    for name in ("size", "premium"):
        prior, fresh = previous["broad_metrics"][name], current["broad_metrics"][name]
        metric = {**fresh}
        metric["total"] = [prior["total"][i] for i in bkeep] + fresh["total"]
        metric["series"] = {c: [prior["series"][c][i] for i in bkeep] + fresh["series"][c] for c in CATEGORIES}
        metric["valid"] = {c: [prior["valid"][c][i] for i in bkeep] + fresh["valid"][c] for c in CATEGORIES}
        metrics[name] = metric
    merged["broad_metrics"] = metrics
    merged["calendar"] = sorted(set(previous.get("calendar", [])) | set(current["calendar"]))
    minutes = dict(previous.get("minutes", {}))
    for day, row in current["minutes"].items():
        if row.get("quality") and day in minutes and not minutes[day].get("quality"):
            continue
        minutes[day] = row
    merged["minutes"] = minutes
    merged["core_fund_daily"] = current.get("core_fund_daily") or previous.get("core_fund_daily")
    merged["latest_flow_date"] = current["latest_flow_date"]
    merged["latest_market_date"] = current["latest_market_date"]
    merged["built_at"] = current["built_at"]
    merged["provider"] = current["provider"]
    merged["sources"] = current["sources"]
    merged["source"] = current.get("source")
    merged["source_audit"] = {**current["source_audit"],
                              "missing_days": sum(day <= merged["latest_flow_date"] and value is None
                                                  for day, value in zip(broad["dates"], broad["total"]))}
    return merged


def preserve_previous(previous, current):
    """Fill null history/broad/metric cells from the previous snapshot.

    A full rebuild can lose old cells when the provider no longer returns an
    otherwise valid historical sample. Keep the last known value instead of
    dropping it; new non-null values always win.
    """
    if not previous:
        return current
    prev_history = {day: i for i, day in enumerate(previous["history"]["dates"])}
    for key in ("amount", "idx", "netflow"):
        for i, day in enumerate(current["history"]["dates"]):
            if finite(current["history"][key][i]):
                continue
            j = prev_history.get(day)
            if j is not None and finite(previous["history"][key][j]):
                current["history"][key][i] = previous["history"][key][j]
    prev_broad = {day: i for i, day in enumerate(previous["broad"]["dates"])}
    for i, day in enumerate(current["broad"]["dates"]):
        j = prev_broad.get(day)
        if j is None:
            continue
        if not finite(current["broad"]["total"][i]) and finite(previous["broad"]["total"][j]):
            current["broad"]["total"][i] = previous["broad"]["total"][j]
        for category in CATEGORIES:
            if not finite(current["broad"]["series"][category][i]) and finite(previous["broad"]["series"][category][j]):
                current["broad"]["series"][category][i] = previous["broad"]["series"][category][j]
    for name, prior in previous.get("broad_metrics", {}).items():
        metric = current["broad_metrics"].get(name)
        if not metric:
            continue
        for i, day in enumerate(current["broad"]["dates"]):
            j = prev_broad.get(day)
            if j is None:
                continue
            if not finite(metric["total"][i]) and finite(prior["total"][j]):
                metric["total"][i] = prior["total"][j]
            for category in CATEGORIES:
                if not finite(metric["series"][category][i]) and finite(prior["series"][category][j]):
                    metric["series"][category][i] = prior["series"][category][j]
                if not metric["valid"][category][i] and prior["valid"][category][j]:
                    metric["valid"][category][i] = prior["valid"][category][j]
    return current


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
