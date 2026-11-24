"""Same-window index futures observations inferred from Level 1 snapshots."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .provider import number

PRODUCTS = {"沪深300": "IF", "上证50": "IH", "中证500": "IC", "中证1000": "IM"}
MULTIPLIERS = {"IF": 300, "IH": 300, "IC": 200, "IM": 200}
TENORS = ("最近月", "次近月", "第一季月", "第二季月")
MAX_AGE = timedelta(seconds=10)
END_GRACE = timedelta(seconds=6)
VERSION = "futures-l1-window-v2"
DIRECTIONS = ("sell", "buy", "unclassified")


@dataclass(frozen=True)
class Snapshot:
    timestamp: datetime
    last: float
    oi: float
    volume: float
    turnover: float
    bid: float | None
    ask: float | None
    received_at: str | None = None


def top(msg, side, field):
    for name in (side, side + "s"):
        levels = msg.get(name)
        if levels is not None and len(levels):
            return number(levels[0])
    return number(msg.get(field))


def right_index(ticks, timestamp):
    low, high = 0, len(ticks)
    while low < high:
        middle = (low + high) // 2
        if ticks[middle].timestamp <= timestamp:
            low = middle + 1
        else:
            high = middle
    return low


def window_ticks(ticks, start, end):
    a = right_index(ticks, start) - 1
    b = right_index(ticks, end)
    return ticks[max(0, a):b]


def contract_window(ticks, start, end, multiplier):
    """Classify turnover and price signed OI changes at the starting price."""
    ticks = window_ticks(ticks, start, end)
    if len(ticks) < 2:
        return None
    first, last = ticks[0], ticks[-1]
    if not timedelta(0) <= start - first.timestamp <= MAX_AGE or end - last.timestamp > MAX_AGE:
        return None
    sides = {side: {"turnover_cny": 0., "oi_increase_hands": 0., "oi_decrease_hands": 0.}
             for side in DIRECTIONS}
    sell_oi = 0.
    invalid_segments = 0
    for left, right in zip(ticks, ticks[1:]):
        volume = right.volume - left.volume
        cash = right.turnover - left.turnover
        oi = right.oi - left.oi
        if volume < 0 or cash < 0 or abs(oi) > volume or (volume > 0) != (cash > 0):
            return None
        if volume == 0:
            continue
        side = "unclassified"
        average = cash / (volume * multiplier)
        if right.timestamp - left.timestamp > MAX_AGE:
            invalid_segments += 1
        elif left.timestamp >= start and left.bid is not None and left.ask is not None and 0 < left.bid < left.ask:
            if max(average, right.last) <= left.bid + 1e-7:
                side = "sell"
            elif min(average, right.last) >= left.ask - 1e-7:
                side = "buy"
        bucket = sides[side]
        bucket["turnover_cny"] += cash
        bucket["oi_increase_hands"] += max(oi, 0)
        bucket["oi_decrease_hands"] += min(oi, 0)
        if side == "sell":
            sell_oi += max(oi, 0) * average * multiplier
    for bucket in sides.values():
        for change in ("increase", "decrease"):
            bucket[f"oi_{change}_cny"] = bucket[f"oi_{change}_hands"] * first.last * multiplier
        bucket["oi_net_cny"] = bucket["oi_increase_cny"] + bucket["oi_decrease_cny"]
    classified = sides["sell"]["turnover_cny"] + sides["buy"]["turnover_cny"]
    return {
        "start_tick": first.timestamp.isoformat(), "end_tick": last.timestamp.isoformat(),
        "start_oi": first.oi, "end_oi": last.oi, "delta_oi": last.oi - first.oi,
        "reference_price": first.last, "multiplier": multiplier, "directions": sides,
        "oi_increase_cny": sum(r["oi_increase_cny"] for r in sides.values()),
        "oi_decrease_cny": sum(r["oi_decrease_cny"] for r in sides.values()),
        "net_oi_cny": (last.oi - first.oi) * first.last * multiplier,
        "price_return": last.last / first.last - 1,
        "turnover_cny": sum(r["turnover_cny"] for r in sides.values()), "classified_turnover_cny": classified,
        "estimated_sell_cny": sides["sell"]["turnover_cny"] if classified else None,
        "estimated_sell_oi_cny": sell_oi if classified else None,
        "invalid_segments": invalid_segments,
    }


class FuturesEvidence:
    def __init__(self):
        self.products = {}
        self.rows = {}
        self.state = "waiting"

    def prepare(self, api, day, products):
        for product in sorted(products):
            try:
                codes = list(api.futures.get_contracts(product, day))
                if len(set(codes)) != len(TENORS) or any(not c.startswith(product) for c in codes):
                    continue
                self.products[product] = codes
                self.rows.update({code: [] for code in codes})
            except Exception as error:  # optional upstream must not disrupt ETF monitoring
                print(f"Futures contracts unavailable for {product} ({type(error).__name__})", flush=True)
        self.state = "waiting" if self.rows else "contracts_unavailable"

    def add(self, code, timestamp, msg, received_at=None):
        rows = self.rows.get(code)
        if rows is None or (rows and timestamp <= rows[-1].timestamp):
            return
        last, oi, volume, cash = (number(msg.get(k)) for k in ("last", "open_interest", "volume", "total_turnover"))
        if not last or last < 0 or any(v is None or v < 0 for v in (oi, volume, cash)):
            return
        if not oi.is_integer() or not volume.is_integer():
            return
        rows.append(Snapshot(timestamp, last, oi, volume, cash, top(msg, "bid", "b1"), top(msg, "ask", "a1"), received_at))
        if len(rows) % 128 == 0:
            keep = right_index(rows, timestamp - timedelta(minutes=20))
            del rows[:max(0, keep - 1)]
        self.state = "receiving"
        return True

    def observe(self, product, start, end, spot_return=None):
        codes = self.products.get(product, [])
        contracts = []
        for tenor, code in zip(TENORS, codes):
            values = contract_window(self.rows[code], start, end, MULTIPLIERS[product])
            if values is not None and spot_return is not None:
                values["relative_return_bp"] = (values["price_return"] - spot_return) * 10000
            contracts.append({"code": code, "tenor": tenor, "values": values})
        valid = [c["values"] for c in contracts if c["values"] is not None]
        full = len(valid) == len(TENORS)
        net = sum(r["net_oi_cny"] for r in valid) if full else None
        total = sum(r["turnover_cny"] for r in valid)
        classified = sum(r["classified_turnover_cny"] for r in valid)
        directions = None
        if full:
            directions = {side: {field: sum(r["directions"][side][field] for r in valid)
                                 for field in valid[0]["directions"][side]} for side in DIRECTIONS}
        sell = directions["sell"]["turnover_cny"] if full and classified else None
        sell_oi = sum(r["estimated_sell_oi_cny"] or 0 for r in valid) if full and classified else None
        state = "missing_ticks"
        if full:
            if net <= 0:
                state = "no_net_increase"
            elif not classified:
                state = "side_unavailable"
            elif sell_oi > 0:
                state = "sell_side_evidence"
            else:
                state = "increase_without_sell_evidence"
        return {
            "version": VERSION, "product": product, "status": state, "contracts": contracts,
            "covered": len(valid), "expected": len(TENORS), "net_oi_cny": net,
            "directions": directions, "turnover_cny": total if full else None,
            "oi_increase_cny": sum(r["oi_increase_cny"] for r in valid) if full else None,
            "oi_decrease_cny": sum(r["oi_decrease_cny"] for r in valid) if full else None,
            "estimated_sell_cny": sell, "estimated_sell_oi_cny": sell_oi,
            "classified_share": classified / total if full and total else None,
            "start_at": start.isoformat(), "end_at": end.isoformat(),
        }
