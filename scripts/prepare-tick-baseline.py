"""Prepare prior-day-only Tick references; does not install a scheduled task."""
import argparse
import contextlib
from datetime import date, timedelta
import io
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from etf_backend.collect import publish, read
from etf_backend.model import BASKET
from etf_backend.tick_factors import build_references, FACTOR_VERSION


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--day', required=True, help='Target monitoring/replay date, YYYY-MM-DD')
    parser.add_argument('--symbols', nargs='+')
    parser.add_argument('--input-dir', type=Path, help='Existing research data directory; fully offline')
    parser.add_argument('--out', type=Path, default=ROOT / os.environ.get('ETF_BURST_DIR', 'data/private/etf-burst') / 'tick-baselines')
    parser.add_argument('--config', type=Path, default=ROOT / os.environ.get('ETF_BURST_CONFIG', 'config/etf-burst.json'))
    args = parser.parse_args()
    target = date.fromisoformat(args.day)
    config = read(args.config)
    q = config.get('amount_window_quantile', .75)
    symbols = args.symbols or config.get('symbols') or BASKET
    import pandas as pd
    if args.input_dir:
        calendar = [d for d in read(args.input_dir / 'calendar.json') if d < args.day][-60:]
        api = None
    else:
        import rqdatac as api
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            api.init(connect_timeout=8, timeout=90)
        calendar = [str(d) for d in api.get_trading_dates(str(target - timedelta(days=130)),
                                                        str(target - timedelta(days=1)))][-60:]
    if len(calendar) != 60:
        raise ValueError('Need 60 prior trading dates')
    refs = {}
    allowed = {fund['symbol'] for fund in read(ROOT / 'config/universe.json')['funds']}
    for symbol in symbols:
        if symbol not in allowed:
            raise ValueError('Unknown ETF: ' + symbol)
        if args.input_dir:
            import numpy as np
            with np.load(args.input_dir / 'raw/tick' / (symbol + '.npz'), allow_pickle=False) as raw:
                frame = pd.DataFrame({'total_turnover': raw['total_turnover']}, index=pd.to_datetime(raw['ts']))
        else:
            code = symbol.replace('.SH', '.XSHG').replace('.SZ', '.XSHE')
            instrument = api.instruments(code)
            if instrument is None or instrument.order_book_id != code:
                raise ValueError('Unverified ETF: ' + symbol)
            frame = api.get_price(code, start_date=calendar[0], end_date=calendar[-1], frequency='tick',
                                  fields=['total_turnover'], adjust_type='none')
            if frame is None:
                raise ValueError('Empty Tick response: ' + symbol)
            frame.index = pd.to_datetime(frame.index.get_level_values(-1))
        refs[symbol] = build_references(frame, calendar, q)
        print('prepared', symbol, flush=True)
    publish(args.out / (args.day + '.json'), {'version': FACTOR_VERSION, 'day': args.day,
            'as_of': calendar[-1], 'quantile': q, 'calendar': calendar, 'references': refs})


if __name__ == '__main__':
    main()
