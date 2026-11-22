"""Import daily ETF direction research into the read-only dashboard archive."""
import argparse
import csv
from datetime import date
from pathlib import Path

from .collect import ROOT, publish
from .model import BASKET, finite

DEFAULT_PATH = ROOT / "data/private/etf-flow-comparison.json"
METHOD = "rqdata-l1-midpoint-band-v1"


def validate_archive(value):
    if value["schema_version"] != 1 or value["unit"] != "CNY_1e8" or value["method"] != METHOD:
        raise ValueError("Incompatible ETF flow comparison archive")
    seen = set()
    dates = set()
    if not value["rows"]:
        raise ValueError("ETF flow comparison is empty")
    for row in value["rows"]:
        day, symbol = row["day"], row["symbol"]
        key = day, symbol
        if date.fromisoformat(day).isoformat() != day or symbol not in BASKET or key in seen:
            raise ValueError(f"Invalid or duplicate ETF-day: {day} {symbol}")
        seen.add(key)
        dates.add(day)
        buy, sell, flow, gross = (row[field] for field in ("active_buy_yi", "active_sell_yi", "net_flow_yi", "gross_yi"))
        if (not finite(gross) or gross < 0 or (buy is None and (sell is not None or gross == 0))
                or (buy is not None and (not finite(buy) or not finite(sell)
                                         or min(buy, sell) < 0 or buy + sell > gross + 1e-8))
                or (flow is not None and not finite(flow)) or not isinstance(row["flow_issues"], str)):
            raise ValueError(f"Invalid comparison amounts: {day} {symbol}")
    if len(seen) != len(dates) * len(BASKET):
        raise ValueError("Every covered date must contain all configured core ETFs")
    return value


def import_rows(source):
    with source.open(encoding="utf-8-sig", newline="") as stream:
        rows = [{"day": row["day"], "symbol": row["symbol"], "flow_issues": row["flow_issues"],
                 **{field: float(row[field]) if row[field] else None
                    for field in ("gross_yi", "active_buy_yi", "active_sell_yi", "net_flow_yi")}}
                for row in csv.DictReader(stream)]
    rows.sort(key=lambda row: (row["day"], row["symbol"]))
    return validate_archive({"schema_version": 1, "method": METHOD, "unit": "CNY_1e8", "rows": rows})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="ETF-day CSV from the RQData midpoint-band replay")
    args = parser.parse_args()
    try:
        value = import_rows(args.csv)
        publish(DEFAULT_PATH, value)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"ETF flow comparison import failed: {error}\n")
    print(f"Imported {len(value['rows'])} ETF-days into {DEFAULT_PATH}")


if __name__ == "__main__":
    main()
