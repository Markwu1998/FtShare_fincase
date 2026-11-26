"""Generate an independent DDB monitoring bundle; installs no scheduler."""
import argparse
from datetime import date
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from etf_backend.collect import publish, read
from etf_backend.ddb_replay import connect
from etf_backend.preopen import load_preopen, prepare, resolve_path


def main() -> int:
    """Prepare before the open, or validate an existing bundle without a connection."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--day', required=True, help='Explicit target trading day, YYYY-MM-DD')
    parser.add_argument('--config', type=Path, default=ROOT / 'config/monitor-preopen.json')
    parser.add_argument('--output', type=Path, help='Override the generated monitor-preopen.json path')
    parser.add_argument('--check', action='store_true', help='Validate the existing output offline')
    args = parser.parse_args()
    date.fromisoformat(args.day)
    config = read(args.config)
    output = args.output or resolve_path(config['output'], ROOT, args.day)
    if args.check:
        rules = read(resolve_path(config['burst_config_file'], ROOT, args.day))
        symbols = rules.get('symbols') or read(resolve_path(config['basket_file'], ROOT, args.day))['symbols']
        existing = read(output)
        value = load_preopen(output, args.day, existing['as_of'], symbols, rules.get('amount_window_quantile', .75))
    else:
        session = connect()
        try:
            value = prepare(session, args.day, config, progress=lambda day: print('prepared history', day, flush=True))
            publish(output, value)
        finally:
            session.close()
    print('output:', output.resolve())
    print('day:', value['day'], 'as_of:', value['as_of'], 'provider:', value['provider'])
    for symbol, coverage in value['coverage'].items():
        print(symbol, 'formal_ready=', coverage['formal_ready'], 'daily_samples=', coverage['daily_samples'],
              'minute_windows=', str(coverage['minute_windows']) + '/' + str(coverage['expected_minute_windows']),
              'tick_slots=', coverage['tick_slots'], 'previous_missing=', coverage['missing_previous_fields'])
    complete = all(c['formal_ready'] for c in value['coverage'].values())
    print('formal_baseline:', 'ready' if complete else 'partial; inspect per-symbol coverage')
    return 0 if complete else 2


if __name__ == '__main__':
    sys.exit(main())
