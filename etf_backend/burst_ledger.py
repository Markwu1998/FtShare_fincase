"""Append-only local audit trail. Raw market data stays outside public packages."""
from hashlib import sha256
from contextlib import closing
import json
from pathlib import Path
import sqlite3


class BurstLedger:
    def __init__(self, path, identity):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.pending = path, []
        with closing(sqlite3.connect(path, timeout=5)) as db, db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS records '
                       '(seq INTEGER PRIMARY KEY, key TEXT UNIQUE, kind TEXT, symbol TEXT, '
                       'market_at TEXT, received_at TEXT, payload TEXT)')
        self.identity = identity
        self.append('session', '', identity['day'], None, identity)
        self.commit()

    def append(self, kind, symbol, market_at, received_at, value):
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
        stable = {k: v for k, v in value.items() if k != 'detected_at'} if kind == 'formal' else value
        key = sha256(json.dumps([self.identity, kind, symbol, market_at, stable, received_at if kind == 'tick' else None],
                                sort_keys=True).encode()).hexdigest()
        self.pending.append((key, kind, symbol, market_at, received_at, payload))

    def commit(self):
        if not self.pending:
            return
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.executemany('INSERT OR IGNORE INTO records '
                           '(key,kind,symbol,market_at,received_at,payload) VALUES (?,?,?,?,?,?)', self.pending)
        self.pending.clear()

    def close(self):
        self.commit()


def read_journal(path, after=0, limit=200, symbol=None, kind=None):
    path = Path(path)
    if not path.is_file():
        return []
    # Read-only connections never create a missing database or mutate the writer.
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
        sql = 'SELECT seq,kind,symbol,market_at,received_at,payload FROM records WHERE seq>?'
        params = [after]
        for column, value in [('symbol', symbol), ('kind', kind)]:
            if value:
                sql += ' AND ' + column + '=?'
                params.append(value)
        rows = db.execute(sql + ' ORDER BY seq LIMIT ?', params + [limit]).fetchall()
    return [dict(zip(('seq', 'kind', 'symbol', 'market_at', 'received_at', 'value'),
                     (*row[:5], json.loads(row[5])))) for row in rows]
