"""Per-ETF short-window volume signals, shared by live collection and replay."""

from datetime import datetime, timedelta
from math import floor
from statistics import median

from .model import BASKET, SLOTS, finite, iso_utc
from .provider import CHINA, local_time, number

WINDOWS = (1, 2, 5, 10)
AFTERNOON = SLOTS.index("13:01")
END = SLOTS.index("14:57")
SLOT_INDEX = {slot: i for i, slot in enumerate(SLOTS)}
DEFAULT_DIR = "data/private/etf-burst"


def total_window(values, end, length):
    start = end + 1 - length
    if start < (AFTERNOON if end >= AFTERNOON else 0) or end > END:
        return None
    window = values[start:end + 1]
    if len(window) == length and all(finite(v) and v >= 0 for v in window):
        return sum(window)
    return None


def quantile(values, q):
    ordered = sorted(values)
    rank = (len(ordered) - 1) * q
    lo = floor(rank)
    return ordered[lo] + (ordered[min(lo + 1, len(ordered) - 1)] - ordered[lo]) * (rank - lo)


def make_baseline(days, minutes, daily, as_of, q, symbols=BASKET):
    if not finite(q) or not 0 < q < 1:
        raise ValueError("amount_window_quantile must be between 0 and 1")
    dates = [day for day in days if day < as_of][-60:]
    result = {}
    for symbol in symbols:
        full = [daily.get(day, {}).get(symbol) for day in dates]
        pairs = [tuple(v for v in full[-n:] if finite(v) and v >= 0) for n in (20, 60)]
        totals = [median(v) if len(v) >= n else None for v, n in zip(pairs, (19, 55))]
        histories = [minutes.get(day, {}).get(symbol, []) for day in dates]
        series = {}
        for length in WINDOWS:
            columns = zip(*[[total_window(values, i, length) for i in range(END + 1)] for values in histories])
            for i, values in enumerate(columns):
                pairs = [[v for v in values[-n:] if v is not None] for n in (20, 60)]
                series[length, i] = [
                    [quantile(v, p) if len(v) >= n else None for v, n in zip(pairs, (19, 55))]
                    for p in (q, .5)]
        result[symbol] = totals, series
    return result


def multiples(volume, values):
    return [volume / v if finite(v) and v > 0 else None for v in values]


def detect(values, end, baseline, shadow=False):
    daily, reference = baseline
    scores = {}
    for length in WINDOWS:
        v = total_window(values, end, length)
        if v is not None:
            scores[length] = (v, multiples(v, reference.get((length, end), ([None] * 2,) * 2)[int(shadow)]),
                              multiples(v, daily))

    def passed(length, multiple, share):
        row = scores.get(length)
        return bool(row and all(x is not None and x >= multiple for x in row[1])
                    and all(x is not None and x >= share for x in row[2]))

    for level, candidates in ((3, (10, 5)), (2, (10, 5)), (1, (2,))):
        for length in candidates:
            row = scores.get(length)
            if level == 3:
                ready = row and all(x is not None and x >= 1 for x in row[2])
            elif level == 2:
                ready = passed(length, 5, .20 if length == 10 else .12)
            else:
                previous = total_window(values, end - 1, 1) if end else None
                prev_ref = reference.get((1, end - 1), ([None] * 2,) * 2)[int(shadow)]
                ready = passed(2, 5, .04) and passed(1, 2, 0) and previous is not None and all(
                    x is not None and x >= 2 for x in multiples(previous, prev_ref))
            if ready:
                start = end - length + 1
                return {"level": level, "window": length, "amount": row[0], "ratios": row[1], "daily": row[2],
                        "start": SLOTS[start], "end": SLOTS[end],
                        "minutes": values[start:end + 1],
                        "minute_refs": [reference.get((1, i), ([None] * 2,) * 2)[int(shadow)]
                                        for i in range(start, end + 1)]}
    return None


class BurstDay:
    def __init__(self, day, baseline, quantile_value, replay=False, symbols=BASKET):
        self.day, self.baseline, self.quantile, self.replay = day, baseline, quantile_value, replay
        self.values = {s: [None] * len(SLOTS) for s in symbols}
        self.latest = {}
        self.events = []
        self.active = {}
        self.shadow = {}

    def add(self, symbol, slot, amount, received=None, *, recovered=False):
        end = SLOT_INDEX.get(slot, -1)
        if symbol not in self.values or not 0 <= end <= END or not finite(amount) or amount < 0:
            return
        previous = self.latest.get(symbol, -1)
        if end <= previous:
            return
        if end != previous + 1 or end == AFTERNOON:
            self.active.pop(symbol, None)
        self.latest[symbol] = end
        values = self.values[symbol]
        values[end] = amount
        signal = detect(values, end, self.baseline[symbol])
        shadow = detect(values, end, self.baseline[symbol], shadow=True)
        if shadow:
            self.shadow[symbol] = {"time": slot, "level": shadow["level"]}
        if not signal:
            return
        detected = None if self.replay else iso_utc(datetime.now(CHINA))
        event = self.active.get(symbol)
        if event is None or end - SLOT_INDEX[event["last"]] > 10:
            event = {"id": f"{self.day}/{symbol}/{slot}", "symbol": symbol, "first": slot, "last": slot,
                     "level": signal["level"], "peak": signal, "received_at": received, "detected_at": detected,
                     "market_at": f"{self.day}T{slot}:00+08:00", "recovered": recovered,
                     "updates": [signal], "evidence": {}}
            self.events.append(event)
            self.active[symbol] = event
        else:
            event["last"] = slot
            if self.replay:
                event["updates"].append(signal)
            if signal["level"] > event["level"]:
                event["level"] = signal["level"]
                if not self.replay:
                    event["updates"].append(signal)
                event["recovered"] = recovered
                event["detected_at"] = detected
                event["received_at"] = received
                event["market_at"] = f"{self.day}T{slot}:00+08:00"
            peak = event["peak"]
            if (signal["level"], min(signal["daily"], default=0) if None not in signal["daily"] else 0) > (
                    peak["level"], min(peak["daily"], default=0) if None not in peak["daily"] else 0):
                event["peak"] = signal

    def payload(self):
        return {"provider": "RQData", "date": self.day, "quantile": self.quantile, "metric": "turnover", "replay": self.replay,
                "through": SLOTS[max(self.latest.values())] if self.latest else None,
                "updated_at": iso_utc(datetime.now(CHINA)), "events": self.events, "median_hits": self.shadow}


def query_history(api, start, end, symbols=BASKET):
    mapping = {s.replace(".SH", ".XSHG").replace(".SZ", ".XSHE"): s for s in symbols}
    minutes, daily = {}, {}
    current = datetime.fromisoformat(start).date()
    finish = datetime.fromisoformat(end).date()
    while current <= finish:
        last = min(current + timedelta(days=30), finish)
        for frequency, output in (("1m", minutes), ("1d", daily)):
            frame = api.get_price(list(mapping), start_date=str(current), end_date=str(last),
                                  frequency=frequency, fields=["total_turnover"], adjust_type="none")
            if frame is None:
                continue
            for (code, stamp), raw in frame["total_turnover"].items():
                ts = local_time(stamp)
                date, slot = ts.date().isoformat(), ts.strftime("%H:%M")
                symbol, amount = mapping[code], number(raw)
                if frequency == "1d":
                    output.setdefault(date, {})[symbol] = amount
                elif slot in SLOT_INDEX:
                    output.setdefault(date, {}).setdefault(symbol, [None] * len(SLOTS))[SLOT_INDEX[slot]] = amount
        current = last + timedelta(days=1)
    return minutes, daily


def replay_day(day, dates, minutes, daily, q, symbols=BASKET):
    detector = BurstDay(day, make_baseline(dates, minutes, daily, day, q, symbols), q, replay=True, symbols=symbols)
    for i, slot in enumerate(SLOTS[:END + 1]):
        for symbol, values in minutes.get(day, {}).items():
            detector.add(symbol, slot, values[i])
    return detector.payload()
