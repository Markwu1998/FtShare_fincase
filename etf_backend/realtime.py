"""RQData push collector. Existing board and per-fund radar share one feed."""

from collections import defaultdict, deque
from contextlib import suppress
from datetime import datetime, timedelta
import json
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Thread
from time import monotonic, sleep

from . import provider
from .burst import BurstDay, query_history, make_baseline, SLOT_INDEX, RULE_VERSION
from .futures_evidence import FuturesEvidence, PRODUCTS, END_GRACE, window_ticks
from .tick_minutes import TickMinutes, partial_signals
from .burst_ledger import BurstLedger
from .tick_factors import load_references, candidates as tick_candidates, FACTOR_VERSION
from .collect import live_snapshot, publish, read
from .model import BASKET, INDEX, SLOTS, iso_utc, trading_minute

INDEX_GROUPS = {"沪深300": "000300.XSHG", "中证500": "000905.XSHG", "中证A500": "000510.XSHG",
                "科创50": "000688.XSHG", "创业板指": "399006.XSHE", "中证1000": "000852.XSHG",
                "上证50": "000016.XSHG", "创业板50": "399673.XSHE"}


class MinuteDriverConflict(ValueError):
    """A source switch must not overwrite today's other-driver archive."""


def feed_time(value):
    raw = str(value)
    if raw.isdigit() and len(raw) in (14, 17):
        return datetime.strptime(raw[:14], "%Y%m%d%H%M%S").replace(
            microsecond=int(raw[14:] or 0) * 1000, tzinfo=provider.CHINA)
    return provider.local_time(value)


def etf_quote_direction(tape):
    """Estimate ETF active buy/sell turnover from L1 trades and the quote rule.

    ``tape`` rows are ``(time, ask, bid, iopv, cumulative_volume, last)``.
    A trade at/above the ask is buy-side, at/below the bid is sell-side; inside
    the spread we use the midpoint. Volume deltas times trade price give a CNY
    estimate. This is intentionally the same, simple economic idea as the
    daily midpoint-band direction archive.
    """
    buy = sell = neutral = 0.0
    prior = None
    covered = 0
    for row in tape:
        t, ask, bid, _, volume, last = row
        if prior is not None:
            _, _, _, _, previous_volume, _ = prior
            delta = volume - previous_volume if volume is not None and previous_volume is not None else 0
            if delta > 0 and last is not None and last > 0:
                cash = delta * last
                covered += 1
                if ask is not None and bid is not None and 0 < bid <= ask:
                    mid = (ask + bid) / 2
                    side = "buy" if last >= ask - 1e-9 else "sell" if last <= bid + 1e-9 else (
                        "buy" if last > mid else "sell" if last < mid else "neutral")
                else:
                    side = "neutral"
                if side == "buy":
                    buy += cash
                elif side == "sell":
                    sell += cash
                else:
                    neutral += cash
        prior = row
    total = buy + sell + neutral
    if covered < 2 or total <= 0:
        return None
    side = "buy" if buy > sell else "sell" if sell > buy else "neutral"
    return {"side": side, "buy_cny": round(buy, 2), "sell_cny": round(sell, 2),
            "neutral_cny": round(neutral, 2), "method": "l1-quote-midpoint-v1",
            "coverage": covered}


class PushSession:
    def __init__(self, api, day, funds, path, burst_dir, q, symbols=BASKET, minute_driver="provider", futures_enabled=False):
        if minute_driver not in {"provider", "tick"}:
            raise ValueError("minute_driver must be provider or tick")
        self.minute_driver = minute_driver
        self.day, self.path, self.funds = day, path, funds
        self.symbols = list(symbols)
        self.burst_path = burst_dir / "live.json"
        previous_futures = {}
        if self.burst_path.is_file():
            previous = read(self.burst_path)
            previous_driver = previous.get("tick_monitor", {}).get("minute_driver", "provider")
            if previous.get("date") == day and previous_driver != minute_driver:
                raise MinuteDriverConflict("Changing minute_driver during a session requires a separate ETF_BURST_DIR")
            if previous.get("date") == day:
                for event in previous.get("events", []):
                    item = event.get("evidence", {}).get("futures")
                    if item and item.get("covered") == item.get("expected"):
                        previous_futures[event["id"], item["window_start"], item["window_end"]] = item
        self.minutes, self.quotes = {}, {}
        self.quote_details = {}
        self.board = None
        self.minute_version = -1
        self.board_signature = None
        self.reference_path = burst_dir / "baselines" / f"{day}.json"
        self.ticks = defaultdict(lambda: deque(maxlen=1800))
        self.mapping = {s.replace(".SH", ".XSHG").replace(".SZ", ".XSHE"): s for s in self.symbols + [INDEX]}
        self.groups = {f["symbol"]: f["bucket"] for f in funds if f["symbol"] in self.symbols}
        self.index_codes = {s: INDEX_GROUPS.get(group) for s, group in self.groups.items()}
        self.futures = FuturesEvidence() if futures_enabled else None
        self.futures_pending = {}
        self.futures_previous = previous_futures
        self.futures_written = set()
        self.etf_bar_volume = {}
        stream_symbols = list(dict.fromkeys(self.symbols + [INDEX] + [c for c in self.index_codes.values() if c]))
        self.tick_minutes = TickMinutes(day, stream_symbols, self.symbols)
        self.provider_minutes, self.local_minutes = {}, {}
        self.tick_gaps = set()
        self.recoveries = set()
        self.comparison_version, self.comparison_cache = None, None
        self.calendar = [str(d) for d in api.get_trading_dates(
            (datetime.fromisoformat(day) - timedelta(days=130)).date().isoformat(), day)]
        self.detector = None
        self.index_baseline = {}
        self.baseline_ready = False
        self.previous = []
        self.last_publish = monotonic()
        self.last_report = None
        if day in self.calendar and len(self.calendar) > 1:
            previous = self.calendar[-2]
            self.previous = provider.bars(api, self.mapping, previous, previous, "1d")
            self.detector = BurstDay(day, make_baseline([], {}, {}, day, q, self.symbols), q, symbols=self.symbols,
                                     baseline_as_of=previous)
            self.load_baseline(api)
            if self.futures is not None:
                self.futures.prepare(api, day, {PRODUCTS[g] for g in self.groups.values() if g in PRODUCTS})
        self.provider_detector = self.detector
        self.tick_detector = None
        if self.detector:
            self.tick_detector = BurstDay(day, self.detector.baseline, q, symbols=self.symbols,
                                          baseline_as_of=self.calendar[-2])
            if minute_driver == "tick":
                self.detector = self.tick_detector
        from hashlib import sha256
        self.tick_references, self.tick_reference_status = load_references(
            burst_dir / 'tick-baselines' / f'{day}.json', day,
            self.calendar[-2] if len(self.calendar) > 1 else None, q)
        self.tick_references = {s: r for s, r in self.tick_references.items() if s in self.symbols}
        self.journal = BurstLedger(burst_dir / 'journals' / f'{day}.sqlite3', {
            'day': day, 'driver': minute_driver, 'rule_version': RULE_VERSION, 'quantile': q,
            'boundary_mode': self.tick_minutes.boundary_mode, 'symbols': self.symbols,
            'baseline_as_of': self.calendar[-2] if len(self.calendar) > 1 else None,
            'baseline_hash': sha256(repr(self.detector.baseline if self.detector else {}).encode()).hexdigest(),
            'tick_factor_version': FACTOR_VERSION,
            'tick_reference_hash': sha256(json.dumps(self.tick_references, sort_keys=True).encode()).hexdigest()})
        self.candidate_states, self.ended_events = {}, set()
        self.second_candidates = {}
        if self.detector:
            self.detector.on_signal = self.record_signal

    def record_signal(self, event, signal, received, detected, recovered):
        values = self.detector.values[event['symbol']]
        end = SLOT_INDEX[signal['end']]
        start = 120 if end >= 120 else 0
        ring = {'raw': None, 'adjusted': None}
        if end - start >= 20:
            history = values[end - 20:end]
            refs = [self.detector.baseline[event['symbol']][1].get((1, i), [[None, None]])[0]
                    for i in range(end - 20, end + 1)]
            if all(v is not None for v in history) and sum(history) > 0:
                ring['raw'] = values[end] * 20 / sum(history)
                if all(all(v is not None and v > 0 for v in r) for r in refs):
                    ring['adjusted'] = (values[end] / max(refs[-1])) / (sum(history) / sum(max(r) for r in refs[:-1]))
        self.journal.append('formal', event['symbol'], f"{self.day}T{signal['end']}:00+08:00", received,
                            {'event_id': event['id'], 'signal': signal, 'detected_at': detected,
                             'recovered': recovered, 'ring': ring})
        event['evidence']['ring'] = ring
        if self.futures is not None and signal["level"] >= 2 and self.groups.get(event["symbol"]) in PRODUCTS:
            self.futures_pending[event["id"], signal["start"], signal["end"]] = (event, signal, recovered)

    def handle_futures(self, msg, received, now):
        code = msg.get("order_book_id")
        if code not in self.futures.rows or "datetime" not in msg or msg.get("channel") != "tick_" + code:
            return
        try:
            timestamp = feed_time(msg["datetime"])
        except (TypeError, ValueError):
            return
        if timestamp.date().isoformat() == self.day and timestamp <= now + timedelta(seconds=2):
            return self.futures.add(code, timestamp, msg, received)

    def update_futures_evidence(self, now):
        if self.futures is None:
            return
        for key, (event, signal, recovered) in list(self.futures_pending.items()):
            start_label, end_label = signal["start"], signal["end"]
            end = feed_time(f"{self.day}T{end_label}:00+08:00")
            if now < end + END_GRACE:
                continue
            del self.futures_pending[key]
            if key in self.futures_previous:
                event["evidence"]["futures"] = self.futures_previous[key]
                continue
            start = feed_time(f"{self.day}T{start_label}:00+08:00") - timedelta(minutes=1)
            symbol = event["symbol"]
            index = self.index_codes[symbol]
            product = PRODUCTS[self.groups[symbol]]
            a = self.minutes.get((index, start.strftime("%H:%M")))
            b = self.minutes.get((index, end_label))
            spot_return = b[2] / a[2] - 1 if a and a[2] and b and b[2] else None
            evidence = self.futures.observe(product, start, end, spot_return)
            slots = SLOTS[SLOT_INDEX[start_label]:SLOT_INDEX[end_label]+1]
            quantities = [self.etf_bar_volume.get((symbol, slot)) for slot in slots]
            evidence.update(window_start=start_label, window_end=end_label, etf_turnover_cny=signal["amount"],
                            etf_volume_shares=sum(quantities) if all(v is not None for v in quantities) else None,
                            recovered=recovered, observed_at=iso_utc(now))
            event["evidence"]["futures"] = evidence
            for contract in evidence["contracts"]:
                code = contract["code"]
                for tick in window_ticks(self.futures.rows[code], start, end):
                    tick_key = (code, tick.timestamp)
                    if tick_key in self.futures_written:
                        continue
                    self.futures_written.add(tick_key)
                    self.journal.append("futures_tick", code, iso_utc(tick.timestamp), tick.received_at,
                                        {"last": tick.last, "open_interest": tick.oi, "volume": tick.volume,
                                         "total_turnover": tick.turnover, "bid": tick.bid, "ask": tick.ask})
            self.journal.append("futures_evidence", symbol, iso_utc(end), iso_utc(now),
                                {"event_id": event["id"], **evidence})
        if self.futures_written:
            limit = now - timedelta(minutes=22)
            self.futures_written = {key for key in self.futures_written if key[1] >= limit}

    def load_baseline(self, api):
        previous = self.calendar[-2]
        first = self.calendar[max(0, len(self.calendar) - 61)]
        identity = {"day": self.day, "as_of": previous, "symbols": self.symbols,
                    "quantile": self.detector.quantile, "rule_version": RULE_VERSION}
        try:
            saved = read(self.reference_path)
            if saved["identity"] == identity:
                baseline = {s: (value["daily"], {(k, i): refs for k, i, refs in value["windows"]})
                            for s, value in saved["baseline"].items()}
                if set(baseline) != set(self.symbols) or any(len(v[1]) != 4 * (SLOT_INDEX["14:57"] + 1)
                                                           for v in baseline.values()):
                    raise ValueError("Incomplete cached baseline")
                self.detector.baseline = baseline
                self.index_baseline = {(code, slot): value for code, slot, value in saved["index"]}
                self.baseline_ready = saved["ready"]
                return
        except (OSError, KeyError, TypeError, ValueError):
            pass
        try:
            minutes, daily = query_history(api, first, previous, self.symbols)
            q = self.detector.quantile
            self.detector.baseline = make_baseline(self.calendar, minutes, daily, self.day, q, self.symbols)
            self.baseline_ready = any(all(v is not None and v > 0 for v in totals)
                                      for totals, _ in self.detector.baseline.values())
        except Exception as error:
            print(f"ETF turnover baseline unavailable ({type(error).__name__})", flush=True)
        try:
            refs = api.get_price(sorted({code for code in self.index_codes.values() if code}),
                                 start_date=first, end_date=previous,
                                 frequency="1m", fields=["close"], adjust_type="none")
            self.index_baseline = self.index_thresholds(refs)
        except Exception as error:
            print(f"Index baseline unavailable ({type(error).__name__})", flush=True)
        if self.baseline_ready and self.index_baseline:
            publish(self.reference_path, {"identity": identity, "ready": self.baseline_ready,
                    "baseline": {s: {"daily": totals, "windows": [[k, i, refs] for (k, i), refs in windows.items()]}
                                 for s, (totals, windows) in self.detector.baseline.items()},
                    "index": [[code, slot, value] for (code, slot), value in self.index_baseline.items()]})

    def index_thresholds(self, frame):
        from .burst import quantile
        samples = defaultdict(dict)
        previous = {}
        if frame is not None:
            for (code, stamp), raw in frame["close"].sort_index().items():
                t = provider.local_time(stamp)
                value = provider.number(raw)
                if value is None or value <= 0:
                    continue
                prior = previous.get((code, t - timedelta(minutes=2)))
                slot = t.strftime("%H:%M")
                if prior and (slot >= "13:03" or "09:33" <= slot <= "11:30"):
                    samples[code, slot][t.date()] = value / prior - 1
                previous[code, t] = value
        return {key: quantile(list(values.values()), .95) for key, values in samples.items() if len(values) >= 55}

    def bar(self, symbol, timestamp, close, cash, volume, received=None, recovered=False):
        slot = timestamp.strftime("%H:%M")
        key = symbol, slot
        if slot not in SLOTS or key in self.provider_minutes:
            return
        if not (close is not None and close > 0):
            return
        if symbol in self.symbols and (cash is None or cash < 0):
            return
        row = (symbol, int(timestamp.timestamp() * 1000), close, cash)
        if symbol in self.symbols and volume is not None and volume >= 0:
            self.etf_bar_volume[key] = volume
        received = received or iso_utc(datetime.now(provider.CHINA))
        self.journal.append('bar', symbol, iso_utc(timestamp), received,
                            {'close': close, 'total_turnover': cash, 'volume': volume, 'recovered': recovered})
        self.provider_minutes[key] = row
        self.feed_detector(self.provider_detector, symbol, slot, cash, received, recovered=recovered)
        if self.minute_driver == "provider":
            self.minutes[key] = row
        if key in self.tick_gaps:
            self.recover_tick_gap(key, row, received)

    def feed_detector(self, detector, symbol, slot, cash, received=None, *, recovered=False):
        if symbol in self.symbols and detector is not None:
            end = SLOT_INDEX[slot]
            if end < detector.latest.get(symbol, -1):
                if recovered and detector.values[symbol][end] is None:
                    detector.values[symbol][end] = cash
            else:
                detector.add(symbol, slot, cash, received, recovered=recovered)

    def recover_tick_gap(self, key, row, received=None):
        if key in self.recoveries:
            return
        symbol, slot = key
        received = received or iso_utc(datetime.now(provider.CHINA))
        self.journal.append('recovery', symbol, f'{self.day}T{slot}:00+08:00', received,
                            {'source': 'provider', 'amount': row[3]})
        self.recoveries.add(key)
        self.feed_detector(self.tick_detector, symbol, slot, row[3], received, recovered=True)
        if self.minute_driver == "tick":
            self.minutes[key] = row

    def advance_ticks(self, now):
        for item in self.tick_minutes.advance(now):
            symbol, timestamp = item["symbol"], item["end"]
            slot = timestamp.strftime("%H:%M")
            key = symbol, slot
            if item["valid"]:
                row = (symbol, int(timestamp.timestamp() * 1000), item["close"], item["amount"])
                self.local_minutes[key] = row
                self.feed_detector(self.tick_detector, symbol, slot, item["amount"], iso_utc(now))
                if self.minute_driver == "tick":
                    self.minutes[key] = row
            else:
                if key not in self.tick_gaps:
                    self.journal.append('data_gap', symbol, iso_utc(timestamp), iso_utc(now),
                                        {'boundary_mode': self.tick_minutes.boundary_mode})
                self.tick_gaps.add(key)
                if key in self.provider_minutes:
                    self.recover_tick_gap(key, self.provider_minutes[key])

    def candidates(self, now):
        if not self.detector:
            return []
        return partial_signals(self.tick_minutes, self.detector, now)

    def tick_comparison(self):
        version = (len(self.local_minutes), len(self.provider_minutes), len(self.tick_gaps), len(self.recoveries))
        if version == self.comparison_version:
            return {**self.comparison_cache, "rejected_ticks": dict(self.tick_minutes.rejected)}
        compared, mismatches = 0, []
        for key in self.local_minutes.keys() & self.provider_minutes.keys():
            if key[0] not in self.symbols:
                continue
            local, official = self.local_minutes[key][3], self.provider_minutes[key][3]
            compared += 1
            if abs(local - official) > max(.01, abs(official) * 1e-9):
                mismatches.append({"symbol": key[0], "slot": key[1], "tick_amount": local,
                                   "provider_amount": official, "difference": local - official})
        def event_levels(detector):
            levels = {}
            if detector is None:
                return levels
            for event in detector.events:
                symbol = event["symbol"]
                common = min(self.tick_detector.latest.get(symbol, -1), self.provider_detector.latest.get(symbol, -1))
                known = [s["level"] for s in event["updates"] if SLOT_INDEX[s["end"]] <= common]
                if known:
                    levels[event["id"]] = max(known)
            return levels
        tick_events, provider_events = event_levels(self.tick_detector), event_levels(self.provider_detector)
        event_diffs = [{"id": key, "tick_level": tick_events.get(key), "provider_level": provider_events.get(key)}
                       for key in sorted(tick_events.keys() | provider_events.keys())
                       if tick_events.get(key) != provider_events.get(key)]
        result = {"minute_driver": self.minute_driver, "compared": compared,
                "mismatched": len(mismatches), "differences": sorted(mismatches, key=lambda r: (r["slot"], r["symbol"]))[-20:],
                "missing": sum(s in self.symbols for s, _ in self.tick_gaps),
                "recovered": sum(s in self.symbols for s, _ in self.recoveries),
                "unrecovered": sum(s in self.symbols for s, _ in self.tick_gaps - self.recoveries),
                "rejected_ticks": dict(self.tick_minutes.rejected),
                "event_mismatches": len(event_diffs), "event_differences": event_diffs[-20:],
                "tick_events": len(self.tick_detector.events) if self.tick_detector else 0,
                "provider_events": len(self.provider_detector.events) if self.provider_detector else 0}
        self.comparison_version, self.comparison_cache = version, result
        return result

    def warm(self, api):
        now = datetime.now(provider.CHINA)
        if now.strftime("%H:%M") < "09:31":
            return
        mapping = {**self.mapping, **{code: code for code in self.index_codes.values() if code}}
        frame = api.get_price(list(mapping), start_date=self.day, end_date=self.day, frequency="1m",
                              fields=["close", "total_turnover", "volume"], adjust_type="none")
        if frame is not None:
            # rqdatac 2.x names this level "datetime"; 3.x names it "date".
            level = "date" if "date" in frame.index.names else "datetime"
            for (code, t), row in frame.sort_index(level=level).iterrows():
                timestamp = provider.local_time(t)
                if timestamp <= now.replace(second=0, microsecond=0):
                    self.bar(mapping[code], timestamp, provider.number(row.close),
                             provider.number(row.total_turnover), provider.number(row.volume), recovered=True)
        self.flush()

    def handle(self, msg, received):
        channel = msg.get("channel", "")
        if not channel.startswith(("bar_", "tick_")) or "datetime" not in msg:
            return False
        code = msg.get("order_book_id")
        if code not in self.mapping and code not in self.index_codes.values():
            return False
        now = datetime.now(provider.CHINA)
        t = feed_time(msg["datetime"])
        if t.date().isoformat() != self.day or t > now + timedelta(seconds=2):
            return False
        symbol = self.mapping.get(code, code)
        self.advance_ticks(now)
        if channel.startswith("bar_"):
            if t.second != 0 or t > now:
                return False
            self.bar(symbol, t, provider.number(msg.get("close")), provider.number(msg.get("total_turnover")),
                     provider.number(msg.get("volume")), received)
            return True
        ts = int(t.timestamp() * 1000)
        close, cash = provider.number(msg.get("last")), provider.number(msg.get("total_turnover"))
        accepted = self.tick_minutes.add(symbol, t, close, cash)
        self.journal.append('tick', symbol, iso_utc(t), received,
                                {'last': close, 'total_turnover': cash, 'accepted': accepted,
                                 'volume': provider.number(msg.get('volume')),
                                 'iopv': provider.number(msg.get('iopv')),
                                 'ask': [provider.number(v) for v in msg.get('ask') or [msg.get('a1')]],
                                 'bid': [provider.number(v) for v in msg.get('bid') or [msg.get('b1')]]})
        if accepted and self.detector and symbol in self.symbols:
            self.second_candidates[symbol] = tick_candidates(
                self.tick_minutes, self.detector.baseline, self.tick_references, symbol, t)
        if symbol in BASKET + [INDEX] and close and close > 0 and (symbol == INDEX or cash is not None and cash >= 0) and ts > self.quotes.get(symbol, (0,))[0]:
            if symbol != INDEX and symbol in self.quotes and cash < self.quotes[symbol][2]:
                return False
            self.quotes[symbol] = (ts, close, cash)
            asks, bids = msg.get("ask") or [], msg.get("bid") or []
            ask = provider.number(asks[0] if asks else msg.get("a1"))
            bid = provider.number(bids[0] if bids else msg.get("b1"))
            iopv = provider.number(msg.get("iopv"))
            valid_book = ask is not None and bid is not None and ask >= bid > 0
            self.quote_details[symbol] = {"bid": bid if valid_book else None,
                                          "ask": ask if valid_book else None, "iopv": iopv,
                                          "premium_bp": ((ask + bid) / (2 * iopv) - 1) * 10000
                                          if valid_book and iopv and iopv > 0 else None}
        tape = self.ticks[symbol]
        if not tape or t > tape[-1][0]:
            asks, bids = msg.get("ask") or [], msg.get("bid") or []
            ask = provider.number(asks[0] if asks else msg.get("a1"))
            bid = provider.number(bids[0] if bids else msg.get("b1"))
            tape.append((t, ask, bid, provider.number(msg.get("iopv")), provider.number(msg.get("volume")), close))
        return True

    def evidence(self, event):
        symbol = event["symbol"]
        end = datetime.fromisoformat(self.day + "T" + event["last"]).replace(tzinfo=provider.CHINA)
        tape = [row for row in self.ticks[symbol] if end - timedelta(minutes=2) <= row[0] <= end]
        pressure, premium, since, discount, prior, last_iopv, changed = None, None, None, False, None, None, None
        changes = 0
        for t, ask, bid, iopv, vol, last in tape:
            valid = ask and bid and ask >= bid
            if valid and prior and end - t <= timedelta(seconds=30):
                _, pa, _, _, pv = prior
                changes += int(pa is not None and pv is not None and vol is not None and ask > pa and vol > pv)
            prior = t, ask, bid, iopv, vol
            if iopv != last_iopv:
                changed, last_iopv = t, iopv
            if not (valid and iopv and iopv > 0 and changed and t - changed <= timedelta(seconds=30)):
                since = None
                continue
            mid = (ask + bid) / 2
            bp = (mid / iopv - 1) * 10000
            premium = {"bp": round(bp, 2), "confirmed": False}
            discount |= bp < 0
            if discount and bp > max(5, (ask - bid) / (2 * iopv) * 10000):
                since = since or t
                premium["confirmed"] = t - since >= timedelta(seconds=30)
                if premium["confirmed"]:
                    discount = False
                    event["evidence"]["premium_cross"] = premium
            else:
                since = None
        if len(tape) > 1:
            pressure = {"ask_up_with_volume": changes}
        other = sorted({e["symbol"] for e in self.detector.events if e["symbol"] != symbol
                        and self.groups[e["symbol"]] == self.groups[symbol]
                        and 0 <= (end - datetime.fromisoformat(self.day + "T" + e["last"]).replace(
                            tzinfo=provider.CHINA)).total_seconds() <= 120})
        code = self.index_codes[symbol]
        close = self.minutes.get((code, event["last"]), (None,) * 4)[2]
        before = self.minutes.get((code, (end - timedelta(minutes=2)).strftime("%H:%M")), (None,) * 4)[2]
        index = None
        if close and before:
            ret = close / before - 1
            p95 = self.index_baseline.get((code, event["last"]))
            index = {"pct": ret * 100, "above_p95": p95 is not None and ret > p95}
        peak = event.get("peak") or {}
        start_slot, end_slot = peak.get("start") or event["first"], peak.get("end") or event["last"]
        start = datetime.fromisoformat(self.day + "T" + start_slot).replace(tzinfo=provider.CHINA)
        finish = datetime.fromisoformat(self.day + "T" + end_slot).replace(tzinfo=provider.CHINA)
        direction = etf_quote_direction([row for row in self.ticks[symbol] if start <= row[0] <= finish])
        for key, value in {"premium": premium, "pressure": pressure, "peers": other or None,
                           "index": index, "etf_direction": direction}.items():
            if value is not None:
                event["evidence"][key] = value

    def flush(self):
        if not self.detector:
            return
        now = datetime.now(provider.CHINA)
        self.advance_ticks(now)
        self.update_futures_evidence(now)
        for event in self.detector.events:
            if event["last"] >= (now - timedelta(minutes=2)).strftime("%H:%M"):
                self.evidence(event)
        report = self.detector.payload()
        report["feed"] = "push"
        report["baseline_ready"] = self.baseline_ready
        if self.futures is not None:
            report["futures_monitor"] = {"state": self.futures.state, "products": list(self.futures.products)}
        report["candidates"] = self.candidates(now)
        report['tick_candidates'] = [c for rows in self.second_candidates.values() for c in rows
                                     if 0 <= (now - datetime.fromisoformat(c['observed_at'])).total_seconds() <= 10]
        report['tick_factor_status'] = {'state': self.tick_reference_status, 'version': FACTOR_VERSION,
                                        'covered': len(self.tick_references), 'expected': len(self.symbols)}
        states = {(c['symbol'], c.get('method', 'aligned')): c
                  for c in report['candidates'] + report['tick_candidates']}
        for key, candidate in states.items():
            if candidate != self.candidate_states.get(key):
                self.journal.append('candidate', key[0], candidate['as_of'], iso_utc(now), candidate)
        for key in self.candidate_states.keys() - states.keys():
            self.journal.append('candidate_removed', key[0], iso_utc(now), iso_utc(now), {'method': key[1]})
        self.candidate_states = states
        for event in self.detector.events:
            end = datetime.fromisoformat(self.day + 'T' + event['last']).replace(tzinfo=provider.CHINA)
            if event['id'] not in self.ended_events and now > end + timedelta(minutes=11, seconds=2):
                self.journal.append('end', event['symbol'], iso_utc(end), iso_utc(now), {'event_id': event['id']})
                self.ended_events.add(event['id'])
        self.journal.commit()
        report['journal'] = {'enabled': True, 'format': 'sqlite-append-v1'}
        report["tick_monitor"] = self.tick_comparison()
        signature = json.dumps({key: value for key, value in report.items() if key != "updated_at"})
        if signature != self.last_report:
            publish(self.burst_path, report)
            archive = self.burst_path.parent / "sessions" / f"{self.day}.json"
            publish(archive, report)
            self.last_report = signature
        if self.minute_version != len(self.minutes):
            self.board = live_snapshot({"calendar": self.calendar, "previous": self.previous,
                               "minutes": [r for (s, _), r in self.minutes.items() if s in BASKET or s == INDEX],
                               "daily": []}, self.funds, self.day, False, partial=True)
            self.minute_version = len(self.minutes)
        value = dict(self.board) if self.board else None
        if value and self.quotes:
            previous = {s: close for s, _, close, _ in self.previous}
            value["table"] = []
            for row in self.board["table"]:
                symbol = row["symbol"]
                ms, close, cash = self.quotes.get(symbol, (None, None, None))
                prior = previous.get(symbol)
                value["table"].append({**row, "price": close, "amount": cash / 1e8 if cash is not None else None,
                                       "pct": (close / prior - 1) * 100 if close and prior and prior > 0 else None,
                                       "quote_at": iso_utc(datetime.fromtimestamp(ms / 1000, provider.CHINA)) if ms else None,
                                       **self.quote_details.get(symbol, {})})
            value["amount"] = sum(r["amount"] for r in value["table"]) if all(r["amount"] is not None for r in value["table"]) else None
            value["quote_coverage"] = sum(r["quote_at"] is not None for r in value["table"])
            value["index_quote"] = None
            if INDEX in self.quotes:
                ms, close, _ = self.quotes[INDEX]
                value["index_quote"] = {"price": close, "quote_at": iso_utc(datetime.fromtimestamp(ms / 1000, provider.CHINA))}
            latest = max(v[0] for v in self.quotes.values())
            value["fetched_at"] = iso_utc(datetime.fromtimestamp(latest / 1000, provider.CHINA))
            value["feed"] = "push"
            signature = json.dumps(value, ensure_ascii=False, sort_keys=True)
            if signature == self.board_signature:
                self.last_publish = monotonic()
                return
            self.board_signature = signature
            value["published_at"] = iso_utc(now)
            publish(self.path, {"schema": 1, "entries": {"live:rqdata:snapshot": json.dumps(
                {"value": value, "checked_at": iso_utc(now)}, ensure_ascii=False)}})
        self.last_publish = monotonic()


class FuturesStream:
    """Connect and retry away from the ETF collection loop."""

    def __init__(self, api, codes):
        self.api = api
        self.channels = ["tick_" + code for code in codes]
        self.messages = Queue(maxsize=12000)
        self.stopped = Event()
        self.client = None
        self.state = "waiting"
        Thread(target=self.run, daemon=True).start()

    def run(self):
        while not self.stopped.is_set():
            try:
                self.client = self.api.LiveMarketDataClient()
                if self.stopped.is_set():
                    return
                self.client.subscribe(self.channels)
                self.state = "waiting"
                for msg in self.client.listen():
                    if self.stopped.is_set():
                        return
                    self.messages.put_nowait((msg, iso_utc(datetime.now(provider.CHINA))))
                    self.state = "receiving"
            except Exception as error:
                if not self.stopped.is_set():
                    print(f"Futures Tick stream unavailable ({type(error).__name__})", flush=True)
            finally:
                if self.client:
                    with suppress(Exception):
                        self.client.close()
                    self.client = None
            self.state = "feed_unavailable"
            self.stopped.wait(30)

    def close(self):
        self.stopped.set()
        client = self.client
        if client:
            Thread(target=client.close, daemon=True).start()


def watch(api, funds, path, burst_dir, q, continuous, symbols=BASKET, minute_driver="provider", futures_enabled=False):
    session = None
    heartbeat = Path(path).with_name("collector.heartbeat")
    while True:
        try:
            heartbeat.write_text(iso_utc(datetime.now(provider.CHINA)), encoding="utf-8")
        except OSError:
            pass
        now = datetime.now(provider.CHINA)
        day = now.date().isoformat()
        active = trading_minute(now)
        preparing = continuous and now.weekday() < 5 and "09:15" <= now.strftime("%H:%M") < "09:30"
        if (not active and not preparing) or (session and session.day == day and not session.detector):
            if not continuous:
                return
            sleep(15)
            continue
        client = None
        future_stream = None
        try:
            if session is None or session.day != day:
                if session:
                    session.journal.close()
                session = PushSession(api, day, funds, path, burst_dir, q, symbols, minute_driver, futures_enabled)
            if not session.detector:
                continue
            if preparing:
                sleep(15)
                continue
            session.warm(api)
            client = api.LiveMarketDataClient()
            codes = list(session.mapping)
            indices = sorted({code for code in session.index_codes.values() if code})
            client.subscribe(["bar_" + code for code in dict.fromkeys(codes + indices)]
                             + ["tick_" + code for code in dict.fromkeys(codes + indices)])
            messages = Queue(maxsize=20000)
            overflow = Event()
            def enqueue(msg):
                try:
                    messages.put_nowait((msg, iso_utc(datetime.now(provider.CHINA))))
                except Full:
                    overflow.set()
            worker = client.listen(handler=enqueue)
            if session.futures and session.futures.rows:
                future_stream = FuturesStream(api, session.futures.rows)
            last_futures_input = monotonic()
            connected = monotonic()
            last_input = connected
            last_warm = connected
            pending = False
            while True:
                if overflow.is_set():
                    raise RuntimeError("RQData queue overflow; reconnect and recover minute bars")
                current = datetime.now(provider.CHINA)
                hm = current.strftime("%H:%M")
                # Keep one subscription alive through the lunch break. Closing
                # at 11:30 and reconnecting at 13:00 can miss 13:01/13:02.
                if not (current.weekday() < 5 and "09:30" <= hm <= "15:02"):
                    break
                lunch = "11:30" < hm < "13:00"
                if lunch:
                    # No market messages during lunch; do not trip the 45s
                    # stream watchdog, and keep the heartbeat fresh.
                    last_input = monotonic()
                if future_stream:
                    if lunch:
                        last_futures_input = monotonic()
                    for _ in range(512):
                        try:
                            futures_msg, futures_received = future_stream.messages.get_nowait()
                        except Empty:
                            break
                        if session.handle_futures(futures_msg, futures_received, current):
                            last_futures_input = monotonic()
                    if not lunch and monotonic() - last_futures_input >= 45:
                        session.futures.state = "feed_unavailable"
                    else:
                        session.futures.state = future_stream.state
                try:
                    msg, received = messages.get(timeout=1)
                    if session.handle(msg, received):
                        pending = True
                        last_input = monotonic()
                except Empty:
                    if not worker.is_alive() or (not lunch and monotonic() - last_input >= 45):
                        raise RuntimeError("RQData stream stopped")
                if monotonic() - session.last_publish >= 1:
                    session.flush()
                    pending = False
                # Re-read today's 1m bars periodically: fills any minutes the
                # push stream missed (for example after a process restart).
                if monotonic() - last_warm >= 300:
                    session.warm(api)
                    last_warm = monotonic()
                if not lunch and monotonic() - last_input >= 45:
                    raise RuntimeError("RQData stream has no usable messages")
                try:
                    heartbeat.write_text(iso_utc(datetime.now(provider.CHINA)), encoding="utf-8")
                except OSError:
                    pass
                if not continuous and monotonic() - connected >= 75:
                    if pending:
                        session.flush()
                    session.journal.close()
                    return
            if pending:
                session.flush()
        except MinuteDriverConflict as error:
            print(str(error), flush=True)
            raise
        except Exception as error:
            if not continuous:
                raise
            print(f"RQData push interrupted ({type(error).__name__}); retrying", flush=True)
            sleep(5)
        finally:
            if future_stream:
                future_stream.close()
            if client:
                client.close()
