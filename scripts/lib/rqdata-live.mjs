import { BASKET, FORMULA, RQDATA_SLOTS, aggregateMinute, chinaTime, cumulative } from '../../src/server/model.js';
import { rqdataMarket } from './rqdata-market.mjs';

const INDEX = '000001.SH';
const tradingSlots = new Set(RQDATA_SLOTS);
const china = new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'Asia/Shanghai', dateStyle: 'short', timeStyle: 'medium'
});

export async function rqdataLive(universe, { replay } = {}) {
  const now = new Date(), asOf = replay ? replay + ' 15:05:00' : china.format(now);
  const date = asOf.slice(0, 10);
  const request = { kind: replay ? 'replay' : 'live', as_of: asOf.replace(' ', 'T'), symbols: [...BASKET, INDEX] };
  const result = await rqdataMarket(request);
  if (!result.calendar.includes(date)) return null;
  const { minutes, daily, previous } = result;
  const rows = minutes.filter(([symbol]) => symbol !== INDEX).map(([symbol, ts_millis, close, turnover]) => ({ symbol, ts_millis, close, turnover }));
  if (rows.some(row => row.turnover > 0 && !tradingSlots.has(chinaTime(row.ts_millis)))) {
    throw Object.assign(new Error('RQData returned turnover outside the configured minute axis'), { code: 'minute_axis_mismatch' });
  }
  const aggregate = aggregateMinute(rows, BASKET, date, RQDATA_SLOTS);
  const closes = new Map(minutes.filter(([symbol]) => symbol === INDEX).map(([, ts, close]) => [chinaTime(ts), close]));
  const quotes = new Map(daily.map(([symbol, ts, close, amount]) => [symbol, { ts, close, amount }]));
  const prev = new Map(previous.map(([symbol, , close]) => [symbol, close]));
  const table = BASKET.map(symbol => {
    const q = quotes.get(symbol), p = prev.get(symbol);
    return {
      symbol, code: symbol.slice(0, 6), name: universe.find(f => f.symbol === symbol)?.name || symbol,
      price: q?.close ?? null, amount: Number.isFinite(q?.amount) && q.amount >= 0 ? q.amount / 1e8 : null,
      pct: Number.isFinite(q?.close) && p > 0 ? (q.close / p - 1) * 100 : null
    };
  });
  const sums = cumulative(aggregate.bars);
  const complete = RQDATA_SLOTS.findLastIndex((slot, i) => Number.isFinite(sums[i]) && Number.isFinite(closes.get(slot)));
  if (complete < 0 || !table.every(row => Number.isFinite(row.amount) && Number.isFinite(row.price)) || !Number.isFinite(prev.get(INDEX))) return null;
  const minuteEnd = Date.parse(date + 'T' + RQDATA_SLOTS[complete] + ':00+08:00');
  const observedAt = replay ? minuteEnd : Math.min(minuteEnd, ...BASKET.map(symbol => quotes.get(symbol).ts));
  return {
    provider: 'RQData', formula: FORMULA, warnings: [], fetched_at: new Date(observedAt).toISOString(), date,
    ...aggregate, closes: RQDATA_SLOTS.map(slot => closes.get(slot) ?? null), pre_close: prev.get(INDEX),
    cumulative: sums, table, amount: table.reduce((sum, row) => sum + row.amount, 0),
    last_complete_minute: RQDATA_SLOTS[complete], expected_flow_date: result.calendar.at(-2),
    trading_dates: result.calendar, daily_batches: 0, ...(replay ? { replay: true } : {})
  };
}
