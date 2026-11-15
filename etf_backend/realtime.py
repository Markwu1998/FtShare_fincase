"""RQData push collector. Existing board and per-fund radar share one feed."""

from collections import defaultdict, deque
from datetime import datetime, timedelta
import json
from queue import Empty, Queue
from time import monotonic, sleep

from . import provider
from .burst import BurstDay, query_history, make_baseline, SLOT_INDEX
from .collect import live_snapshot, publish, read
from .model import BASKET, INDEX, SLOTS, iso_utc, trading_minute

INDEX_GROUPS = {"沪深300": "000300.XSHG", "中证500": "000905.XSHG", "中证A500": "000510.XSHG",
                "科创50": "000688.XSHG", "创业板指": "399006.XSHE", "中证1000": "000852.XSHG",
                "上证50": "000016.XSHG", "创业板50": "399673.XSHE"}


def feed_time(value):
    raw = str(value)
    if raw.isdigit() and len(raw) in (14, 17):
        return datetime.strptime(raw[:14], "%Y%m%d%H%M%S").replace(
            microsecond=int(raw[14:] or 0) * 1000, tzinfo=provider.CHINA)
    return provider.local_time(value)


class PushSession:
    def __init__(self, api, day, funds, path, burst_dir, q):
        self.day, self.path, self.funds = day, path, funds
        self.burst_path = burst_dir / "live.json"
        self.minutes, self.quotes = {}, {}
        self.ticks = defaultdict(lambda: deque(maxlen=320))
        self.mapping = {s.replace(".SH", ".XSHG").replace(".SZ", ".XSHE"): s for s in BASKET + [INDEX]}
        self.groups = {f["symbol"]: f["bucket"] for f in funds if f["symbol"] in BASKET}
        self.index_codes = {s: INDEX_GROUPS.get(group) for s, group in self.groups.items()}
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
            self.detector = BurstDay(day, make_baseline([], {}, {}, day, q), q)
            self.load_baseline(api)
        self.last_board = None

    def load_baseline(self, api):
        previous = self.calendar[-2]
        first = self.calendar[max(0, len(self.calendar) - 61)]
        try:
            minutes, daily = query_history(api, first, previous)
            q = self.detector.quantile
            self.detector.baseline = make_baseline(self.calendar, minutes, daily, self.day, q)
            self.baseline_ready = any(all(v is not None and v > 0 for v in totals)
                                      for totals, _ in self.detector.baseline.values())
        except Exception as error:
            print(f"ETF volume baseline unavailable ({type(error).__name__})", flush=True)
        try:
            refs = api.get_price(sorted({code for code in self.index_codes.values() if code}),
                                 start_date=first, end_date=previous,
                                 frequency="1m", fields=["close"], adjust_type="none")
            self.index_baseline = self.index_thresholds(refs)
        except Exception as error:
            print(f"Index baseline unavailable ({type(error).__name__})", flush=True)

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
        if slot not in SLOTS or (symbol, slot) in self.minutes:
            return
        if not (close is not None and close > 0):
            return
        if symbol in BASKET and (cash is None or cash < 0 or volume is None or volume < 0):
            return
        self.minutes[symbol, slot] = (symbol, int(timestamp.timestamp() * 1000), close, cash)
        if symbol in BASKET and self.detector is not None:
            end = SLOT_INDEX[slot]
            if end < self.detector.latest.get(symbol, -1):
                if recovered and self.detector.values[symbol][end] is None:
                    self.detector.values[symbol][end] = volume
            else:
                self.detector.add(symbol, slot, volume, received, recovered=recovered)

    def warm(self, api):
        now = datetime.now(provider.CHINA)
        if now.strftime("%H:%M") < "09:31":
            return
        mapping = {**self.mapping, **{code: code for code in self.index_codes.values() if code}}
        frame = api.get_price(list(mapping), start_date=self.day, end_date=self.day, frequency="1m",
                              fields=["close", "total_turnover", "volume"], adjust_type="none")
        if frame is not None:
            for (code, t), row in frame.sort_index(level="date").iterrows():
                timestamp = provider.local_time(t)
                if timestamp < now.replace(second=0, microsecond=0):
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
        if channel.startswith("bar_"):
            if t.second != 0 or t > now:
                return False
            self.bar(symbol, t, provider.number(msg.get("close")), provider.number(msg.get("total_turnover")),
                     provider.number(msg.get("volume")), received)
            return True
        ts = int(t.timestamp() * 1000)
        close, cash = provider.number(msg.get("last")), provider.number(msg.get("total_turnover"))
        if symbol in BASKET and close and cash is not None and ts > self.quotes.get(symbol, (0,))[0]:
            self.quotes[symbol] = (ts, close, cash)
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
        for event in self.detector.events:
            if event["last"] >= (now - timedelta(minutes=2)).strftime("%H:%M"):
                self.evidence(event)
        report = self.detector.payload()
        report["feed"] = "push"
        report["baseline_ready"] = self.baseline_ready
        signature = json.dumps({key: value for key, value in report.items() if key != "updated_at"})
        if signature != self.last_report:
            publish(self.burst_path, report)
            archive = self.burst_path.parent / "sessions" / f"{self.day}.json"
            if not archive.exists() or report["events"]:
                publish(archive, report)
            self.last_report = signature
        value = live_snapshot({"calendar": self.calendar, "previous": self.previous,
                               "minutes": [r for (s, _), r in self.minutes.items() if s in BASKET or s == INDEX],
                               "daily": [[s, *v] for s, v in self.quotes.items()]}, self.funds, self.day, False)
        if value and (self.last_board is None or value["last_complete_minute"] >= self.last_board):
            self.last_board = value["last_complete_minute"]
            value["feed"] = "push"
            publish(self.path, {"schema": 1, "entries": {"live:rqdata:snapshot": json.dumps(
                {"value": value, "checked_at": iso_utc(now)}, ensure_ascii=False)}})
        self.last_publish = monotonic()


def watch(api, funds, path, burst_dir, q, continuous):
    session = None
    while True:
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
                session = PushSession(api, day, funds, path, burst_dir, q)
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
                             + ["tick_" + code for code in codes])
            messages = Queue(maxsize=20000)
            def enqueue(msg):
                messages.put((msg, iso_utc(datetime.now(provider.CHINA))), timeout=5)
            worker = client.listen(handler=enqueue)
            connected = monotonic()
            last_input = connected
            pending = False
            while trading_minute(datetime.now(provider.CHINA)):
                try:
                    msg, received = messages.get(timeout=1)
                    if session.handle(msg, received):
                        pending = True
                        last_input = monotonic()
                except Empty:
                    if not worker.is_alive() or monotonic() - last_input >= 45:
                        raise RuntimeError("RQData stream stopped")
                if pending and monotonic() - session.last_publish >= 1:
                    session.flush()
                    pending = False
                if monotonic() - last_input >= 45:
                    raise RuntimeError("RQData stream has no usable messages")
                if not continuous and monotonic() - connected >= 75:
                    if pending:
                        session.flush()
                    return
            if pending:
                session.flush()
        except Exception as error:
            if not continuous:
                raise
            print(f"RQData push interrupted ({type(error).__name__}); retrying", flush=True)
            sleep(5)
        finally:
            if client:
                client.close()
