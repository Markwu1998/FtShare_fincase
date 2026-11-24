from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from etf_backend.burst_ledger import read_journal
from etf_backend.futures_evidence import Snapshot, contract_window
from etf_backend.provider import CHINA
from etf_backend.realtime import PushSession

DAY = "2026-11-20"
SYMBOL = "510300.SH"
CONTRACTS = ["IF2611", "IF2612", "IF2703", "IF2706"]


def stamp(time):
    return datetime.fromisoformat(DAY + "T" + time).replace(tzinfo=CHINA)


class FuturesEvidenceTests(unittest.TestCase):
    def test_component_amounts_differ_and_ambiguous_turnover_keeps_a_gap(self):
        start = stamp("09:45:00")
        rows = [
            Snapshot(start, 4010, 100, 1000, 0, 4000, 4020),
            Snapshot(start + timedelta(seconds=1), 4000, 102, 1005, 6000000, 3999, 4001),
            Snapshot(start + timedelta(seconds=2), 4000, 101, 1007, 8400000, 3999, 4001),
            Snapshot(start + timedelta(seconds=3), 4001, 103, 1010, 12000900, 4000, 4002),
            Snapshot(start + timedelta(seconds=4), 4000, 102, 1011, 13200900, 3999, 4001),
        ]
        result = contract_window(rows, start, rows[-1].timestamp, 300)
        self.assertEqual(result["net_oi_cny"], 2406000)
        self.assertEqual(result["estimated_sell_cny"], 7200000)
        self.assertEqual(result["estimated_sell_oi_cny"], 2400000)
        self.assertEqual(result["classified_turnover_cny"], 10800900)
        self.assertEqual(result["turnover_cny"], 13200900)
        sides = result["directions"]
        self.assertEqual(sides["sell"]["oi_increase_cny"], 2406000)
        self.assertEqual(sides["sell"]["oi_decrease_cny"], -1203000)
        self.assertEqual(sides["buy"]["oi_net_cny"], 2406000)
        self.assertEqual(sides["unclassified"]["oi_net_cny"], -1203000)
        self.assertEqual(sum(side["oi_net_cny"] for side in sides.values()), result["net_oi_cny"])
        self.assertIsNone(contract_window(rows, start - timedelta(seconds=1), rows[-1].timestamp, 300))
        self.assertIsNone(contract_window(rows, start, start + timedelta(seconds=30), 300))

    def test_snapshot_gap_excludes_sell_proxy_and_preserves_endpoint_oi(self):
        start = stamp("09:45:00")
        end = start + timedelta(seconds=15)
        values = contract_window([Snapshot(start, 4001, 100, 100, 0, 4000, 4002),
                                  Snapshot(end, 4000, 102, 105, 6000000, 3999, 4001)], start, end, 300)
        self.assertEqual(values["delta_oi"], 2)
        self.assertIsNone(values["estimated_sell_cny"])
        self.assertEqual(values["invalid_segments"], 1)

    def test_live_confirmation_archives_all_contracts_and_raw_ticks_once(self):
        class Feed:
            class futures:
                @staticmethod
                def get_contracts(product, day):
                    return CONTRACTS

            def get_trading_dates(self, *_):
                return ["2026-11-19", DAY]

            def get_price(self, *_args, **_kwargs):
                return None

        funds = [{"symbol": SYMBOL, "bucket": "沪深300"}]
        now = stamp("09:36:08")
        with tempfile.TemporaryDirectory() as tmp, patch("etf_backend.realtime.provider.bars", return_value=[]):
            directory = Path(tmp)
            session = PushSession(Feed(), DAY, funds, directory / "board.json", directory, .75,
                                  symbols=[SYMBOL], futures_enabled=True)
            refs = {(k, i): ([1, 1], [1, 1]) for k in (1, 2, 5, 10) for i in range(240)}
            session.detector.baseline = {SYMBOL: ([1000, 1000], refs)}
            for code in CONTRACTS:
                for step in range(37):
                    t = stamp("09:30:00") + timedelta(seconds=10 * step)
                    session.handle_futures({"channel": "tick_" + code, "order_book_id": code,
                                            "datetime": t.isoformat(), "last": 4000, "open_interest": 100 + step,
                                            "volume": 1000 + step, "total_turnover": step * 1200000,
                                            "bids": [4000], "asks": [4001]}, t.isoformat(), now)
            for i, amount in enumerate([1, 1, 1, 1, 120, 120]):
                session.bar(SYMBOL, stamp(f"09:{31+i:02d}:00"), 1., amount, 10, now.isoformat())
            session.update_futures_evidence(now)
            event = session.detector.events[0]
            self.assertEqual(event["evidence"]["futures"]["etf_volume_shares"], 50)
            self.assertEqual(event["evidence"]["futures"]["covered"], 4)
            self.assertEqual(event["evidence"]["futures"]["status"], "sell_side_evidence")
            self.assertEqual(len(event["updates"]), 1)
            session.journal.commit()
            path = directory / "journals" / f"{DAY}.sqlite3"
            logs = read_journal(path, kind="futures_evidence")
            self.assertEqual(len(logs), 2)
            self.assertEqual([r["value"]["window_end"] for r in logs], ["09:35", "09:36"])
            self.assertEqual(len(logs[-1]["value"]["contracts"]), 4)
            self.assertEqual(logs[-1]["value"]["directions"]["sell"]["turnover_cny"], 144000000)
            self.assertEqual(logs[-1]["value"]["contracts"][0]["values"]["directions"]["sell"]["oi_net_cny"], 36000000)
            self.assertEqual(len(read_journal(path, limit=1000, kind="futures_tick")), 4 * 37)
            session.futures.rows[CONTRACTS[-1]].clear()
            partial = session.futures.observe("IF", stamp("09:31:00"), stamp("09:36:00"))
            self.assertEqual(partial["covered"], 3)
            self.assertIsNone(partial["directions"])
            self.assertIsNone(partial["estimated_sell_cny"])
            self.assertEqual(partial["contracts"][0]["values"]["estimated_sell_cny"], 36000000)
