from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from etf_backend.collect import ROOT, history_snapshot, publish, read, validate_replacement
from etf_backend.burst import BurstDay, detect, quantile, total_window
from etf_backend.crowding import add_percentiles, basket_total, turnover_metrics
from etf_backend.crowding_extra import released_values, shsz_symbol
from etf_backend.model import BASKET, INDEX, SLOTS, fund_flows
from etf_backend.realtime import PushSession, feed_time
from etf_backend.web import Archive, validate_seed


class BackendTests(unittest.TestCase):
    def test_burst_direct_confirmation_and_replay_bounds(self):
        symbol = BASKET[0]
        daily = [1000, 1000]
        baseline = {symbol: (daily, {(k, i): ([1, 2], [1, 1]) for k in (1, 2, 5, 10) for i in range(240)})}
        state = BurstDay("2026-11-13", baseline, .75)
        for slot, count in zip(SLOTS[:5], [1, 1, 1, 1, 120]):
            state.add(symbol, slot, count, recovered=True)
        self.assertEqual([(e["first"], e["level"], e["recovered"]) for e in state.events],
                         [("09:35", 2, True)])
        state.add(symbol, "09:34", 9999)
        state.add(symbol, "09:35", 9999)
        self.assertEqual(len(state.events), 1)
        state.add(symbol, "09:38", 1100)
        self.assertEqual(len(state.events), 1)
        self.assertIsNone(total_window(state.values[symbol], SLOTS.index("13:01"), 2))
        self.assertIsNone(total_window([1] * 240, SLOTS.index("14:58"), 5))
        self.assertEqual(quantile([0, 10, 20, 30], .75), 22.5)
        self.assertEqual(feed_time(20261113093500000).isoformat(), "2026-11-13T09:35:00+08:00")
        self.assertIsNone(detect([0, 0, 0, 0, 120], 4, (daily, {})))

    def test_burst_replay_endpoint_serves_archives_without_etf_seed(self):
        from starlette.requests import Request
        from etf_backend.web import create_app

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            publish(root / "replay/2026-11-13.json", {"schema_version": 2, "metric": "turnover", "unit": "CNY", "provider": "RQData", "date": "2026-11-13",
                    "replay": True, "events": [], "updated_at": "2026-11-13T15:00:00Z"})
            app = create_app("live", seed_file=root / "missing.json", burst_dir=root, radar=True)
            endpoint = next(r.endpoint for r in app.routes if r.path == "/{path:path}")
            def request(path, query=b""):
                return endpoint(path, Request({"type": "http", "path": "/" + path, "query_string": query, "headers": []}))
            self.assertEqual(json.loads(request("api/etf-burst/dates").body), {"dates": ["2026-11-13"]})
            self.assertTrue(json.loads(request("api/etf-burst", b"date=2026-11-13").body)["replay"])
            self.assertEqual(request("api/etf-burst", b"date=../live").status_code, 400)

    def test_push_updates_existing_board_and_separate_burst_archive(self):
        from datetime import datetime, timedelta
        from unittest.mock import patch
        from etf_backend.provider import CHINA

        day = (datetime.now(CHINA) - timedelta(days=1)).date().isoformat()
        anchor = (datetime.fromisoformat(day) - timedelta(days=1)).date().isoformat()
        start = datetime.fromisoformat(day + "T09:31:00").replace(tzinfo=CHINA)
        class Feed:
            def get_trading_dates(self, *_):
                return [anchor, day]

            def get_price(self, *_args, **_kwargs):
                return None

        funds = [f for f in read(ROOT / "config/universe.json")["funds"] if f["symbol"] in BASKET]
        reference = {(length, i): ([1, 2], [1, 1]) for length in (1, 2, 5, 10) for i in range(240)}
        with tempfile.TemporaryDirectory() as directory, patch("etf_backend.realtime.provider.bars",
                return_value=[(symbol, 0, 1, 0) for symbol in BASKET + [INDEX]]):
            root = Path(directory)
            session = PushSession(Feed(), day, funds, root / "board.json", root, .75)
            session.detector.baseline = {s: ([1000, 1000], reference) for s in BASKET}
            for i in range(5):
                t = start + timedelta(minutes=i)
                for s in BASKET + [INDEX]:
                    volume = 1000 if s == BASKET[0] and i == 4 else 1
                    code = s.replace(".SH", ".XSHG").replace(".SZ", ".XSHE")
                    dt = t.strftime("%Y%m%d%H%M%S") + "000"
                    bar = {"channel": "bar_" + code, "order_book_id": code, "datetime": int(dt),
                           "close": 1, "total_turnover": volume, "volume": volume}
                    self.assertTrue(session.handle(bar, "test"))
                    session.handle({**bar, "volume": 99999, "total_turnover": 99999}, "test")
                    if s != INDEX:
                        session.handle({**bar, "channel": "tick_" + code, "last": 1}, "test")
            session.flush()
            live = read(root / "live.json")
            value = json.loads(read(root / "board.json")["entries"]["live:rqdata:snapshot"])["value"]
            self.assertEqual(live["events"][-1]["level"], 3)
            self.assertEqual(live["through"], "09:35")
            self.assertEqual(value["last_complete_minute"], "09:35")
            self.assertEqual(value["feed"], "push")
            self.assertEqual(value["coverage"][:5], [20] * 5)

    def test_event_stream_reconnect_skips_last_seen_update(self):
        import asyncio
        from datetime import datetime
        from etf_backend.provider import CHINA
        from etf_backend.web import create_app

        class Request:
            headers = {"last-event-id": "v1"}

            async def is_disconnected(self):
                return False

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = {"schema_version": 2, "metric": "turnover", "unit": "CNY", "provider": "RQData", "date": datetime.now(CHINA).date().isoformat(),
                        "events": [], "updated_at": "v1"}
            publish(root / "live.json", snapshot)
            app = create_app("live", radar=True, burst_dir=root)
            endpoint = next(r.endpoint for r in app.routes if r.path == "/api/etf-burst/stream")
            async def check():
                result = await endpoint(Request())
                stream = result.body_iterator
                self.assertEqual(await stream.__anext__(), ": heartbeat\n\n")
                publish(root / "live.json", {**snapshot, "updated_at": "v2"})
                self.assertIn("id: v2\nevent: burst", await stream.__anext__())
                await stream.aclose()
            asyncio.run(check())

    def test_macro_ratios_respect_release_date_and_contiguous_quarters(self):
        events = [
            ("2024-04-16", "2024-03-31", 2),
            ("2024-10-18", "2024-09-30", 4),
            ("2025-01-17", "2024-12-31", 5),
            ("2025-03-01", "2024-06-30", 3),
            ("2025-04-16", "2025-03-31", 6),
        ]
        values = released_values(events, ["2025-02-28", "2025-03-03", "2025-04-16"], trailing_gdp=True)
        self.assertNotIn("2025-02-28", values)
        self.assertEqual(values["2025-03-03"], (14, "2024-12-31", "2025-03-01"))
        self.assertEqual(values["2025-04-16"][0], 18)
        self.assertEqual(shsz_symbol("600519.SH"), "600519.XSHG")
        self.assertEqual(shsz_symbol("000001"), "000001.XSHE")
        self.assertIsNone(shsz_symbol("920008"))

    def test_crowding_distinguishes_suspensions_from_missing_quotes(self):
        instruments = {"A", "B", "C", "D"}
        amounts = {"A": 6e8, "B": 4e8, "D": 0}
        partial = turnover_metrics(instruments, amounts, set())
        self.assertEqual(partial["covered"], 3)
        self.assertIsNone(partial["tiers"]["1"]["share"])
        complete = turnover_metrics(instruments, amounts, {"C"})
        self.assertEqual(complete["traded"], 2)
        self.assertEqual(complete["tiers"]["1"], {"count": 1, "amount": 6, "share": 60})
        self.assertEqual(basket_total(instruments, amounts, {"C"}), 1e9)
        self.assertIsNone(basket_total(instruments, amounts, positive=True))

        rows = [{"date": "2019-01-01", "concentration": complete},
                {"date": "2026-01-01", "concentration": partial},
                {"date": "2026-01-02", "concentration": deepcopy(complete)}]
        add_percentiles(rows)
        self.assertEqual([r["concentration"]["tiers"]["1"]["sample"] for r in rows], [1, 0, 1])

    def test_size_and_premium_use_same_day_inputs_and_independent_coverage(self):
        first, second, third = days = ["2026-11-10", "2026-11-11", "2026-11-12"]
        universe = read(ROOT / "config/universe.json")
        universe["funds"] = [f for f in universe["funds"] if f["symbol"] in BASKET]
        target, other, *rest = universe["funds"]
        target["listed_date"] = second
        records = {
            f["symbol"]: {"shares": [(d, 1e8) for d in days],
                          "navs": [(d, 2, 1) for d in days], "splits": []}
            for f in universe["funds"]
        }
        records[other["symbol"]]["navs"].pop()
        closes = {second: {other["symbol"]: 2.2}, third: {target["symbol"]: 2.4,
                  other["symbol"]: 4, rest[0]["symbol"]: 1.8}}
        ms = 1794441600000
        market = {"calendar": days, "daily": [[INDEX, ms, 4000, None]] +
                  [[symbol, ms, 2, 1e8] for symbol in BASKET], "minutes": []}
        snapshot = history_snapshot(market, records, universe, {
            "flow_start": first, "market_start": first, "minute_days": 0,
        }, third, closes)
        size, premium = (snapshot["broad_metrics"][name] for name in ("size", "premium"))
        self.assertEqual(size["total"], [40, None])
        self.assertEqual(size["latest"], third)
        self.assertAlmostEqual(premium["total"][0], 10)
        self.assertAlmostEqual(premium["total"][1], 5)
        self.assertEqual({row["symbol"] for row in premium["members"]}, {target["symbol"], rest[0]["symbol"]})
        self.assertEqual(sum(row[-1] for row in premium["valid"].values()), 2)
        validate_seed(snapshot, "live")
        malformed = deepcopy(snapshot)
        malformed["broad_metrics"]["size"]["total"].pop()
        with self.assertRaisesRegex(ValueError, "Invalid broad metrics"):
            validate_seed(malformed, "live")

    def test_same_day_sz_shares_and_confirmed_split(self):
        days = ["2026-11-11", "2026-11-12"]
        fund = {"symbol": "159915.SZ", "inception_date": "2011-09-20", "listed_date": "2011-12-09"}
        records = {"shares": list(zip(days, [100, 206])), "navs": [(days[0], 2, 1), (days[1], 1, 2)],
                   "splits": [(days[1], 2)]}
        result = fund_flows(fund, records, days)[days[1]]
        self.assertEqual(result["shares_record_date"], days[1])
        self.assertEqual(result["flow"], 6 / 1e8)
        records["splits"] = [(days[1], 3)]
        self.assertIsNone(fund_flows(fund, records, days)[days[1]]["flow"])

    def test_board_provider_header_is_request_scoped(self):
        from starlette.requests import Request
        from etf_backend.web import create_app

        app = create_app("demo")
        endpoint = next(r.endpoint for r in app.routes if r.path == "/{path:path}")
        response = endpoint("api/board", Request({"type": "http", "path": "/api/board",
                                                  "query_string": b"", "headers": []}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Data-Provider"], "Demo")

    def test_reload_keeps_last_valid_snapshot_and_rejects_losing_history(self):
        original = read(ROOT / "data/demo-seed.json")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "seed.json"
            publish(path, original)
            archive = Archive(path, lambda value: validate_seed(value, "demo"))
            self.assertEqual(archive.load()["built_at"], original["built_at"])
            path.write_text("broken", encoding="utf-8")
            self.assertEqual(archive.load()["built_at"], original["built_at"])
            updated = deepcopy(original)
            updated["built_at"] = "2026-11-14T00:00:00.000Z"
            publish(path, updated)
            self.assertEqual(archive.load()["built_at"], updated["built_at"])
            index = next(i for i, value in enumerate(updated["history"]["netflow"]) if value is not None)
            updated["history"]["netflow"][index] = None
            with self.assertRaisesRegex(ValueError, "lose previously valid"):
                validate_replacement(original, updated)


if __name__ == "__main__":
    unittest.main()
