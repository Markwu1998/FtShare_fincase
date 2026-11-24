"""Offline Chrome acceptance: synthetic ticks -> collector -> SSE -> visible UI.

Run explicitly: python -B tests/browser_tick_smoke.py
Uses the project's websockets dependency and installed Chrome; no market login.
"""
from datetime import datetime, timedelta
import base64
import json
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import threading
import time
import urllib.request
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from websockets.sync.client import connect
from etf_backend.collect import ROOT, publish, read
from etf_backend.flow_comparison import METHOD
from etf_backend.model import BASKET, INDEX, SLOTS
from etf_backend.provider import CHINA
from etf_backend.realtime import PushSession
from etf_backend.web import create_app


def main():
    chrome = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
    if not chrome.is_file():
        raise RuntimeError("Install Chrome or set the test's chrome path")
    with tempfile.TemporaryDirectory(prefix="etf-tick-smoke-") as directory:
        root = Path(directory)
        now = datetime.now(CHINA)
        day = now.date().isoformat()
        anchor = (now - timedelta(days=1)).date().isoformat()
        seed = read(ROOT / "data/demo-seed.json")
        seed.pop("demo")
        seed.update(provider="RQData", source={"id": "rqdata"})
        positions = [seed["minute_slots"].index(slot) for slot in SLOTS]
        seed["minute_slots"] = SLOTS
        for minute in seed["minutes"].values():
            for key in ("bars", "cumulative"):
                minute[key] = [minute[key][i] for i in positions]
        publish(root / "seed.json", seed)
        funds = read(ROOT / "config/universe.json")["funds"]
        class Feed:
            def get_trading_dates(self, *_): return [anchor, day]
            def get_price(self, *_args, **_kwargs): return None
        with patch("etf_backend.realtime.provider.bars", return_value=[(s, 0, 1, 0) for s in BASKET + [INDEX]]):
            session = PushSession(Feed(), day, funds, root / "live.json", root / "burst", .75)
        def tick(symbol, price=1.1, cash=1e6):
            code = symbol.replace(".SH", ".XSHG").replace(".SZ", ".XSHE")
            session.handle({"channel": "tick_" + code, "order_book_id": code,
                            "datetime": datetime.now(CHINA).isoformat(), "last": price,
                            "total_turnover": cash, "bid": [price - .001], "ask": [price + .001],
                            "iopv": 1.1, "volume": 1000}, "synthetic")
        historical = ROOT / ".local/tick-history-replay/replay"
        if historical.is_dir():
            (root / "burst/replay").mkdir(parents=True)
            for archive in historical.glob("????-??-??.json"):
                shutil.copyfile(archive, root / "burst/replay" / archive.name)
        real_replay = ROOT / ".local/day-replay-offline-ih/replay/2026-10-21.json"
        if real_replay.is_file():
            (root / "burst/replay").mkdir(parents=True, exist_ok=True)
            shutil.copyfile(real_replay, root / "burst/replay/2026-10-21.json")
        comparison = root / "flow-comparison.json"
        publish(comparison, {"schema_version": 1, "method": METHOD, "unit": "CNY_1e8", "rows": [
            {"day": date, "symbol": symbol, "gross_yi": 10, "active_buy_yi": 6, "active_sell_yi": 2,
             "net_flow_yi": None if date == "2026-11-20" and i == 0 else -2,
             "flow_issues": "share_missing" if date == "2026-11-20" and i == 0 else ""}
            for date in ("2026-11-19", "2026-11-20") for i, symbol in enumerate(BASKET)
        ]})
        app = create_app("live", seed_file=root / "seed.json", cache_file=root / "live.json",
                         burst_dir=root / "burst", crowding_file=root / "crowding.json", radar=True,
                         flow_comparison_file=comparison)
        config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
        server = uvicorn.Server(config)
        sock = config.bind_socket()
        port = sock.getsockname()[1]
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
        thread.start()
        process = None
        try:
            process = subprocess.Popen([str(chrome), "--headless=new", "--disable-gpu", "--no-first-run",
                                        "--proxy-server=direct://", "--proxy-bypass-list=*",
                                        "--remote-debugging-port=0", "--user-data-dir=" + str(root / "chrome"),
                                        f"http://127.0.0.1:{port}/"], stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            deadline = time.monotonic() + 25
            active = root / "chrome/DevToolsActivePort"
            while not active.exists() and time.monotonic() < deadline: time.sleep(.1)
            debug_port = int(active.read_text().splitlines()[0])
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(f"http://127.0.0.1:{debug_port}/json/list") as response:
                page = next(p for p in json.load(response) if p["type"] == "page")
            with connect(page["webSocketDebuggerUrl"], max_size=16 * 1024 * 1024, proxy=None) as connection:
                sequence = 0
                errors = []
                def call(method, params=None):
                    nonlocal sequence
                    sequence += 1
                    connection.send(json.dumps({"id": sequence, "method": method, "params": params or {}}))
                    while True:
                        response = json.loads(connection.recv(timeout=10))
                        if response.get("method") in {"Runtime.exceptionThrown", "Log.entryAdded"}:
                            errors.append(response["params"])
                        if response.get("id") == sequence:
                            if "error" in response: raise RuntimeError(response["error"])
                            return response.get("result", {})
                def evaluate(expression):
                    result = call("Runtime.evaluate", {"expression": expression, "returnByValue": True})
                    if "exceptionDetails" in result: raise RuntimeError(result["exceptionDetails"])
                    return result["result"].get("value")
                def wait(expression, expected=True):
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        if evaluate(expression) == expected: return
                        time.sleep(.1)
                    raise AssertionError(expression + " did not reach " + repr(expected) + " " +
                                         str(evaluate("JSON.stringify({url:location.href,body:document.body?.innerText.slice(0,1800)})")) +
                                         str(errors[-3:]))
                call("Runtime.enable")
                call("Log.enable")
                call("Emulation.setDeviceMetricsOverride", {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
                wait("window.__ETF_APP_READY===true")
                wait("document.getElementById('flowCompareSummary').textContent.includes('39/40')")
                wait("document.querySelectorAll('#flowCompareTable tbody tr').length", 20)
                evaluate("document.getElementById('flowCompareNext').click()")
                wait("document.querySelector('#flowCompareTable tbody tr td').textContent", "2026-11-19")
                evaluate("document.getElementById('flowCompareStart').value='2026-11-20';document.getElementById('flowCompareStart').dispatchEvent(new Event('change'))")
                wait("document.getElementById('flowCompareSummary').textContent.includes('19/20')")
                evaluate("document.querySelector('#flowCompareViews [data-view=day]').click()")
                wait("document.querySelector('#flowCompareTable tbody tr').textContent", "2026-11-20114.0038.00-38.00+152.0019/20")
                evaluate("document.querySelector('#flowCompareViews [data-view=symbol]').click()")
                wait("document.querySelectorAll('#flowCompareTable tbody tr').length", 20)
                evaluate("document.getElementById('flowCompareReset').click()")
                wait("document.getElementById('flowCompareSummary').textContent.includes('39/40')")
                assert evaluate("document.querySelector('#realtimeSourceMenu [data-source=\"rqdata\"]').disabled")
                for symbol in BASKET + [INDEX]: tick(symbol)
                session.flush()
                selector = '[data-symbol="' + BASKET[0] + '"]'
                price = "document.querySelector(" + json.dumps(selector) + ")?.children[2]?.textContent"
                wait(price, "1.1000")
                assert not evaluate("document.querySelector('#realtimeSourceMenu [data-source=\"rqdata\"]').disabled")
                evaluate("window.__draws=0;window.__chart=echarts.getInstanceByDom(document.getElementById('chart'));window.__setOption=__chart.setOption.bind(__chart);__chart.setOption=(...args)=>{__draws++;return __setOption(...args)}")
                before = evaluate("window.__draws")
                start = time.monotonic()
                tick(BASKET[0], 1.25, 2e6)
                session.flush()
                wait(price, "1.2500")
                latency = time.monotonic() - start
                assert evaluate("window.__draws") == before, "Tick redrew the minute chart"
                assert evaluate("document.getElementById('overviewAmount').textContent").startswith("0.2")
                for symbol in BASKET + [INDEX]:
                    session.bar(symbol, now.replace(hour=9, minute=31, second=0, microsecond=0), 1.1, 1e6, 1000)
                session.flush()
                wait("window.__draws>0")
                assert evaluate("__chart.getOption().series[0].data[0].value") == .2
                # A candidate is a separate packet field, not a formal event.
                candidate_report = session.detector.payload()
                candidate_report["candidates"] = [{"symbol": BASKET[0], "as_of": datetime.now(CHINA).isoformat(),
                    "minute_end": "09:35", "provisional": True,
                    "signal": {"level": 2, "window": 5, "amount": 5e8}}]
                candidate_report['tick_candidates'] = [{'symbol': BASKET[0], 'method': '30s-persistence',
                    'as_of': datetime.now(CHINA).isoformat(), 'window_seconds': 30, 'amount': 6e7,
                    'ratio': 6, 'persistence': 5 / 6, 'provisional': True}]
                candidate_report['accounting'] = {'minutes': [[BASKET[0], '09:31', 1e8, 2e7, '09:35']]}
                candidate_report['journal'] = {'enabled': True}
                evaluate("window.__spoken=0;window.speechSynthesis.speak=()=>{window.__spoken++}")
                evaluate("document.getElementById('burstSound').click()")
                spoken_before_candidate = evaluate("window.__spoken")  # enable confirmation is intentional
                publish(root / "burst/live.json", candidate_report)
                wait("document.getElementById('burstCandidates').textContent.includes('未确认、不播报')")
                wait("document.getElementById('tickFactorCandidates').textContent.includes('30秒')")
                wait("document.getElementById('burstGross').textContent", '1.00')
                wait("document.getElementById('burstExcess').textContent", '0.80')
                assert evaluate("document.getElementById('burstFlow').textContent") == '—'
                evaluate("fetch(document.getElementById('burstJournal').href).then(r=>r.json()).then(v=>window.__journalOK=Array.isArray(v.rows))")
                wait('window.__journalOK===true')
                assert evaluate("document.getElementById('burstEvents').children.length") == 0
                assert evaluate("window.__spoken") == spoken_before_candidate
                futures_report = {**candidate_report, "futures_monitor": {"state": "receiving", "products": ["IF"]}}
                futures_report["updated_at"] = datetime.now(CHINA).isoformat()
                signal = {"level": 2, "window": 5, "start": "09:31", "end": "09:35",
                          "amount": 1e8, "ratios": [6, 7], "daily": [.15, .16]}
                futures_report["events"] = [{"id": day + "/futures-smoke", "symbol": BASKET[0], "level": 2,
                    "first": "09:35", "last": "09:35", "peak": signal, "updates": [signal],
                    "evidence": {"futures": {"product": "IF", "status": "sell_side_evidence",
                        "window_start": "09:31", "window_end": "09:35", "etf_turnover_cny": 1e8,
                        "etf_volume_shares": 80000, "net_oi_cny": 9e7, "estimated_sell_cny": 8e7,
                        "estimated_sell_oi_cny": 6e7, "classified_share": .65, "covered": 4, "expected": 4,
                        "directions": {"sell": {"oi_decrease_cny": -2e7}},
                        "contracts": [{"code": "IF2612", "tenor": "次近月", "values": {
                            "delta_oi": 20, "net_oi_cny": 9e7, "estimated_sell_cny": 8e7, "estimated_sell_oi_cny": 6e7,
                            "directions": {"sell": {"oi_decrease_cny": -1e7}}}}]}}}]
                publish(root / "burst/live.json", futures_report)
                wait("!!document.querySelector('.radar-futures-detail')")
                evaluate("document.querySelector('.radar-futures-detail summary').click()")
                assert evaluate("document.querySelector('.radar-futures-detail[open]').textContent.includes('主动卖成交额估算 0.80 亿')")
                assert evaluate("document.querySelector('.radar-futures-detail[open]').textContent.includes('IF2612')")
                evaluate("document.getElementById('futuresMetric').value='sell.oi_decrease_cny';document.getElementById('futuresMetric').dispatchEvent(new Event('change'))")
                assert evaluate("document.querySelector('.radar-futures-detail[open]').textContent.includes('卖侧区间持仓减少额 -0.20 亿')")
                assert evaluate("document.querySelector('.radar-futures-detail[open] li').textContent.includes('-0.10 亿')")
                assert evaluate("document.getElementById('futuresColumn').textContent") == '卖侧区间持仓减少额'
                evaluate("document.getElementById('futuresMetric').value='estimated_sell_oi_cny';document.getElementById('futuresMetric').dispatchEvent(new Event('change'))")
                assert evaluate("document.querySelector('.radar-futures-detail[open]').textContent.includes('卖侧增仓估算额（成交均价） 0.60 亿')")
                evaluate("document.getElementById('futuresMetric').value='estimated_sell_cny';document.getElementById('futuresMetric').dispatchEvent(new Event('change'));document.getElementById('futuresMetric').scrollIntoView({block:'center',behavior:'instant'})")
                metrics_screenshot = ROOT / '.local/futures-metric-smoke.png'
                metrics_screenshot.write_bytes(base64.b64decode(call('Page.captureScreenshot', {'format':'png'})['data']))
                publish(root / "burst/live.json", {**candidate_report, "updated_at": datetime.now(CHINA).isoformat()})
                wait("document.getElementById('burstEvents').children.length===0")
                evaluate("document.getElementById('tickFactorCandidates').scrollIntoView({block:'center',behavior:'instant'});document.body.insertAdjacentHTML('beforeend','<div id=\"__synthetic\" style=\"position:fixed;bottom:0;left:0;z-index:99999;background:#fff5cf;padding:8px\">离线合成验收 · 非真实成交或告警</div>')")
                upgrade_screenshot = ROOT / '.local/tick-burst-upgrade-ui.png'
                upgrade_screenshot.write_bytes(base64.b64decode(call('Page.captureScreenshot', {'format': 'png'})['data']))
                call('Emulation.setDeviceMetricsOverride', {'width':390,'height':844,'deviceScaleFactor':1,'mobile':False})
                wait("getComputedStyle(document.querySelector('.burst-metrics')).gridTemplateColumns.split(' ').length===2")
                wait("getComputedStyle(document.getElementById('chartNavigation')).visibility==='hidden'&&document.getElementById('chartNavigation').getBoundingClientRect().right<=0")
                evaluate("document.querySelector('.burst-metrics').scrollIntoView({block:'center',behavior:'instant'})")
                mobile_screenshot = ROOT / '.local/tick-burst-mobile.png'
                mobile_screenshot.write_bytes(base64.b64decode(call('Page.captureScreenshot', {'format':'png'})['data']))
                call('Emulation.setDeviceMetricsOverride', {'width':1440,'height':1000,'deviceScaleFactor':1,'mobile':False})
                evaluate("document.getElementById('__synthetic').remove()")
                charts = evaluate("['chart','chartCum','chartTypCase','chartTypNorm','chartBroad','chartDAmt','chartDNf','chartAmt','chartNf'].filter(id=>echarts.getInstanceByDom(document.getElementById(id))).length")
                assert charts == 9, "Missing original charts"
                # Force SSE unavailable on reload, then verify the 2-second fallback.
                call("Network.enable")
                call("Network.setBlockedURLs", {"urls": ["*api/live/stream*"]})
                call("Page.reload", {"ignoreCache": True})
                wait("window.__ETF_APP_READY===true")
                wait(price, "1.2500")
                tick(BASKET[0], 1.3, 3e6)
                session.flush()
                wait(price, "1.3000")
                history_checked = False
                if (historical / "2026-11-13.json").is_file():
                    wait("!!document.querySelector('#burstDate option[value=\"2026-11-13\"]')")
                    evaluate("document.getElementById('burstDate').value='2026-11-13';document.getElementById('burstDate').dispatchEvent(new Event('change'))")
                    wait("document.getElementById('burstStatus').textContent.includes('历史回放')")
                    wait("!!document.querySelector('#burstEvents [data-burst-id]')")
                    evaluate("document.querySelector('#burstEvents [data-burst-id]').click()")
                    wait("!document.getElementById('burstDetail').hidden")
                    assert evaluate("document.getElementById('burstSound').disabled")
                    tick_archive = read(root / "burst/replay/2026-11-13.json")
                    tick_archive["feed"] = "tick-replay"
                    tick_archive["candidate_history"] = [{**candidate_report["candidates"][0],
                                                          "as_of": "2026-11-13T09:34:20+08:00"}]
                    publish(root / "burst/replay/2026-11-13.json", tick_archive)
                    evaluate("document.getElementById('burstDate').dispatchEvent(new Event('change'))")
                    wait("document.getElementById('burstCandidates').textContent.includes('Tick 历史候选')")
                    evaluate("document.getElementById('burstTime').value='09:34';document.getElementById('burstTime').dispatchEvent(new Event('change'))")
                    wait("document.getElementById('burstCandidates').textContent.includes('暂无 Tick 候选')")
                    history_checked = True
                if real_replay.is_file():
                    evaluate("document.getElementById('burstTime').value='14:55';document.getElementById('burstDate').value='2026-10-21';document.getElementById('burstDate').dispatchEvent(new Event('change'))")
                    wait("document.getElementById('futuresStatus').textContent.includes('按行情时间重建')")
                    assert not evaluate("!!document.querySelector('.radar-futures-detail')")
                    evaluate("document.getElementById('burstTime').value='14:56';document.getElementById('burstTime').dispatchEvent(new Event('change'))")
                    wait("document.querySelector('.radar-futures-detail summary')?.textContent.includes('IH · 4.02 亿')")
                    evaluate("document.getElementById('burstTime').value='15:00';document.getElementById('burstTime').dispatchEvent(new Event('change'))")
                    wait("document.querySelector('.radar-futures-detail summary')?.textContent.includes('IH · 6.10 亿')")
                evaluate("document.getElementById('etfTable').scrollIntoView({block:'center',behavior:'instant'})")
                wait("document.getElementById('etfTable').getBoundingClientRect().top>=0&&document.getElementById('etfTable').getBoundingClientRect().top<innerHeight")
                evaluate("document.body.insertAdjacentHTML('beforeend','<div style=\"position:fixed;bottom:0;left:0;z-index:99999;background:#fff5cf;padding:8px\">离线合成行情验收 · 非真实交易价格</div>')")
                screenshot = ROOT / ".local/tick-ui-smoke.png"
                screenshot.write_bytes(base64.b64decode(call("Page.captureScreenshot", {"format": "png"})["data"]))
                print(json.dumps({"mode": "synthetic offline", "price_before": "1.1000", "price_after": "1.2500",
                                  "tick_to_dom_seconds": round(latency, 3), "tick_minute_redraws": 0,
                                  "bar_updates_minute_chart": True, "original_charts": charts,
                                  "sse_failure_poll_fallback": True, "cached_history_replay_ui": history_checked,
                                  "candidate_shown_without_formal_event_or_speech": True,
                                  'second_candidates_and_accounting_visible': True, 'journal_api': True,
                                  'futures_contract_estimates_visible': True,
                                  'upgrade_screenshot': str(upgrade_screenshot),
                                  'mobile_accounting_columns': 2, 'mobile_screenshot': str(mobile_screenshot),
                                  "screenshot": str(screenshot)}, ensure_ascii=False))
        finally:
            if process:
                process.terminate()
                process.wait(timeout=10)
            server.should_exit = True
            thread.join(timeout=10)
            sock.close()


if __name__ == "__main__":
    main()
