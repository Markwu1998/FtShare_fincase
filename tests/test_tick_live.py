import asyncio
from datetime import datetime, timedelta
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from starlette.requests import Request

from etf_backend.burst import BurstDay, END, RULE_VERSION, total_window, validate_metric, window_totals
from etf_backend.collect import publish, read, ROOT
from etf_backend.model import BASKET, INDEX, SLOTS, fund_flows
from etf_backend.provider import CHINA
from etf_backend.realtime import PushSession
from etf_backend.web import create_app, validate_burst


class TickLiveTest(unittest.TestCase):
    def test_unknown_split_without_reconciliation_preserves_a_flow_gap(self):
        fund = {"symbol": "510050.SH", "listed_date": "2005-01-01", "inception_date": "2004-01-01"}
        records = {"shares": [["2026-11-12", 100], ["2026-11-13", 110]],
                   "navs": [["2026-11-12", 10, 1], ["2026-11-13", 10 / 1.1, 1]], "splits": [],
                   "unverified_split_dates": ["2026-11-13"]}
        row = fund_flows(fund, records, ["2026-11-12", "2026-11-13"])["2026-11-13"]
        self.assertIsNone(row["flow"])
        self.assertIn("split_evidence_missing", row["issues"])

    def test_warm_recovers_bar_at_current_completed_minute(self):
        import pandas as pd
        now = datetime.now(CHINA).replace(hour=14, minute=0, second=20, microsecond=0)
        day, anchor = now.date().isoformat(), (now - timedelta(days=1)).date().isoformat()
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return now
        class Feed:
            def get_trading_dates(self, *_): return [anchor, day]
            def get_price(self, *_args, **kwargs):
                if kwargs.get("end_date") != day: return None
                index = pd.MultiIndex.from_tuples([(BASKET[0].replace(".SH", ".XSHG").replace(".SZ", ".XSHE"),
                           pd.Timestamp(day + " 14:00"))], names=["order_book_id", "date"])
                return pd.DataFrame({"close": [1.1], "total_turnover": [1e6], "volume": [1000]}, index=index)
        funds = read(ROOT / "config/universe.json")["funds"]
        with tempfile.TemporaryDirectory() as directory, patch("etf_backend.realtime.datetime", Clock), patch(
                "etf_backend.realtime.provider.bars", return_value=[(s, 0, 1, 0) for s in BASKET + [INDEX]]):
            root = Path(directory)
            session = PushSession(Feed(), day, funds, root / "board.json", root, .75)
            session.warm(Feed())
            self.assertIn((BASKET[0], "14:00"), session.minutes)
    def test_persisted_baseline_skips_queries_and_invalidates_quantile(self):
        now = datetime.now(CHINA)
        day, anchor = now.date().isoformat(), (now - timedelta(days=1)).date().isoformat()
        funds = [f for f in read(ROOT / "config/universe.json")["funds"] if f["symbol"] in BASKET]
        class Feed:
            calls = 0
            def get_trading_dates(self, *_): return [anchor, day]
            def get_price(self, *_args, **_kwargs):
                self.calls += 1
                return None
        with tempfile.TemporaryDirectory() as directory, patch("etf_backend.realtime.provider.bars", return_value=[]):
            root = Path(directory)
            publish(root / "baselines" / (day + ".json"), {
                "identity": {"day": day, "as_of": anchor, "symbols": BASKET, "quantile": .75, "rule_version": RULE_VERSION},
                "ready": True, "index": [["000300.XSHG", "09:33", .01]],
                "baseline": {s: {"daily": [240, 240], "windows": [[k, i, [[1, 1], [1, 1]]]
                             for k in (1, 2, 5, 10) for i in range(END + 1)]} for s in BASKET}})
            feed = Feed()
            session = PushSession(feed, day, funds, root / "live.json", root, .75)
            self.assertEqual(feed.calls, 0)
            self.assertTrue(session.baseline_ready)
            PushSession(feed, day, funds, root / "live.json", root, .8)
            self.assertGreater(feed.calls, 0)
    def test_rolling_optimization_preserves_missing_lunch_and_end_boundaries(self):
        rng = random.Random(7)
        values = [None if rng.random() < .08 else rng.random() * 1e7 for _ in SLOTS]
        for length in (1, 2, 5, 10):
            actual = window_totals(values, length)
            for i in range(END + 1):
                expected = total_window(values, i, length)
                if expected is None:
                    self.assertIsNone(actual[i])
                else:
                    self.assertAlmostEqual(actual[i], expected, places=5)

    def test_metric_rejects_legacy_volume_archives_and_exports(self):
        for value in ({}, {"metric": "volume", "unit": "shares", "schema_version": 2}):
            with self.assertRaises(ValueError):
                validate_metric(value)
        value = BurstDay("2026-11-13", {}, .75, symbols=[]).payload()
        self.assertEqual(validate_burst(value)["unit"], "CNY")
        self.assertEqual(value["rule_version"], "turnover-p75-v1")

    def test_daily_board_etag_skips_unchanged_payload(self):
        app = create_app("demo")
        route = next(r.endpoint for r in app.routes if r.path == "/{path:path}")
        def request(headers):
            return route("api/board", Request({"type": "http", "path": "/api/board", "query_string": b"", "headers": headers}))
        first = request([])
        self.assertEqual(first.status_code, 200)
        second = request([(b"if-none-match", first.headers["etag"].encode())])
        self.assertEqual(second.status_code, 304)
        self.assertEqual(second.body, b"")

    def test_ticks_publish_before_first_bar_and_do_not_change_minute_signals(self):
        now = datetime.now(CHINA).replace(hour=14, minute=0, second=0, microsecond=0)
        day = now.date().isoformat()
        anchor = (now - timedelta(days=1)).date().isoformat()
        class Feed:
            def get_trading_dates(self, *_): return [anchor, day]
            def get_price(self, *_args, **_kwargs): return None
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return now
        funds = [f for f in read(ROOT / "config/universe.json")["funds"] if f["symbol"] in BASKET]
        with tempfile.TemporaryDirectory() as directory, patch("etf_backend.realtime.datetime", Clock), patch(
                "etf_backend.realtime.provider.bars", return_value=[(s, 0, 1, 0) for s in BASKET + [INDEX]]):
            root = Path(directory)
            session = PushSession(Feed(), day, funds, root / "board.json", root, .75)
            def tick(symbol, seconds, price=1.1, cash=1e6):
                code = symbol.replace(".SH", ".XSHG").replace(".SZ", ".XSHE")
                return session.handle({"channel": "tick_" + code, "order_book_id": code,
                        "datetime": (now - timedelta(seconds=seconds)).isoformat(), "last": price,
                        "total_turnover": cash, "volume": 1000, "bid": [1.099], "ask": [1.101], "iopv": 1.1}, "received")
            tick(BASKET[0], 2)
            session.flush()
            def snapshot(): return json.loads(read(root / "board.json")["entries"]["live:rqdata:snapshot"])["value"]
            value = snapshot()
            self.assertEqual(value["quote_coverage"], 1)
            self.assertIsNone(value["amount"])
            self.assertIsNone(value["last_complete_minute"])
            self.assertTrue(all(v is None for v in value["bars"]))
            for symbol in BASKET[1:] + [INDEX]: tick(symbol, 2)
            session.flush()
            self.assertAlmostEqual(snapshot()["amount"], .2)
            minute_version = session.minute_version
            tick(BASKET[0], 1, 1.2, 2e6)
            session.flush()
            value = snapshot()
            row = next(r for r in value["table"] if r["symbol"] == BASKET[0])
            self.assertEqual(row["price"], 1.2)
            self.assertEqual(row["bid"], 1.099)
            self.assertAlmostEqual(value["amount"], .21)
            self.assertEqual(value["index_quote"]["price"], 1.1)
            self.assertEqual(session.minute_version, minute_version)
            self.assertEqual(session.detector.events, [])
            before = (root / "board.json").stat().st_mtime_ns
            session.flush()
            self.assertEqual(before, (root / "board.json").stat().st_mtime_ns)
            tick(BASKET[0], 2, 9, 9e6)  # out of order
            self.assertFalse(tick(BASKET[0], 0, 9, 1))  # cumulative turnover regressed
            self.assertEqual(session.quotes[BASKET[0]][1], 1.2)

    def test_live_api_and_stream_work_without_daily_seed_and_ready_refreshes(self):
        day = datetime.now(CHINA).date().isoformat()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = create_app("live", seed_file=root / "absent.json", cache_file=root / "live.json")
            route = next(r.endpoint for r in app.routes if r.path == "/{path:path}")
            def request(path): return route(path, Request({"type": "http", "path": "/" + path, "query_string": b"", "headers": []}))
            self.assertFalse(json.loads(request("api/sources").body)["realtime_sources"][0]["ready"])
            value = {"provider": "RQData", "date": day, "slots": SLOTS, "feed": "push",
                     "fetched_at": datetime.now(CHINA).isoformat(),
                     **{key: [None] * 240 for key in ("bars", "coverage", "closes", "cumulative")}}
            def write(value): publish(root / "live.json", {"entries": {"live:rqdata:snapshot": json.dumps({"value": value})}})
            write(value)
            self.assertTrue(json.loads(request("api/sources").body)["realtime_sources"][0]["ready"])
            self.assertEqual(request("api/live").status_code, 200)
            endpoint = next(r.endpoint for r in app.routes if r.path == "/api/live/stream")
            class StreamRequest:
                headers = {}
                query_params = {}
                async def is_disconnected(self): return False
            async def check():
                result = await endpoint(StreamRequest())
                iterator = result.body_iterator
                self.assertIn("event: live", await iterator.__anext__())
                self.assertEqual(await iterator.__anext__(), ": heartbeat\n\n")
                write({**value, "date": "2026-01-01"})
                self.assertIn("event: unavailable", await iterator.__anext__())
                await iterator.aclose()
            asyncio.run(check())
            self.assertEqual(request("api/live").status_code, 503)

    def test_replay_is_opt_in_and_never_becomes_realtime_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = {"provider": "RQData", "date": "2026-11-13", "slots": SLOTS, "replay": True,
                     "fetched_at": "2026-11-13T15:00:00+08:00",
                     **{key: [None] * 240 for key in ("bars", "coverage", "closes", "cumulative")}}
            publish(root / "cache.json", {"entries": {"live:rqdata:snapshot": json.dumps({"value": value})}})
            for allowed in (False, True):
                app = create_app("live", seed_file=root / "absent", cache_file=root / "cache.json", allow_replay=allowed)
                endpoint = next(r.endpoint for r in app.routes if r.path == "/{path:path}")
                request = Request({"type": "http", "path": "/api/live", "query_string": b"", "headers": []})
                self.assertEqual(endpoint("api/live", request).status_code, 200 if allowed else 503)
