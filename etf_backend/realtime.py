"""RQData push collector. Existing board and per-fund radar share one feed."""

from collections import defaultdict, deque
from datetime import datetime, timedelta
import json
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event
from time import monotonic, sleep

from . import provider
from .burst import BurstDay, query_history, make_baseline, SLOT_INDEX, RULE_VERSION
from .tick_minutes import TickMinutes, partial_signals
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


class PushSession:
    def __init__(self, api, day, funds, path, burst_dir, q, symbols=BASKET, minute_driver="provider"):
        if minute_driver not in {"provider", "tick"}:
            raise ValueError("minute_driver must be provider or tick")
        self.minute_driver = minute_driver
        self.day, self.path, self.funds = day, path, funds
        self.symbols = list(symbols)
        self.burst_path = burst_dir / "live.json"
        if self.burst_path.is_file():
            previous = read(self.burst_path)
            previous_driver = previous.get("tick_monitor", {}).get("minute_driver", "provider")
            if previous.get("date") == day and previous_driver != minute_driver:
                raise MinuteDriverConflict("Changing minute_driver during a session requires a separate ETF_BURST_DIR")
        self.minutes, self.quotes = {}, {}
        self.quote_details = {}
        self.board = None
        self.minute_version = -1
        self.board_signature = None
        self.reference_path = burst_dir / "baselines" / f"{day}.json"
        self.ticks = defaultdict(lambda: deque(maxlen=320))
        self.mapping = {s.replace(".SH", ".XSHG").replace(".SZ", ".XSHE"): s for s in self.symbols + [INDEX]}
        self.groups = {f["symbol"]: f["bucket"] for f in funds if f["symbol"] in self.symbols}
        self.index_codes = {s: INDEX_GROUPS.get(group) for s, group in self.groups.items()}
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
        self.provider_detector = self.detector
        self.tick_detector = None
        if self.detector:
            self.tick_detector = BurstDay(day, self.detector.baseline, q, symbols=self.symbols,
                                          baseline_as_of=self.calendar[-2])
            if minute_driver == "tick":
                self.detector = self.tick_detector

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
        self.tick_minutes.add(symbol, t, close, cash)
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
            tape.append((t, ask, bid, provider.number(msg.get("iopv")), provider.number(msg.get("volume"))))
        return True

    def evidence(self, event):
        symbol = event["symbol"]
        end = datetime.fromisoformat(self.day + "T" + event["last"]).replace(tzinfo=provider.CHINA)
        tape = [row for row in self.ticks[symbol] if end - timedelta(minutes=2) <= row[0] <= end]
        pressure, premium, since, discount, prior, last_iopv, changed = None, None, None, False, None, None, None
        changes = 0
        for t, ask, bid, iopv, vol in tape:
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
        for key, value in {"premium": premium, "pressure": pressure, "peers": other or None, "index": index}.items():
            if value is not None:
                event["evidence"][key] = value

    def flush(self):
        if not self.detector:
            return
        now = datetime.now(provider.CHINA)
        self.advance_ticks(now)
        for event in self.detector.events:
            if event["last"] >= (now - timedelta(minutes=2)).strftime("%H:%M"):
                self.evidence(event)
        report = self.detector.payload()
        report["feed"] = "push"
        report["baseline_ready"] = self.baseline_ready
        report["candidates"] = self.candidates(now)
        report["tick_monitor"] = self.tick_comparison()
        signature = json.dumps({key: value for key, value in report.items() if key != "updated_at"})
        if signature != self.last_report:
            publish(self.burst_path, report)
            archive = self.burst_path.parent / "sessions" / f"{self.day}.json"
            if not archive.exists() or report["events"]:
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


def watch(api, funds, path, burst_dir, q, continuous, symbols=BASKET, minute_driver="provider"):
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
        try:
            if session is None or session.day != day:
                session = PushSession(api, day, funds, path, burst_dir, q, symbols, minute_driver)
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
            if client:
                client.close()
