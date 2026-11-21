"""Summarize first confirmations by ETF/event, not inferred trader identity."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from etf_backend.burst_ledger import read_journal


def summarize(path):
    events, after = {}, 0
    while True:
        rows = read_journal(path, after, 1000, kind='formal')
        if not rows:
            break
        for row in rows:
            value = row['value']
            if value['signal']['level'] < 2:
                continue
            item = {'event_id': value['event_id'], 'symbol': row['symbol'], 'market_at': row['market_at'],
                    'received_at': row['received_at'], 'recovered': value['recovered']}
            previous = events.get(item['event_id'])
            if previous is None or item['market_at'] < previous['market_at']:
                events[item['event_id']] = item
        after = rows[-1]['seq']
    first = sorted(events.values(), key=lambda row: (row['market_at'], row['symbol']))
    bins = Counter(row['market_at'][11:14] + str(int(row['market_at'][14:16]) // 10 * 10).zfill(2) for row in first)
    return {'unit': 'ETF event', 'confirmed_events': len(first), 'first_confirmation_10m_bins': dict(sorted(bins.items())),
            'recovered_events': sum(row['recovered'] for row in first), 'first_confirmations': first,
            'note': '异常集中交易筛查，不能据此确认交易主体；同一行动可能触发多只ETF。'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('journal', type=Path)
    args = parser.parse_args()
    if not args.journal.is_file():
        parser.error('Journal does not exist')
    print(json.dumps(summarize(args.journal), ensure_ascii=False, indent=2))
