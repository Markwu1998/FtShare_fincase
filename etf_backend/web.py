"""FastAPI server for the existing dashboard and read-only HTTP API."""
from asyncio import sleep
from contextlib import suppress
from datetime import datetime, timezone
import json
from hashlib import sha256
from math import sin
import os
import re
from threading import Lock
from statistics import mean, pstdev

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from .burst import DEFAULT_DIR as BURST_DIR, validate_metric
from .burst_ledger import read_journal
from .collect import ROOT, read
from .crowding import DEFAULT_PATH as CROWDING_PATH, validate_snapshot as validate_crowding
from .crowding_extra import MODULES, path_for, validate_extra
from .flow_comparison import DEFAULT_PATH as FLOW_COMPARISON_PATH, validate_archive as validate_flow_comparison
from .model import BASKET, CATEGORIES, DEFAULT_STATUS, FORMULA, SLOTS, finite
from .provider import CHINA
from .sources import load_config as load_source_config, realtime_entries, source_descriptions, source_entries

ASSETS = {
    "/style.css": "src/style.css", "/echarts.min.js": "src/echarts.min.js",
    **{f"/assets/{name}": f"src/assets/{name}" for name in (
        "dashboard.js", "data-source.js", "flow-math.js", "radar.js", "radar-math.js", "brush.js", "presentation.js", "crowding.js", "ftai-mark.svg"
    )},
    "/licenses/MIT.txt": "LICENSE",
    "/licenses/echarts-LICENSE.txt": "third_party/echarts/LICENSE",
    "/licenses/echarts-NOTICE.txt": "third_party/echarts/NOTICE",
    "/licenses/d3-LICENSE.txt": "third_party/echarts/licenses/LICENSE-d3",
}


def validate_seed(seed, mode, source_id=None):
    history = seed["history"]
    dates = history["dates"]
    seed_source = seed.get("source", {}).get("id")
    if source_id and seed_source and seed_source != source_id:
        raise ValueError("Seed source does not match requested source")
    if seed["schema"] != 1 or seed["formula"] != FORMULA or seed["categories"] != CATEGORIES or not dates or (
            dates != sorted(set(dates)) or not all(len(history[key]) == len(dates) for key in (
                "idx", "amount", "netflow", "flow_coverage", "flow_expected"))):
        raise ValueError("Invalid snapshot")
    if mode == "live" and (seed.get("demo") or seed["provider"] not in {"RQData", "DDB"} or
                           seed["minute_slots"] != SLOTS):
        raise ValueError("Live mode requires a supported daily snapshot")
    if mode == "demo" and not (seed.get("demo") and seed["provider"] == "Demo"):
        raise ValueError("Invalid demo snapshot")
    if {fund["symbol"] for fund in seed["universe"] if fund["is_core_basket"]} != set(BASKET):
        raise ValueError("Invalid core membership")
    broad = seed["broad"]
    size = len(broad["dates"])
    if not size or len(broad["total"]) != size or any(
            len(broad["series"][category]) != size or
            any(len(broad["coverage"][category][key]) != size for key in ("valid", "expected"))
            for category in CATEGORIES):
        raise ValueError("Invalid broad history")
    if "broad_metrics" in seed:
        for name in ("size", "premium"):
            metric = seed["broad_metrics"][name]
            if (len(metric["total"]) != size or any(
                    len(metric[key][category]) != size
                    for key in ("series", "valid") for category in CATEGORIES)
                    or (metric["latest"] is not None and metric["latest"] not in broad["dates"])
                    or not isinstance(metric["members"], list)):
                raise ValueError("Invalid broad metrics")
    slots = len(seed["minute_slots"])
    if any(len(row["bars"]) != slots or len(row["cumulative"]) != slots for row in seed["minutes"].values()):
        raise ValueError("Invalid minute archive")
    if not all(value is None or finite(value) for key in ("idx", "amount", "netflow") for value in history[key]):
        raise ValueError("Invalid history values")
    return seed


class Archive:
    def __init__(self, path, validate):
        self.path = path
        self.validate = validate
        self.value = None
        self.version = None
        self.lock = Lock()

    def load(self):
        with self.lock:
            try:
                info = self.path.stat()
                version = (info.st_ino, info.st_size, info.st_mtime_ns)
                if version != self.version:
                    self.value = self.validate(read(self.path))
                    self.version = version
            except (OSError, ValueError, KeyError, TypeError):
                pass
            return self.value


def demo_live(seed):
    date = seed["demo"]["as_of"]
    row = seed["minutes"][date]
    amount = row["cumulative"][-1]
    names = {f["symbol"]: f["name"] for f in seed["universe"]}
    return {"provider": "Demo", "formula": seed["formula"], "date": date, "fetched_at": seed["built_at"],
            "slots": seed["minute_slots"], **row, "coverage": [20] * len(row["bars"]), "pre_close": 3490,
            "closes": [3500 + 22 * sin(i / 42) for i in range(len(row["bars"]))],
            "last_complete_minute": "15:00", "expected_flow_date": seed["latest_flow_date"],
            "trading_dates": seed["calendar"], "daily_batches": (len(names) + 7) // 8, "warnings": [], "amount": amount,
            "table": [{"symbol": s, "code": s[:6], "name": names[s], "price": 1 + i / 10,
                       "amount": amount * (i + 1) / 210, "pct": sin(i) * 1.2} for i, s in enumerate(BASKET)]}


def validate_cache(value):
    entry = json.loads(value["entries"].get("live:rqdata:snapshot", "null"))
    if entry is None:
        return None
    live = entry["value"]
    if live["provider"] != "RQData" or live["slots"] != SLOTS or any(
            len(live[key]) != len(SLOTS) for key in ("bars", "coverage", "closes", "cumulative")):
        raise ValueError("Invalid minute cache")
    fetched_at = datetime.fromisoformat(live["fetched_at"].replace("Z", "+00:00"))
    if fetched_at.utcoffset() is None:
        raise ValueError("Missing quote timestamp offset")
    return entry


def validate_burst(value):
    validate_metric(value)
    if value["provider"] != "RQData" or not isinstance(value["events"], list) or not value["updated_at"]:
        raise ValueError("Invalid ETF event archive")
    return value


def create_app(mode=None, seed_file=None, cache_file=None, *, radar=None, allow_replay=None, crowding_file=None,
               burst_dir=None, flow_comparison_file=None):
    mode = mode or os.environ.get("DATA_MODE", "demo")
    if mode not in {"demo", "live"}:
        raise ValueError("Invalid DATA_MODE")
    demo = mode == "demo"
    if radar is None:
        radar = os.environ.get("ENABLE_RADAR") == "1"
    if allow_replay is None:
        allow_replay = os.environ.get("RQDATA_ALLOW_REPLAY") == "1"
    if demo:
        source_config = {"default": "demo", "realtime_default": "demo", "sources": [], "realtime_sources": []}
        seed_paths = {"demo": ROOT / "data/demo-seed.json"}
        default_source = "demo"
        realtime_default = "demo"
        daily_entries = []
        realtime_entries_list = []
    elif seed_file:
        source_config = {"default": "rqdata", "realtime_default": "rqdata", "sources": [], "realtime_sources": []}
        seed_paths = {"rqdata": ROOT / (seed_file or os.environ.get(
            "FTSHARE_SEED_FILE", "data/private/rqdata-seed.json"))}
        default_source = "rqdata"
        realtime_default = "rqdata"
        daily_entries = [{"id": "rqdata", "label": "RQData", "seed": str(seed_paths["rqdata"])}]
        realtime_entries_list = [{"id": "rqdata", "label": "RQData", "kind": "realtime"}]
    else:
        source_config = load_source_config()
        daily_entries = source_entries(source_config)
        realtime_entries_list = realtime_entries(source_config)
        seed_paths = {entry["id"]: ROOT / entry["seed"] for entry in daily_entries}
        if os.environ.get("FTSHARE_SEED_FILE"):
            seed_paths["rqdata"] = ROOT / os.environ["FTSHARE_SEED_FILE"]
        default_source = source_config.get("default") or (daily_entries[0]["id"] if daily_entries else "rqdata")
        realtime_default = source_config.get("realtime_default") or "rqdata"
    cache_path = ROOT / (cache_file or os.environ.get("FTSHARE_LIVE_CACHE_FILE", "data/private/rqdata-live.json"))
    archives = {source_id: Archive(path, lambda value, source_id=source_id: validate_seed(value, mode, source_id))
                for source_id, path in seed_paths.items()}
    archive = archives.get(default_source) or next(iter(archives.values()))
    cache = Archive(cache_path, validate_cache)
    crowding_path = ROOT / (crowding_file or os.environ.get("CROWDING_FILE") or CROWDING_PATH)
    crowding = Archive(crowding_path, validate_crowding)
    crowding_modules = {module: Archive(path_for(module, crowding_path),
                                        lambda value, module=module: validate_extra(value, module))
                        for module in MODULES}
    event_dir = ROOT / (burst_dir or os.environ.get("ETF_BURST_DIR") or BURST_DIR)
    events = Archive(event_dir / "live.json", validate_burst)
    flow_comparison = Archive(ROOT / (flow_comparison_file or FLOW_COMPARISON_PATH), validate_flow_comparison)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    index = (ROOT / "src/index.html").read_text(encoding="utf-8").replace(
        'data-data-mode="demo"', f'data-data-mode="{mode}"').replace(
        'data-radar="off"', 'data-radar="on"' if radar else 'data-radar="off"')
    crowding_page = (ROOT / "src/crowding.html").read_text(encoding="utf-8")

    def with_observatory_switch(page, active):
        tabs = "".join(
            f'<a href="{url}"' + (' aria-current="page"' if active == url else '') + f'>{name}</a>'
            for url, name in (("/", "ETF 资金观测台"), ("/crowding", "市场拥挤度观测台"))
        )
        return page.replace('<!-- observatory-switch -->',
                            f'<nav class="observatory-switch" aria-label="切换观测台">{tabs}</nav>')

    index = with_observatory_switch(index, "/")
    crowding_page = with_observatory_switch(crowding_page, "/crowding")

    def response(value, status=200, provider=None):
        headers = {"Cache-Control": "no-store"}
        if provider:
            headers["X-Data-Provider"] = provider
        return JSONResponse(value, status, headers=headers)

    def error(message, code, status):
        return response({"error": message, "code": code}, status)

    status_lock = Lock()
    status_cache = {"key": None, "value": None}

    def source_status():
        values = []
        today = datetime.now(CHINA).date().isoformat()

        def file_version(path):
            try:
                info = path.stat()
                return (info.st_ino, info.st_size, info.st_mtime_ns)
            except OSError:
                return None

        key = (today, int(datetime.now(CHINA).timestamp()) // 30, file_version(cache_path), tuple((source_id, file_version(archives[source_id].path))
                                                    for source_id in archives))
        with status_lock:
            if status_cache["key"] == key:
                return status_cache["value"]
        loaded = {source_id: archives[source_id].load() for source_id in archives}
        known_dates = set()
        for seed in loaded.values():
            if seed:
                known_dates.update(seed.get("history", {}).get("dates", []))
        expected = max((day for day in known_dates if day < today), default=None)
        for entry in daily_entries:
            source_id = entry["id"]
            seed = loaded.get(source_id)
            if seed is None:
                values.append({"id": source_id, "label": entry.get("label", source_id), "kind": "daily",
                               "ready": False, "default": source_id == default_source,
                               "reason": "快照尚未构建", "capabilities": entry.get("capabilities", [])})
                continue
            meta = seed.get("source") or {}
            ready = meta.get("readiness", "ready") == "ready"
            reason = meta.get("reason")
            if expected and (seed.get("latest_flow_date") or "") < expected:
                ready = ready and source_id == "rqdata"
                reason = f"最新份额 {seed.get('latest_flow_date') or '无'}，目标 {expected}"
            elif expected and (seed.get("latest_market_date") or "") < expected:
                ready = ready and source_id == "rqdata"
                reason = f"最新行情 {seed.get('latest_market_date') or '无'}，目标 {expected}"
            values.append({"id": source_id, "label": entry.get("label", seed.get("provider", source_id)),
                           "kind": "daily", "ready": ready,
                           "default": source_id == default_source,
                           "latest_market_date": seed.get("latest_market_date"),
                           "latest_flow_date": seed.get("latest_flow_date"),
                           "built_at": seed.get("built_at"),
                           "stale": bool(reason),
                           "capabilities": meta.get("capabilities") or entry.get("capabilities", []),
                           "descriptions": source_descriptions(source_id),
                           "reason": reason})
        realtime = []
        for entry in realtime_entries_list:
            realtime.append({"id": entry["id"], "label": entry.get("label", entry["id"]), "kind": "realtime",
                             "ready": entry["id"] == "rqdata" and bool(cache.load()) and
                                      cache.load()["value"].get("date") == today and
                                      not cache.load()["value"].get("replay"),
                             "default": entry["id"] == realtime_default,
                             "capabilities": entry.get("capabilities", [])})
        result = {"default": default_source, "realtime_default": realtime_default,
                  "sources": values, "realtime_sources": realtime}
        with status_lock:
            status_cache["key"] = key
            status_cache["value"] = result
        return result

    def live_response(request):
        realtime_id = request.query_params.get("realtime_source") or realtime_default
        if realtime_id != "rqdata":
            return error("未知或尚未接入的实时行情源", "realtime_source_unknown", 400)
        entry = cache.load() or {}
        live = entry.get("value")
        if live is None:
            seed = archive.load()
            live = seed.get("live_snapshot") if seed else None
        if not live or live.get("provider") != "RQData" or live.get("slots") != SLOTS:
            return error("后台行情尚未就绪；历史图表可继续查看", "cache_not_ready", 503)
        replay = bool(live.get("replay"))
        now = datetime.now(CHINA)
        if replay and not allow_replay or not replay and live.get("date") != now.date().isoformat():
            return error("等待当日盘中行情", "cache_not_ready", 503)
        try:
            observed = datetime.fromisoformat(live["fetched_at"].replace("Z", "+00:00"))
            hm = now.strftime("%H:%M")
            in_session = now.weekday() < 5 and ("09:30" <= hm <= "11:30" or "13:00" <= hm <= "15:05")
            lag = (now - observed).total_seconds()
            if observed.utcoffset() is None or lag < -2:
                raise ValueError("Invalid quote time")
            stale = not replay and in_session and lag > 180
        except (KeyError, TypeError, ValueError):
            return error("盘中行情时间无效", "cache_not_ready", 503)
        if live.get("table"):
            rows = []
            for row in live["table"]:
                try:
                    age = max(0, (now - datetime.fromisoformat(row["quote_at"].replace("Z", "+00:00"))).total_seconds())
                except (KeyError, TypeError, ValueError):
                    age = None
                rows.append({**row, "quote_stale": not replay and in_session and (age is None or age > 90)})
            live = {**live, "table": rows}
        return response({**live, "cache": {"mode": "background", "collected_at": live["fetched_at"],
                         "checked_at": entry.get("checked_at"), "stale": stale,
                         "last_error": entry.get("last_error"), "refresh_seconds": 1 if live.get("feed") == "push" else 60}},
                        provider="RQData")

    @app.get("/api/live/stream")
    async def live_stream(request: Request):
        if demo:
            return error("演示数据不提供实时行情流", "live_disabled", 404)
        async def stream():
            previous = request.headers.get("last-event-id", "")[:160]
            while not await request.is_disconnected():
                result = await run_in_threadpool(live_response, request)
                value = json.loads(result.body)
                if value.get("replay"):
                    result = error("历史回放请使用回放接口", "replay_not_live", 409)
                    value = json.loads(result.body)
                # Include availability transitions as well as quote versions.
                version = value.get("published_at") or value.get("fetched_at") or value.get("code")
                version = f"{result.status_code}/{version}/{value.get('cache', {}).get('stale')}"
                if previous != version:
                    previous = version
                    event = "live" if result.status_code == 200 else "unavailable"
                    yield f"id: {version}\nevent: {event}\ndata: {result.body.decode('utf-8')}\n\n"
                else:
                    yield ": heartbeat\n\n"
                await sleep(1)
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    def current_events():
        snapshot = events.load()
        if snapshot and snapshot["date"] == datetime.now(CHINA).date().isoformat():
            return snapshot
        return None

    @app.get("/api/etf-burst/stream")
    async def event_stream(request: Request):
        if demo or not radar:
            return error("实时监控未启用", "burst_disabled", 404)

        async def stream():
            previous = request.headers.get("last-event-id", "")[:80]
            while not await request.is_disconnected():
                snapshot = await run_in_threadpool(current_events)
                if snapshot and snapshot["updated_at"] != previous:
                    previous = snapshot["updated_at"]
                    yield f"id: {previous}\nevent: burst\ndata: {json.dumps(snapshot, ensure_ascii=False)}\n\n"
                else:
                    yield ": heartbeat\n\n"
                await sleep(1)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.api_route("/{path:path}", methods=["GET", "HEAD"])
    def route(path: str, request: Request):
        if len(request.url.query) > 256:
            return error("查询参数过长", "query_too_long", 414)
        key = "/" + path
        if key == "/":
            return HTMLResponse(index, headers={"Cache-Control": "no-cache"})
        if key in {"/crowding", "/crowding/"}:
            return HTMLResponse(crowding_page, headers={"Cache-Control": "no-cache"})
        if key == '/api/etf-burst/journal':
            if demo or not radar:
                return error('盘中放量监控未启用', 'burst_disabled', 404)
            day = request.query_params.get('date', '')
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
                return error('查询日期无效', 'invalid_date', 400)
            try:
                after = int(request.query_params.get('after', '0'))
                limit = int(request.query_params.get('limit', '200'))
                if after < 0 or not 1 <= limit <= 1000:
                    raise ValueError()
            except ValueError:
                return error('分页参数无效', 'invalid_query', 400)
            rows = read_journal(event_dir / 'journals' / f'{day}.sqlite3', after, limit,
                                request.query_params.get('symbol'), request.query_params.get('kind'))
            return response({'date': day, 'rows': rows, 'next_after': rows[-1]['seq'] if rows else after})
        if key in {"/api/etf-burst", "/api/etf-burst/dates"}:
            if demo or not radar:
                return error("盘中放量监控未启用", "burst_disabled", 404)
            if key.endswith("/dates"):
                dates = sorted((p.stem for p in (event_dir / "replay").glob("????-??-??.json")), reverse=True)
                return response({"dates": dates})
            day = request.query_params.get("date", "")
            snapshot = None
            if day:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                    return error("查询日期无效", "invalid_date", 400)
                with suppress(OSError, ValueError, KeyError, TypeError):
                    snapshot = validate_burst(read(event_dir / "replay" / f"{day}.json"))
            else:
                snapshot = current_events()
            return response(snapshot) if snapshot else error("暂无盘中放量归档", "burst_not_ready", 503)
        if key == "/api/etf-flow-comparison":
            value = None if demo else flow_comparison.load()
            return response(value) if value is not None else error("盘中主动买与日末申赎尚未归档", "flow_comparison_not_ready", 503)
        if key.startswith("/api/crowding/"):
            module = key.removeprefix("/api/crowding/")
            if module not in crowding_modules:
                return error("指标不存在", "not_found", 404)
            value = None if demo else crowding_modules[module].load()
            return response(value) if value is not None else error("指标尚未归档", "crowding_not_ready", 503)
        if key == "/api/crowding":
            snapshot = None if demo else crowding.load()
            if snapshot is None:
                return error("市场拥挤度数据尚未归档", "crowding_not_ready", 503)
            rows = snapshot["rows"]
            period = request.query_params.get("days", "250")
            table_period = request.query_params.get("table_days", "5")
            if period not in {"20", "60", "125", "250", "750", "1250", "2500"} or table_period not in {
                    "5", "20", "60", "125", "250"}:
                return error("无效的交易日范围", "invalid_range", 400)
            selected = rows[-int(period):]
            sample = [row["style"]["capital"] for row in selected if row["style"]["capital"] is not None]
            return response({**snapshot, "first_date": rows[0]["date"], "last_date": rows[-1]["date"], "archived_days": len(rows),
                             "rows": selected,
                             "table_rows": [{"date": row["date"], "concentration": row["concentration"]}
                                            for row in rows[-int(table_period):]], "capital_stats": {
                                 "count": len(sample), "mean": mean(sample) if sample else None,
                                 "std": pstdev(sample) if sample else None}})
        if key in ASSETS:
            return FileResponse(ROOT / ASSETS[key], headers={"Cache-Control": "no-cache"})
        if key == "/favicon.ico":
            return Response(status_code=204)
        if key not in {"/api/sources", "/api/health", "/api/board", "/api/live", "/api/history",
                       "/api/cumulative", "/api/daily"}:
            return error("页面不存在", "not_found", 404)
        if key == "/api/sources":
            return response(source_status())
        if key == "/api/live" and not demo:
            return live_response(request)
        source_id = request.query_params.get("source") or default_source
        selected = archives.get(source_id)
        if selected is None:
            return error("未知的日频数据源", "source_unknown", 400)
        status_map = {item["id"]: item for item in source_status()["sources"]}
        if not demo and not status_map.get(source_id, {}).get("ready"):
            return error(status_map.get(source_id, {}).get("reason") or "该数据源尚未就绪",
                         "source_not_ready", 409)
        seed = selected.load()
        if seed is None:
            return error("该数据源快照尚未就绪", "source_not_ready", 409)
        provider_label = seed["provider"]
        if key == "/api/health":
            status = source_status()
            return response({"provider": provider_label, "mode": mode, "sources": seed["sources"],
                             "configured": not demo and (cache_path.is_file() or bool(seed.get("live_snapshot"))),
                             "history_built_at": seed["built_at"], "formula": seed["formula"],
                             "flow_policy": seed["flow_policy"], "funds": len(seed["universe"]),
                             "daily_sources": status["sources"], "default_source": default_source,
                             "realtime_sources": status["realtime_sources"],
                             "cloud_update": seed.get("cloud_update"), "update_policy": DEFAULT_STATUS},
                            provider=provider_label)
        if key == "/api/board":
            etag = '"' + sha256(str((source_id, selected.version)).encode()).hexdigest() + '"'
            if request.headers.get("if-none-match") == etag:
                return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
            result = response(seed if demo else {**seed, "update_policy": DEFAULT_STATUS}, provider=provider_label)
            result.headers.update({"ETag": etag, "Cache-Control": "no-cache"})
            return result
        if key == "/api/history":
            history = seed["history"]
            return response({"provider": provider_label, "rows": [{"date": d, "amount": a, "idx": i} for d, a, i in
                                                          zip(history["dates"], history["amount"], history["idx"])]},
                            provider=provider_label)
        if key == "/api/cumulative":
            day = request.query_params.get("date", "")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                return error("查询参数无效", "invalid_date", 400)
            if day not in seed["minutes"]:
                return error("该日期尚未归档", "not_archived", 404)
            return response({"provider": provider_label, "date": day, **seed["minutes"][day]},
                            provider=provider_label)
        if key == "/api/daily":
            if not demo:
                return error("日度数据由后台定时更新", "visitor_collection_disabled", 410)
            batch = request.query_params.get("batch", "")
            if not re.fullmatch(r"\d{1,2}", batch) or int(batch) * 8 >= len(seed["universe"]):
                return error("请选择有效的数据批次", "invalid_batch", 400)
            offset = int(batch) * 8
            dates = seed["history"]["dates"][-3:]
            return response({"provider": provider_label, "dates": dates, "funds": [
                {"symbol": f["symbol"], "days": {d: {"flow": seed["demo"]["fund_flows"][d].get(f["symbol"])} for d in dates}}
                for f in seed["universe"][offset:offset + 8]]}, provider=provider_label)
        if demo:
            return response(demo_live(seed), provider="Demo")
        return error("接口不存在", "not_found", 404)

    @app.middleware("http")
    async def headers(request, call_next):
        result = await call_next(request)
        result.headers.update({"X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin"})
        return result

    return app


app = create_app()
