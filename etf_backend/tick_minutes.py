"""Aligned minute estimates from L1 cumulative turnover, never tick-size sums.

Snapshots cannot reveal which side of a minute boundary every trade belongs to.
Keep provider bars for comparison/recovery before enabling this as the driver.
"""
from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta

from .model import SLOTS, finite
from .provider import CHINA
from .burst import BurstDay, SLOT_INDEX, detect

FINALIZE_DELAY = timedelta(seconds=2)
MAX_BOUNDARY_AGE = timedelta(seconds=10)
CLOSE_WINDOW = timedelta(seconds=5)


class TickMinutes:
    def __init__(self, day, symbols, turnover_symbols, boundary_mode="provider-v2"):
        if boundary_mode not in {"provider-v2", "legacy-right"}:
            raise ValueError('Unknown Tick minute boundary mode')
        self.boundary_mode = boundary_mode
        self.legacy = boundary_mode == "legacy-right"
        self.day = day
        self.symbols = list(symbols)
        self.turnover_symbols = set(turnover_symbols)
        self.ends = [datetime.fromisoformat(day + "T" + slot).replace(tzinfo=CHINA) for slot in SLOTS]
        self.times = {s: [] for s in symbols}
        self.rows = {s: [] for s in symbols}
        self.bad = set()
        self.next_slot = 0
        self.rejected = {"late": 0, "duplicate": 0, "regression": 0}

    def add(self, symbol, timestamp, close, cumulative):
        if symbol not in self.rows or timestamp.date().isoformat() != self.day or not finite(close) or close <= 0:
            return False
        if symbol in self.turnover_symbols and (not finite(cumulative) or cumulative < 0):
            return False
        sealed = self.ends[self.next_slot - 1] if self.next_slot else None
        late = sealed and (timestamp <= sealed if self.legacy else
                          timestamp <= sealed + CLOSE_WINDOW if self.is_close(sealed) else timestamp < sealed)
        if late:
            self.rejected["late"] += 1
            return False
        times, rows = self.times[symbol], self.rows[symbol]
        offset = bisect_right(times, timestamp)
        if offset and times[offset - 1] == timestamp:
            self.rejected["duplicate"] += 1
            return False
        neighbors = rows[max(0, offset - 1):offset + 1]
        if finite(cumulative) and any(finite(r[2]) and ((r[0] < timestamp and r[2] > cumulative) or
                                                       (r[0] > timestamp and r[2] < cumulative)) for r in neighbors):
            self.rejected["regression"] += 1
            slot = bisect_right(self.ends, timestamp - (timedelta(microseconds=1) if self.legacy else timedelta()))
            if not self.legacy and slot and self.is_close(self.ends[slot - 1]) and timestamp <= self.ends[slot - 1] + CLOSE_WINDOW:
                slot -= 1
            self.bad.add((symbol, slot))
            return False
        times.insert(offset, timestamp)
        rows.insert(offset, (timestamp, close, cumulative))
        return True

    @staticmethod
    def is_close(end):
        return end.strftime('%H:%M:%S') in {'11:30:00', '15:00:00'}

    def endpoint(self, symbol, end, *, afternoon_start=False, current=False):
        closing = self.is_close(end) and not self.legacy and not current
        target = end + CLOSE_WINDOW if closing else end
        search = bisect_right if closing or current or self.legacy else bisect_left
        offset = search(self.times[symbol], target) - 1
        if offset < 0:
            return None
        row = self.rows[symbol][offset]
        if -CLOSE_WINDOW <= end - row[0] <= MAX_BOUNDARY_AGE:
            return row
        # No trading during lunch: a fresh AM closing snapshot may anchor 13:00.
        if afternoon_start:
            am_end = end.replace(hour=11, minute=30)
            if not self.legacy:
                closing = bisect_right(self.times[symbol], am_end + CLOSE_WINDOW) - 1
                if closing >= 0:
                    row = self.rows[symbol][closing]
            if am_end - MAX_BOUNDARY_AGE <= row[0] <= am_end + (CLOSE_WINDOW if not self.legacy else timedelta()):
                return row
        return None

    def amount(self, symbol, index, end_row):
        if (symbol, index) in self.bad:
            return None
        if symbol not in self.turnover_symbols:
            return None
        if index == 0:
            return end_row[2]  # day cumulative includes opening-auction turnover
        start = self.ends[index] - timedelta(minutes=1)
        before = self.endpoint(symbol, start, afternoon_start=start.strftime("%H:%M") == "13:00")
        if before is None or not finite(before[2]):
            return None
        delta = end_row[2] - before[2]
        return delta if delta >= 0 else None

    def advance(self, now):
        """Seal elapsed minutes: 2s grace normally, 6s at session closes."""
        output = []
        while self.next_slot < len(self.ends):
            delay = CLOSE_WINDOW + timedelta(seconds=1) if not self.legacy and self.is_close(self.ends[self.next_slot]) else FINALIZE_DELAY
            if self.ends[self.next_slot] + delay > now:
                break
            index = self.next_slot
            end = self.ends[index]
            for symbol in self.symbols:
                row = self.endpoint(symbol, end)
                cash = self.amount(symbol, index, row) if row else None
                valid = row is not None and (symbol not in self.turnover_symbols or cash is not None)
                output.append({"symbol": symbol, "end": end, "close": row[1] if valid else None,
                               "amount": cash, "valid": valid, "tick_at": row[0] if row else None})
            self.next_slot += 1
        return output

    def partial(self, symbol, now):
        """Current aligned window, conservatively compared to full-window refs."""
        index = bisect_right(self.ends, now)
        if index >= len(self.ends) or index < self.next_slot:
            return None
        end = self.ends[index]
        start = end - timedelta(minutes=1)
        if not start <= now < end:
            return None  # opening/lunch gaps are not trading minutes
        row = self.endpoint(symbol, now, current=True)
        if row is None or row[0] < start:
            return None
        cash = self.amount(symbol, index, row)
        if cash is None:
            return None
        return {"slot": SLOTS[index], "amount": cash, "as_of": row[0]}


def partial_signals(aggregator, detector, now):
    """Reuse the formal formula on a copy; never mutate confirmed event state."""
    result = []
    for symbol in detector.values:
        partial = aggregator.partial(symbol, now)
        if not partial:
            continue
        end = SLOT_INDEX[partial["slot"]]
        values = list(detector.values[symbol])
        values[end] = partial["amount"]
        signal = detect(values, end, detector.baseline[symbol])
        if signal:
            result.append({"symbol": symbol, "as_of": partial["as_of"].isoformat(),
                           "minute_end": partial["slot"], "signal": signal, "provisional": True})
    return result


def replay_tick_day(day, baseline, q, ticks, symbols, baseline_as_of=None, boundary_mode='legacy-right',
                    factor_references=None, include_volume=False):
    """Replay recorded reception order; do not move late data back in time.

    Rows: [symbol, market_timestamp, received_timestamp, close, day_turnover].
    Historical 1m/1d data is used only to prepare the supplied prior-day baseline.
    """
    aggregator = TickMinutes(day, symbols, symbols, boundary_mode)
    volume_aggregator = TickMinutes(day, symbols, symbols, boundary_mode) if include_volume else None
    volumes = {}
    detector = BurstDay(day, baseline, q, replay=True, symbols=symbols, baseline_as_of=baseline_as_of)
    last_received, candidates, levels = None, [], {}
    tick_history, factor_states = [], {}

    def advance(now):
        for row in aggregator.advance(now):
            if row["valid"]:
                detector.add(row["symbol"], row["end"].strftime("%H:%M"), row["amount"],
                             None if boundary_mode == 'legacy-right' else now.isoformat())
        if volume_aggregator:
            for row in volume_aggregator.advance(now):
                if row["valid"]:
                    volumes.setdefault(row["symbol"], {})[row["end"].strftime("%H:%M")] = row["amount"]

    for symbol, market, received, close, cash, *extra in ticks:
        market = datetime.fromisoformat(market.replace("Z", "+00:00"))
        received = datetime.fromisoformat(received.replace("Z", "+00:00"))
        if market.utcoffset() is None or received.utcoffset() is None:
            raise ValueError("Tick replay timestamps require timezone offsets")
        market, received = market.astimezone(CHINA), received.astimezone(CHINA)
        if market.date().isoformat() != day or symbol not in symbols:
            continue
        if last_received and received < last_received:
            raise ValueError("Tick replay must be in reception order")
        if market > received + FINALIZE_DELAY:
            raise ValueError("Tick market timestamp is in the future")
        last_received = received
        advance(received)
        accepted = aggregator.add(symbol, market, close, cash)
        if volume_aggregator and extra:
            volume_aggregator.add(symbol, market, close, extra[0])
        if accepted and factor_references and 0 <= (received - market).total_seconds() <= 10:
            from .tick_factors import candidates as factor_candidates
            for candidate in factor_candidates(aggregator, baseline, factor_references, symbol, market):
                key = symbol, candidate['method']
                if candidate['as_of'] != factor_states.get(key):
                    tick_history.append({**candidate, 'received_at': received.isoformat()})
                    factor_states[key] = candidate['as_of']
        for candidate in partial_signals(aggregator, detector, received):
            key = candidate["symbol"], candidate["minute_end"]
            if candidate["signal"]["level"] > levels.get(key, 0):
                levels[key] = candidate["signal"]["level"]
                candidates.append({**candidate, 'received_at': received.isoformat()})
    advance(datetime.fromisoformat(day + "T15:00:06").replace(tzinfo=CHINA))
    return {**detector.payload(), "feed": "tick-replay", "minute_driver": "tick",
            "candidate_history": candidates, "tick_rejections": aggregator.rejected, 'boundary_mode': boundary_mode,
            'tick_candidate_history': tick_history, **({'minute_volume_shares': volumes} if include_volume else {})}
