from copy import deepcopy
from datetime import datetime, timedelta
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from etf_backend.burst import BurstDay
from etf_backend.collect import main as collect_main, read, publish
from etf_backend.model import BASKET, SLOTS
from etf_backend.provider import CHINA
from etf_backend.realtime import PushSession
from etf_backend.tick_minutes import TickMinutes, partial_signals, replay_tick_day


DAY, SYMBOL = "2026-11-13", BASKET[0]


def clock(hm):
    return datetime.fromisoformat(DAY + "T" + hm).replace(tzinfo=CHINA)


def reference():
    return {SYMBOL: ([1000, 1000], {(k, i): ([1, 2], [1, 1])
                                   for k in (1, 2, 5, 10) for i in range(240)})}


class TickAggregationTest(unittest.TestCase):
    def test_cumulative_amount_not_tick_sum_and_exact_boundary(self):
        agg = TickMinutes(DAY, [SYMBOL], [SYMBOL], "legacy-right")
        agg.add(SYMBOL, clock("09:30:00"), 1, 50)
        agg.add(SYMBOL, clock("09:30:58"), 1.1, 120)
        agg.add(SYMBOL, clock("09:31:00"), 1.2, 150)
        self.assertEqual(agg.advance(clock("09:31:01")), [])
        first = agg.advance(clock("09:31:02"))[0]
        self.assertTrue(first["valid"])
        self.assertEqual((first["amount"], first["close"]), (150, 1.2))
        agg.add(SYMBOL, clock("09:31:58"), 1.3, 200)
        self.assertEqual(agg.advance(clock("09:32:02"))[0]["amount"], 50)

    def test_unsealed_reordering_and_duplicates(self):
        agg = TickMinutes(DAY, [SYMBOL], [SYMBOL], "legacy-right")
        agg.add(SYMBOL, clock("09:30:58"), 1, 120)
        self.assertTrue(agg.add(SYMBOL, clock("09:30:55"), 1, 110))
        self.assertFalse(agg.add(SYMBOL, clock("09:30:58"), 1, 120))
        self.assertEqual(agg.advance(clock("09:31:02"))[0]["amount"], 120)
        self.assertFalse(agg.add(SYMBOL, clock("09:30:59"), 1, 130))
        self.assertEqual(agg.rejected, {"late": 1, "duplicate": 1, "regression": 0})

    def test_counter_regression_and_missing_boundaries_do_not_make_zero_bars(self):
        agg = TickMinutes(DAY, [SYMBOL], [SYMBOL], "legacy-right")
        agg.add(SYMBOL, clock("09:30:55"), 1, 120)
        self.assertFalse(agg.add(SYMBOL, clock("09:30:58"), 1, 100))
        self.assertFalse(agg.advance(clock("09:31:02"))[0]["valid"])
        self.assertFalse(agg.advance(clock("09:32:02"))[0]["valid"])
        agg.add(SYMBOL, clock("09:32:59"), 1, 200)
        self.assertFalse(agg.advance(clock("09:33:02"))[0]["valid"])

    def test_lunch_anchors_cumulative_but_does_not_create_lunch_minutes(self):
        agg = TickMinutes(DAY, [SYMBOL], [SYMBOL], "legacy-right")
        agg.add(SYMBOL, clock("11:29:59"), 1, 1000)
        agg.advance(clock("11:30:02"))
        self.assertEqual(agg.advance(clock("12:30:00")), [])
        agg.add(SYMBOL, clock("13:00:58"), 1, 1100)
        rows = agg.advance(clock("13:01:02"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["amount"], 100)
        self.assertEqual(rows[0]["end"].strftime("%H:%M"), "13:01")

    def test_candidate_uses_original_formula_without_changing_formal_state(self):
        agg = TickMinutes(DAY, [SYMBOL], [SYMBOL], "legacy-right")
        detector = BurstDay(DAY, reference(), .75, symbols=[SYMBOL])
        for i in range(4):
            detector.add(SYMBOL, SLOTS[i], 1)
        agg.add(SYMBOL, clock("09:34:00"), 1, 4)
        agg.add(SYMBOL, clock("09:34:20"), 1.1, 124)
        before = deepcopy((detector.values, detector.events, detector.active, detector.shadow))
        candidate = partial_signals(agg, detector, clock("09:34:20"))[0]
        self.assertEqual((candidate["signal"]["level"], candidate["signal"]["window"]), (2, 5))
        self.assertEqual(candidate["minute_end"], "09:35")
        self.assertTrue(candidate["provisional"])
        self.assertEqual(before, (detector.values, detector.events, detector.active, detector.shadow))
        self.assertEqual(partial_signals(agg, detector, clock("09:34:40")), [])  # stale tick
        self.assertEqual(partial_signals(agg, detector, clock("12:00:00")), [])

    def test_index_can_aggregate_close_without_turnover(self):
        agg = TickMinutes(DAY, ["index"], [])
        agg.add("index", clock("09:30:59"), 3000, None)
        row = agg.advance(clock("09:31:02"))[0]
        self.assertTrue(row["valid"])
        self.assertEqual(row["close"], 3000)
        self.assertIsNone(row["amount"])

    def test_tick_replay_matches_original_bar_detector_on_identical_minutes(self):
        amounts = [1, 1, 1, 1, 120, 1, 1100] + [0] * 233
        ticks, total = [], 0
        original = BurstDay(DAY, reference(), .75, replay=True, symbols=[SYMBOL])
        for slot, amount in zip(SLOTS, amounts):
            total += amount
            market = clock(slot + ":00")
            ticks.append([SYMBOL, market.isoformat(), (market + timedelta(milliseconds=100)).isoformat(), 1, total])
            original.add(SYMBOL, slot, amount)
        replay = replay_tick_day(DAY, reference(), .75, ticks, [SYMBOL])
        self.assertEqual(replay["events"], original.payload()["events"])
        self.assertEqual(replay["median_hits"], original.payload()["median_hits"])
        self.assertEqual(replay["through"], "14:57")
        self.assertEqual(replay["minute_coverage"]["received"][SYMBOL], 237)
        self.assertEqual(replay["provider"], "RQData")
        self.assertEqual(replay_tick_day(DAY, reference(), .75, ticks, [SYMBOL], provider="DDB")["provider"], "DDB")

    def test_tick_replay_validates_reception_order_and_timezone(self):
        records = [[SYMBOL, clock("09:30:59").isoformat(), clock("09:31:00").isoformat(), 1, 100],
                   [SYMBOL, clock("09:30:58").isoformat(), clock("09:30:59").isoformat(), 1, 90]]
        with self.assertRaisesRegex(ValueError, "reception order"):
            replay_tick_day(DAY, reference(), .75, records, [SYMBOL])
        records[0][1] = "2026-11-13T09:30:59"
        with self.assertRaisesRegex(ValueError, "timezone"):
            replay_tick_day(DAY, reference(), .75, records[:1], [SYMBOL])

    def test_tick_replay_retains_early_candidate_separately_from_confirmation(self):
        ticks = [[SYMBOL, clock(slot + ":00").isoformat(), clock(slot + ":01").isoformat(), 1, i + 1]
                 for i, slot in enumerate(SLOTS[:4])]
        for hm, cash in [("09:34:20", 124), ("09:34:58", 124), ("09:35:03", 124)]:
            ticks.append([SYMBOL, clock(hm).isoformat(), clock(hm).isoformat(), 1, cash])
        result = replay_tick_day(DAY, reference(), .75, ticks, [SYMBOL])
        candidate = result["candidate_history"][0]
        self.assertEqual(candidate["as_of"], clock("09:34:20").isoformat())
        self.assertEqual(candidate["minute_end"], "09:35")
        self.assertTrue(candidate["provisional"])
        self.assertEqual(result["events"][0]["first"], "09:35")
        self.assertEqual(result["events"][0]["level"], 2)

    def test_tick_replay_cli_runs_offline_and_preserves_the_replay_api_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = {"schema_version": 2, "metric": "turnover", "unit": "CNY", "1m": [],
                      "1d": [[SYMBOL, "2026-11-12", "15:00", 1000]],
                      "ticks": [[SYMBOL, clock("09:31:00").isoformat(), clock("09:31:01").isoformat(), 1, 10]]}
            (root / "input.json").write_text(json.dumps(source), encoding="utf-8")
            (root / "config.json").write_text(json.dumps({"symbols": [SYMBOL]}), encoding="utf-8")
            argv = ["collect", "burst-replay", "--end", DAY, "--burst-input", str(root / "input.json"),
                    "--burst-config", str(root / "config.json"), "--burst-dir", str(root / "output")]
            with patch("sys.argv", argv), patch.dict("sys.modules", {"rqdatac": None}), contextlib.redirect_stdout(io.StringIO()):
                collect_main()
            result = read(root / "output/replay" / (DAY + ".json"))
            self.assertTrue(result["replay"])
            self.assertEqual(result["feed"], "tick-replay")
            self.assertEqual(result["minute_coverage"]["received"][SYMBOL], 1)
            self.assertEqual(result["events"], [])  # no fabricated baseline or missing-minute zeros


class TickDriverTest(unittest.TestCase):
    def session(self, directory, driver):
        class Feed:
            def get_trading_dates(self, *_): return ["2026-11-12", DAY]
            def get_price(self, *_args, **_kwargs): return None
        funds = [{"symbol": SYMBOL, "bucket": "沪深300", "name": "test"}]
        with patch("etf_backend.realtime.provider.bars", return_value=[]):
            session = PushSession(Feed(), DAY, funds, Path(directory) / "board", Path(directory), .75,
                                  symbols=[SYMBOL], minute_driver=driver)
        session.provider_detector.baseline = reference()
        session.tick_detector.baseline = reference()
        return session

    def test_tick_driver_confirms_without_provider_bars_and_compares_later(self):
        with tempfile.TemporaryDirectory() as directory:
            session = self.session(directory, "tick")
            total = 0
            for i, cash in enumerate([1, 1, 1, 1, 120]):
                end = clock(SLOTS[i] + ":00")
                total += cash
                session.tick_minutes.add(SYMBOL, end - timedelta(milliseconds=1), 1, total)
                session.advance_ticks(end + timedelta(seconds=2))
            self.assertEqual(session.detector.events[0]["level"], 2)
            self.assertEqual(session.detector.events[0]["first"], "09:35")
            key = SYMBOL, "09:35"
            self.assertEqual(session.minutes[key][3], 120)
            session.bar(SYMBOL, clock("09:35:00"), 1, 121, 100)
            audit = session.tick_comparison()
            self.assertEqual((audit["compared"], audit["mismatched"]), (1, 1))
            self.assertEqual(session.minutes[key][3], 120)  # no retroactive rewrite

    def test_switching_driver_cannot_overwrite_same_day_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            publish(Path(directory) / "live.json", {"date": DAY, "tick_monitor": {"minute_driver": "provider"}})
            with self.assertRaisesRegex(ValueError, "separate ETF_BURST_DIR"):
                self.session(directory, "tick")

    def test_provider_default_is_unchanged_while_tick_shadow_differs(self):
        with tempfile.TemporaryDirectory() as directory:
            session = self.session(directory, "provider")
            for i, cash in enumerate([1, 1, 1, 1, 120]):
                end = clock(SLOTS[i] + ":00")
                session.tick_minutes.add(SYMBOL, end - timedelta(milliseconds=1), 1, i + 1)
                session.advance_ticks(end + timedelta(seconds=2))
                session.bar(SYMBOL, end, 1, cash, cash)
            self.assertEqual(session.detector.events[0]["level"], 2)
            self.assertIs(session.detector, session.provider_detector)
            self.assertEqual(session.tick_detector.events, [])
            self.assertEqual(session.minutes[SYMBOL, "09:35"][3], 120)
            self.assertEqual(session.tick_comparison()["mismatched"], 1)
            self.assertEqual(session.tick_comparison()["event_mismatches"], 1)
            self.assertEqual(session.tick_comparison()["event_differences"][0]["provider_level"], 2)

    def test_provider_recovery_marks_events_and_fills_gaps_once(self):
        with tempfile.TemporaryDirectory() as directory:
            session = self.session(directory, "tick")
            session.advance_ticks(clock("09:35:02"))  # disconnected
            for i, cash in enumerate([1, 1, 1, 1, 120]):
                session.bar(SYMBOL, clock(SLOTS[i] + ":00"), 1, cash, cash)
            self.assertEqual(session.detector.events[0]["level"], 2)
            self.assertTrue(session.detector.events[0]["recovered"])
            self.assertEqual(session.tick_comparison()["recovered"], 5)
            session.bar(SYMBOL, clock("09:35:00"), 1, 999999, 1)
            self.assertEqual(len(session.detector.events), 1)
            self.assertEqual(session.minutes[SYMBOL, "09:35"][3], 120)
