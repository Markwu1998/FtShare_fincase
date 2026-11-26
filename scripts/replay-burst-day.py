"""Replay one ETF and index futures trading day using the monitor's rules."""
import argparse
from collections import Counter
from contextlib import redirect_stdout, redirect_stderr
import csv
from datetime import date, datetime, timedelta
import io
import json
import os
from pathlib import Path
import re
import sys
from tempfile import NamedTemporaryFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from etf_backend import ddb_replay
from etf_backend.burst import END, RULE_VERSION, SLOT_INDEX, make_baseline, query_history
from etf_backend.burst_ledger import BurstLedger
from etf_backend.collect import publish, read
from etf_backend.futures_evidence import END_GRACE, PRODUCTS, FuturesEvidence, window_ticks
from etf_backend.model import BASKET, SLOTS, eligible, finite, fund_flows, iso_utc
from etf_backend.provider import CHINA, fund_history, number
from etf_backend.tick_factors import load_references
from etf_backend.tick_minutes import replay_tick_day

ETF_FIELDS = ["last", "total_turnover", "volume"]
FUTURES_FIELDS = [*ETF_FIELDS, "open_interest", "b1", "a1"]


def connect_rqdata():
    import rqdatac as api
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        rq_call("login", api.init, connect_timeout=8, timeout=90)
    return api


def rq_call(label, operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except Exception as error:
        raise ValueError(f"RQData {label} failed ({type(error).__name__}). Completed local downloads are reusable.") from None


def trading_dates(api, day):
    first = (date.fromisoformat(day) - timedelta(days=160)).isoformat()
    calendar = [str(x) for x in rq_call("calendar", api.get_trading_dates, first, day)]
    if not calendar or calendar[-1] != day:
        raise ValueError("Requested date is not an RQData trading day")
    return calendar


def prepare_baseline(api, day, symbols, q, path):
    history = trading_dates(api, day)[-61:-1]
    if len(history) < 60:
        raise ValueError("Need 60 trading dates before the replay date")
    codes = {s.replace(".SH", ".XSHG").replace(".SZ", ".XSHE") for s in symbols}
    if {x.order_book_id for x in rq_call("ETF identifiers", api.instruments, sorted(codes)) if x is not None} != codes:
        raise ValueError("Could not verify every ETF code for the baseline")
    minutes, daily = rq_call("prior 1m/1d", query_history, api, history[0], history[-1], symbols)
    baseline = make_baseline(history, minutes, daily, day, q, symbols)
    publish(path, {
        "identity": {"day": day, "as_of": history[-1], "symbols": symbols, "quantile": q, "rule_version": RULE_VERSION},
        "baseline": {s: {"daily": totals, "windows": [[k, i, refs] for (k, i), refs in windows.items()]}
                     for s, (totals, windows) in baseline.items()},
    })


def daily_flow_summary(day, funds, inputs, provider=None):
    """Summarise daily net creations; the cached inputs may carry their own source label."""
    provider = provider or inputs.get("provider") or "RQData"
    calendar = [inputs["previous_date"], inputs["date"]]
    if calendar[-1] != day or date.fromisoformat(calendar[0]) >= date.fromisoformat(day):
        raise ValueError("Fund flow input has incompatible dates")
    by_symbol = {}
    for fund in funds:
        symbol = fund["symbol"]
        records = inputs["funds"].get(symbol)
        if day < eligible(fund):
            row = {"flow": None, "issues": ["not_listed"]}
        elif records is None:
            row = {"flow": None, "issues": ["fund_records_missing"]}
        else:
            row = fund_flows(fund, records, calendar)[day]
        flow_yi = row.pop("flow")
        row["flow_cny"] = flow_yi * 1e8 if finite(flow_yi) else None
        by_symbol[symbol] = row
    valid = [row["flow_cny"] for row in by_symbol.values() if finite(row["flow_cny"])]
    return {"date": day, "previous_date": calendar[0], "provider": provider, "unit": "CNY",
            "valid": len(valid), "expected": len(funds), "net_flow_cny": sum(valid) if len(valid) == len(funds) else None,
            "by_symbol": by_symbol}


def fetch_daily_flow(api, day, symbols):
    dates = trading_dates(api, day)
    if len(dates) < 2:
        raise ValueError("Previous trading day is unavailable for fund flows")
    previous = dates[-2]
    return {"date": day, "previous_date": previous,
            **rq_call("fund units/NAV/splits", fund_history, symbols, previous, day)}


def rqdata_input(api, day, symbols, products, directory):
    actual = {s.replace(".SH", ".XSHG").replace(".SZ", ".XSHE"): s for s in symbols}
    manifest = directory / "contracts.json"
    if manifest.is_file():
        cached = read(manifest)
        if cached["date"] != day:
            raise ValueError("Cached contract manifest is for another date")
        contracts = cached["products"]
    else:
        if {x.order_book_id for x in rq_call("ETF identifiers", api.instruments, list(actual)) if x is not None} != set(actual):
            raise ValueError("Could not verify every ETF code")
        contracts = {p: list(rq_call(p + " contract list", api.futures.get_contracts, p, day)) for p in sorted(products)}
        publish(manifest, {"date": day, "products": contracts})
    validate_contracts(contracts, products)
    futures = {c: c for codes in contracts.values() for c in codes}
    for name, mapping, fields in [("etf", actual, ETF_FIELDS), ("futures", futures, FUTURES_FIELDS)]:
        for code, symbol in mapping.items():
            target = directory / name / (symbol + ".csv")
            if target.is_file():
                continue
            frame = rq_call(code + " Tick", api.get_price, code, start_date=day, end_date=day,
                            frequency="tick", fields=fields, adjust_type="none")
            target.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile("w", encoding="utf-8", newline="", dir=target.parent, delete=False) as file:
                writer = csv.writer(file)
                writer.writerow(["symbol", "datetime", *fields])
                if frame is not None and not frame.empty:
                    times = frame.index.get_level_values(-1)
                    writer.writerows([symbol, t.isoformat(), *row]
                                     for t, row in zip(times, frame[fields].itertuples(index=False, name=None)))
                else:
                    print("No Tick returned for", code, flush=True)
            Path(file.name).replace(target)
            print("Downloaded", symbol, flush=True)


def validate_contracts(contracts, products):
    if set(contracts) != products:
        raise ValueError("The contract manifest must contain the futures products mapped to the selected ETFs")
    for product, codes in contracts.items():
        if not isinstance(codes, list) or len(codes) != 4 or codes != sorted(set(codes)) or any(
                not re.fullmatch(product + r"[0-9]{4}", code) for code in codes):
            raise ValueError(f"{product} requires four actual dated contracts in expiry order")


def baseline_for(path, day, symbols, q):
    data = read(path)
    identity = data["identity"]
    prior = date.fromisoformat(identity["as_of"]).isoformat()
    if (identity["day"] != day or prior >= day or identity["quantile"] != q or
            identity["rule_version"] != RULE_VERSION or not set(symbols).issubset(identity["symbols"])):
        raise ValueError("Baseline date, symbols, rule version or quantile is incompatible")
    baseline = {}
    for symbol in symbols:
        item = data["baseline"][symbol]
        daily = item["daily"]
        windows = {(k, i): refs for k, i, refs in item["windows"]}
        if (len(daily) != 2 or not all(finite(x) and x > 0 for x in daily) or
                not any(k in (5, 10) and all(finite(x) and x > 0 for x in refs[0])
                        for (k, _), refs in windows.items())):
            raise ValueError(f"{symbol} lacks usable prior 20/60-day references")
        baseline[symbol] = daily, windows
    return baseline, prior


def timestamp(value, clock):
    t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if t.tzinfo is None:
        if clock == "received":
            raise ValueError("Recorded reception replay requires explicit timezone offsets")
        return t.replace(tzinfo=CHINA)
    return t.astimezone(CHINA)


def load_ticks(path, day, clock, allowed, futures=False):
    fields = {"symbol", "datetime", "last", "total_turnover"}
    if futures:
        fields.update(FUTURES_FIELDS)
    if clock == "received":
        fields.add("received_at")
    records = []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if not fields.issubset(reader.fieldnames or ()):
            raise ValueError(path.name + " is missing columns: " + ", ".join(sorted(fields - set(reader.fieldnames or ()))))
        for idx, row in enumerate(reader, 2):
            symbol = row["symbol"].strip()
            if symbol not in allowed:
                continue
            market = timestamp(row["datetime"], clock)
            received = timestamp(row["received_at"], clock) if clock == "received" else market
            if market.date().isoformat() != day or market > received + timedelta(seconds=2):
                raise ValueError(f"{path.name}:{idx} has incompatible market/reception time for {day}")
            values = {key: number(row.get(key)) for key in (FUTURES_FIELDS if futures else ETF_FIELDS)}
            records.append((received, market, symbol, values))
    # Stable sort preserves source order when receive timestamps tie.
    records.sort(key=lambda x: x[0])
    return records


def read_input(directory, name, day, clock, allowed):
    combined = directory / (name + ".csv")
    if combined.is_file():
        return load_ticks(combined, day, clock, allowed, futures=name == "futures")
    records = []
    for symbol in sorted(allowed):
        path = directory / name / (symbol + ".csv")
        if path.is_file():
            records.extend(load_ticks(path, day, clock, {symbol}, futures=name == "futures"))
    records.sort(key=lambda r: r[0])
    return records


def attach_futures(report, records, contracts, product_by_symbol):
    tracker = FuturesEvidence()
    tracker.products = contracts
    tracker.rows = {code: [] for codes in contracts.values() for code in codes}
    work = []
    for event in report["events"]:
        product = product_by_symbol.get(event["symbol"])
        observations = event["evidence"]["futures_windows"] = [] if product else None
        if observations is None:
            continue
        for signal in event["updates"]:
            if signal["level"] < 2:
                continue
            day = report["date"]
            start = timestamp(day + "T" + signal["start"], "market") - timedelta(minutes=1)
            end = timestamp(day + "T" + signal["end"], "market")
            available = max(end + END_GRACE, timestamp(signal["received_at"], "received"))
            work.append((available, start, end, product, event, signal))
    work.sort(key=lambda x: x[0])
    cursor = 0
    used_ticks = {}
    for available, start, end, product, event, signal in work:
        while cursor < len(records) and records[cursor][0] <= available:
            received, market, code, values = records[cursor]
            tracker.add(code, market, values, iso_utc(received))
            cursor += 1
        evidence = tracker.observe(product, start, end)
        symbol = event["symbol"]
        minute_volume = report["minute_volume_shares"].get(symbol, {})
        slots = SLOTS[SLOT_INDEX[signal["start"]]:SLOT_INDEX[signal["end"]] + 1]
        shares = sum(minute_volume[s] for s in slots) if all(s in minute_volume for s in slots) else None
        evidence.update(window_start=signal["start"], window_end=signal["end"], etf_turnover_cny=signal["amount"],
                        etf_volume_shares=shares, observed_at=iso_utc(available), recovered=False)
        for code in contracts[product]:
            for tick in window_ticks(tracker.rows[code], start, end):
                used_ticks.setdefault((code, tick.timestamp), tick)
        event["evidence"]["futures_windows"].append(evidence)
    return used_ticks


def write_journal(path, report, used_ticks):
    identity = {"day": report["date"], "driver": "tick-replay", "clock": report["replay_clock"],
                "baseline_as_of": report["baseline_as_of"], "rule_version": report["rule_version"],
                "quantile": report["quantile"], "symbols": report["symbols"]}
    journal = BurstLedger(path, identity)
    for field in ("candidate_history", "tick_candidate_history"):
        for candidate in report[field]:
            journal.append("candidate", candidate["symbol"], candidate["as_of"], candidate.get("received_at"), candidate)
    for event in report["events"]:
        symbol = event["symbol"]
        for signal in event["updates"]:
            market = report["date"] + "T" + signal["end"] + ":00+08:00"
            journal.append("formal", symbol, market, signal["received_at"], {
                "event_id": event["id"], "signal": signal, "recovered": False})
        for evidence in event["evidence"].get("futures_windows") or []:
            journal.append("futures_evidence", symbol, evidence["end_at"], evidence["observed_at"],
                           {"event_id": event["id"], **evidence})
    for (code, time), tick in used_ticks.items():
        journal.append("futures_tick", code, iso_utc(time), tick.received_at,
                       {"last": tick.last, "open_interest": tick.oi, "volume": tick.volume,
                        "total_turnover": tick.turnover, "bid": tick.bid, "ask": tick.ask})
        if len(journal.pending) >= 5000:
            journal.commit()
    journal.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", required=True, type=lambda x: date.fromisoformat(x).isoformat())
    parser.add_argument("--source", choices=("rqdata", "ddb"), default="rqdata")
    parser.add_argument("--input-dir", type=Path, help="Directory containing normalized Tick CSVs and contracts.json")
    parser.add_argument("--offline", action="store_true", help="Use local Tick and baseline files only")
    parser.add_argument("--baseline", type=Path, help="Prepared ETF baseline JSON, defaults to the replay input or collector cache")
    parser.add_argument("--tick-factor-baseline", type=Path, help="Optional prior-day second-level baseline JSON")
    parser.add_argument("--clock", choices=("market", "received"), default="market")
    parser.add_argument("--symbols", nargs="+", help="ETF subset, defaults to the configured ETF basket")
    parser.add_argument("--config", type=Path, default=ROOT / os.environ.get("ETF_BURST_CONFIG", "config/etf-burst.json"))
    parser.add_argument("--out", type=Path, help="Replay directory, not the live collector directory")
    args = parser.parse_args()
    day = args.day
    config = read(args.config)
    q = config.get("amount_window_quantile", config.get("volume_window_quantile", .75))
    symbols = list(dict.fromkeys(args.symbols or config.get("symbols") or BASKET))
    universe = {f["symbol"]: f for f in read(ROOT / "config/universe.json")["funds"]}
    if not finite(q) or not 0 < q < 1 or not set(symbols).issubset(universe):
        parser.error("Invalid ETF selection or quantile")
    funds = [universe[s] for s in symbols]
    product_by_symbol = {f["symbol"]: PRODUCTS[f["bucket"]] for f in funds if f["bucket"] in PRODUCTS}
    products = set(product_by_symbol.values())
    provider = "DDB" if args.source == "ddb" else "RQData"
    out = args.out or ROOT / ".local" / "burst-day-replay" / day
    collector_dir = ROOT / os.environ.get("ETF_BURST_DIR", "data/private/etf-burst")
    archive = out / "replay" / f"{day}.json"
    journal = out / "journals" / f"{day}.sqlite3"
    if out.resolve() == collector_dir.resolve() or archive.exists() or journal.exists() or (out / "live.json").exists():
        parser.error("Choose an unused output directory separate from live collection")
    directory = args.input_dir or out / "inputs"
    if args.input_dir is None and args.clock != "market":
        parser.error("Use --input-dir with recorded reception times for --clock=received")
    if args.input_dir is not None and not directory.is_dir():
        parser.error("Tick input directory does not exist")
    identity = {"date": day, "symbols": symbols, "products": sorted(products)}
    cache = directory / "download.json"
    if args.input_dir is None and cache.is_file() and read(cache) != identity:
        parser.error("This output directory contains downloads for a different replay selection")
    api = None
    session = None

    def ddb_session():
        nonlocal session
        if session is None:
            session = ddb_replay.connect()
        return session

    baseline_path = args.baseline or directory / "baseline.json"
    generated_baseline = out / "inputs" / "baseline.json"
    if args.baseline:
        baseline, prior = baseline_for(baseline_path, day, symbols, q)
    else:
        baseline = None
        for candidate in dict.fromkeys([baseline_path, generated_baseline, collector_dir / "baselines" / (day + ".json")]):
            try:
                baseline, prior = baseline_for(candidate, day, symbols, q)
                break
            except (OSError, KeyError, TypeError, ValueError):
                continue
        if baseline is None:
            if args.offline:
                parser.error("No compatible prior-day baseline; supply --baseline or allow history generation")
            if args.source == "ddb":
                ddb_replay.prepare_baseline(ddb_session(), day, symbols, q, generated_baseline)
            else:
                api = connect_rqdata()
                prepare_baseline(api, day, symbols, q, generated_baseline)
            baseline, prior = baseline_for(generated_baseline, day, symbols, q)
    if args.input_dir is None and not args.offline:
        if not cache.is_file():
            publish(cache, identity)
        listing_path = directory / "contracts.json"
        recorded = read(listing_path)["products"] if listing_path.is_file() else {}
        expected_futures = [c for codes in recorded.values() for c in codes]
        if (set(recorded) != products or not all((directory / "etf" / (s + ".csv")).is_file() for s in symbols) or
                not all((directory / "futures" / (c + ".csv")).is_file() for c in expected_futures)):
            if args.source == "ddb":
                ddb_replay.write_inputs(ddb_session(), day, symbols, products, directory)
            else:
                api = api or connect_rqdata()
                rqdata_input(api, day, symbols, products, directory)
    flow_path = out / "inputs" / "fund-flow.json"
    flow_input = next((p for p in (flow_path, directory / "fund-flow.json") if p.is_file()), None)
    daily_flow = None
    if flow_input:
        daily_flow = daily_flow_summary(day, funds, read(flow_input), provider=provider)
    if not args.offline and (daily_flow is None or daily_flow["valid"] < len(funds)):
        try:
            if args.source == "ddb":
                inputs = ddb_replay.fund_input(ddb_session(), day, symbols)
            else:
                inputs = fetch_daily_flow(api or connect_rqdata(), day, symbols)
        except ValueError as error:
            print(f"Daily fund flow unavailable: {error}", file=sys.stderr, flush=True)
        else:
            daily_flow = daily_flow_summary(day, funds, inputs, provider=provider)
            publish(flow_path, inputs)
            print(f"Daily fund flow: {daily_flow['valid']}/{daily_flow['expected']}", flush=True)
    factor_refs = None
    factor_path = args.tick_factor_baseline or collector_dir / "tick-baselines" / (day + ".json")
    if args.tick_factor_baseline or factor_path.is_file():
        factor_refs, state = load_references(factor_path, day, prior, q)
        if state != "ready" or not set(symbols).issubset(factor_refs):
            if args.tick_factor_baseline:
                parser.error("The second-level baseline is not ready for all selected ETFs")
            factor_refs = None
    contracts = {}
    futures_records = []
    if products:
        listing = read(directory / "contracts.json")
        if listing["date"] != day:
            parser.error("Futures contract manifest is for another date")
        contracts = {p: codes for p, codes in listing["products"].items() if p in products}
        validate_contracts(contracts, products)
        futures_records = read_input(directory, "futures", day, args.clock, {c for codes in contracts.values() for c in codes})
    etf_records = read_input(directory, "etf", day, args.clock, set(symbols))
    counts = Counter(r[2] for r in etf_records)
    if set(counts) != set(symbols):
        parser.error("ETF Tick missing for " + ", ".join(sorted(set(symbols) - set(counts))))
    tick_rows = ([symbol, market.isoformat(), received.isoformat(), values["last"], values["total_turnover"], values["volume"]]
                 for received, market, symbol, values in etf_records)
    report = replay_tick_day(day, baseline, q, tick_rows, symbols, prior, boundary_mode="provider-v2",
                             factor_references=factor_refs, include_volume=True, provider=provider)
    if report["through"] is None:
        parser.error("ETF Tick yielded no usable minute bars")
    report["replay_clock"] = args.clock
    if daily_flow is not None:
        report["daily_flow"] = daily_flow
    report["journal"] = {"enabled": True, "format": "sqlite-append-v1"}
    if products:
        report["futures_monitor"] = {"state": "replay", "products": sorted(products)}
    report["futures_contracts"] = contracts
    report["input_coverage"] = {"etf_ticks": counts, "futures_ticks": Counter(r[2] for r in futures_records),
                                "expected_etf_minutes_per_symbol": END + 1,
                                "second_level_baseline": bool(factor_refs)}
    used = attach_futures(report, futures_records, contracts, product_by_symbol)
    write_journal(journal, report, used)
    publish(archive, report)
    print(json.dumps({"date": day, "clock": args.clock, "events": len(report["events"]),
                      "futures_windows": sum(len(e["evidence"].get("futures_windows") or []) for e in report["events"]),
                      "archive": str(archive), "journal": str(journal)}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SystemExit(f"Single-day replay failed: {error}")
