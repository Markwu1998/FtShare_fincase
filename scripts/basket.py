"""Maintain the 20-ETF core basket from one config file.

Usage:
    python scripts/basket.py list
    python scripts/basket.py set 510050.SH 510100.SH ... 20 symbols
    python scripts/basket.py add 510330.SH --remove 159633.SZ   (not supported; use set)
    python scripts/basket.py add 510330.SH
    python scripts/basket.py remove 159633.SZ
    python scripts/basket.py sync

`config/basket.json` is the single source. Every mutation validates the list,
then rewrites `src/server/model.js` and the `is_core_basket` flags in
`config/universe.json`. `config/etf-burst.json` follows the basket unless it
defines its own `symbols` override.
"""
import argparse
from datetime import date
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
BASKET_FILE = ROOT / "config/basket.json"
UNIVERSE_FILE = ROOT / "config/universe.json"
MODEL_FILE = ROOT / "src/server/model.js"
BURST_FILE = ROOT / "config/etf-burst.json"
COUNT = 20


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def universe():
    return read(UNIVERSE_FILE)["funds"]


def validate(symbols):
    if len(symbols) != COUNT:
        raise SystemExit(f"Basket must contain exactly {COUNT} symbols, got {len(symbols)}")
    if len(set(symbols)) != len(symbols):
        raise SystemExit("Basket contains duplicate symbols")
    known = {fund["symbol"] for fund in universe()}
    unknown = [symbol for symbol in symbols if symbol not in known]
    if unknown:
        raise SystemExit("Unknown symbols (not in config/universe.json): " + ", ".join(unknown))


def sync(symbols):
    validate(symbols)
    write_json(BASKET_FILE, {"symbols": symbols, "updated_at": date.today().isoformat()})

    funds = universe()
    for fund in funds:
        fund["is_core_basket"] = fund["symbol"] in symbols
    write_json(UNIVERSE_FILE, read(UNIVERSE_FILE) | {"funds": funds})

    model = MODEL_FILE.read_text(encoding="utf-8")
    replacement = "export const BASKET = [" + ",".join("'" + s + "'" for s in symbols) + "];"
    model, count = re.subn(r"export const BASKET = \[[^\]]*\];", replacement, model)
    if count != 1:
        raise SystemExit("Could not locate BASKET in src/server/model.js")
    MODEL_FILE.write_text(model, encoding="utf-8")

    if BURST_FILE.exists():
        burst = read(BURST_FILE)
        if "symbols" in burst:
            burst["symbols"] = symbols
            write_json(BURST_FILE, burst)


def show(symbols):
    funds = {fund["symbol"]: fund for fund in universe()}
    for index, symbol in enumerate(symbols, 1):
        fund = funds.get(symbol, {})
        print(f"{index:2d}. {symbol}  {fund.get('bucket', '?'):8s} {fund.get('name', '?')}")


def next_steps():
    print()
    print("Next steps (basket changes affect daily net-flow history):")
    print("  1. Back up and rebuild the full history (NOT --recent):")
    print("     Move-Item data\\private\\rqdata-seed.json .local\\rqdata-seed-old.json")
    print("     & .\\scripts\\python-backend.ps1 history")
    print("  2. Rebuild demo data for the Node path: npm run demo:generate")
    print("  3. Rebuild today's live cache: & .\\scripts\\python-backend.ps1 -Mode live -Arguments @('--close')")
    print("  4. Restart the web and collector processes (BASKET is loaded at startup).")
    print("  5. Verify: .\\status.cmd")


def main():
    parser = argparse.ArgumentParser(description="Maintain the 20-ETF core basket")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="Show the current basket")
    sub.add_parser("sync", help="Re-apply the basket config to generated files")
    for name in ("add", "remove", "set"):
        item = sub.add_parser(name, help=f"{name} symbols (add/remove keep 20 total)")
        item.add_argument("symbols", nargs="+")
    args = parser.parse_args()

    current = read(BASKET_FILE)["symbols"] if BASKET_FILE.exists() else read(UNIVERSE_FILE)["funds"]
    current = [fund["symbol"] for fund in current if fund.get("is_core_basket")] if isinstance(current[0], dict) else current

    if args.command == "list":
        show(current)
        return

    if args.command == "sync":
        sync(current)
        print(f"Synced {len(current)} symbols.")
        show(current)
        return

    if args.command == "set":
        symbols = args.symbols
    elif args.command == "add":
        symbols = current + [symbol for symbol in args.symbols if symbol not in current]
    else:
        symbols = [symbol for symbol in current if symbol not in set(args.symbols)]
        missing = [symbol for symbol in args.symbols if symbol not in current]
        if missing:
            raise SystemExit("Not in current basket: " + ", ".join(missing))

    validate(symbols)
    sync(symbols)
    added = [symbol for symbol in symbols if symbol not in current]
    removed = [symbol for symbol in current if symbol not in symbols]
    print(f"Basket now has {len(symbols)} symbols.")
    if added:
        print("Added:   " + ", ".join(added))
    if removed:
        print("Removed: " + ", ".join(removed))
    show(symbols)
    next_steps()


if __name__ == "__main__":
    main()
