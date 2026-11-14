"""Shared calculations for daily archives and minute snapshots."""

from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN
import json
from math import isfinite
from pathlib import Path

from .provider import CHINA

CONSTANTS = json.loads(Path(__file__).with_name("constants.json").read_text(encoding="utf-8"))
BASKET = CONSTANTS["basket"]
CATEGORIES = CONSTANTS["categories"]
COLORS = CONSTANTS["colors"]
FORMULA = CONSTANTS["formula"]
FLOW_POLICY = CONSTANTS["flow_policy"]
INDEX = "000001.SH"
SLOTS = [f"{m // 60:02}:{m % 60:02}" for m in (*range(571, 691), *range(781, 901))]
DEFAULT_STATUS = {"mode": "background", "timezone": "Asia/Shanghai", "daily": ["08:00", "12:30", "18:00"],
                  "intraday_seconds": 60, "visitor_collection": False}


def stamp(ms):
    return datetime.fromtimestamp(ms / 1000, CHINA)


def eligible(fund):
    return max(fund["inception_date"], fund["listed_date"])


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def cumulative(values):
    total = 0
    result = []
    for value in values:
        total = total + value if total is not None and finite(value) else None
        result.append(total)
    return result


def minute_basket(rows):
    by_slot = {}
    for symbol, ms, _, amount in rows:
        if symbol in BASKET and finite(amount) and amount >= 0:
            by_slot.setdefault(stamp(ms).strftime("%H:%M"), {})[symbol] = amount
    coverage = [len(by_slot.get(slot, {})) for slot in SLOTS]
    amounts = [sum(by_slot[slot][symbol] for symbol in BASKET) / 1e8 if count == len(BASKET) else None
               for slot, count in zip(SLOTS, coverage)]
    return amounts, coverage, cumulative(amounts)


def fixed(value, places):
    return int(Decimal(str(value)).scaleb(places).to_integral_value(rounding=ROUND_DOWN))


def fund_flows(fund, records, calendar):
    evidence = fund.get("flow_evidence", {})
    shares = dict(records["shares"])
    navs = {row["nav_date"]: (float(row["unit_nav"]), row.get("adjustment_factor"), row) for row in evidence.get("navs", [])}
    navs.update({day: (value, factor, {}) for day, value, factor in records["navs"]})
    reported = dict(records["splits"])
    verified = {row["date"]: row for row in evidence.get("splits", [])}
    result = {}
    for previous, day in zip(calendar, calendar[1:]):
        if day < eligible(fund):
            continue
        share, prior = shares.get(day), shares.get(previous)
        nav, factor, nav_meta = navs.get(day, (None, None, {}))
        prior_nav, prior_factor, _ = navs.get(previous, (None, None, {}))
        baseline = evidence.get("listing_baseline") or {}
        used = prior is None and fund["symbol"].endswith(".SH") and fund["listed_date"] == day and (
            baseline.get("date") == previous and baseline.get("subscription_start_date", "") >= day
            and baseline.get("redemption_start_date", "") >= day and baseline.get("fund_share") is not None
        )
        if used:
            prior = float(baseline["fund_share"])
        event = verified.get(day, {})
        ratio = float(event.get("ratio", 1))
        factor_ratio = float(factor) / float(prior_factor) if factor and prior_factor else 0
        tolerance = 5e-6 if (event.get("source") or {}).get("verified_official_event") is True else 1e-8
        if not ratio or abs(factor_ratio / ratio - 1) >= tolerance:
            ratio = 1
        candidate = reported.get(day)
        rq_split = candidate and candidate > 0 and abs(factor_ratio / candidate - 1) < 5e-4
        if rq_split and ratio == 1:
            ratio = candidate
        issues = []
        if share is None:
            issues.append("share_missing")
        if prior is None:
            issues.append("listing_baseline_missing" if fund["symbol"].endswith(".SH") and fund["listed_date"] == day
                          else "previous_share_missing")
        if nav is None or not nav > 0:
            issues.append("nav_missing")
        flow = None
        if not issues:
            share_ratio = share / (prior * ratio) if prior else 1
            nav_ratio = nav / (prior_nav / ratio) if prior_nav else None
            if (share_ratio > 1.5 or share_ratio < .67) and nav_ratio and abs(share_ratio * nav_ratio - 1) < .08:
                issues.append("share_change_review")
            else:
                base = fixed(prior, 8) * fixed(ratio, 12) // 10**12
                flow = (fixed(share, 8) - base) * fixed(nav, 8) / 10**24
        row = {"flow": flow, "shares": share, "nav": nav, "shares_record_date": day,
               "previous_shares_record_date": previous, "publish_date": nav_meta.get("publish_date")}
        if issues:
            row["issues"] = issues
        if used:
            row["baseline_source"] = "verified_prelisting_shares"
        if ratio != 1:
            row["split_ratio"] = ratio
        if rq_split:
            row["split_source"] = "RQData fund.get_split and NAV factor"
        if nav_meta.get("source"):
            row["nav_source"] = nav_meta["source"]
        result[day] = row
    return result


def iso_utc(now):
    return now.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def trading_minute(now):
    hm = now.strftime("%H:%M")
    return now.weekday() < 5 and ("09:30" <= hm <= "11:30" or "13:00" <= hm <= "15:02")


def before(day, days):
    return (datetime.fromisoformat(day) - timedelta(days=days)).date().isoformat()
