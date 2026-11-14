import { BASKET, CATEGORIES, COLORS, FORMULA, FLOW_POLICY, RQDATA_SLOTS, SLOTS, chinaDate, dateISO, dateInt, fundDaily, aggregateFunds, aggregateMinute, cumulative, mapLimit, split } from '../../src/server/model.js';
import { fundStartDate } from '../../src/assets/flow-math.js';

export function mergeShares(primary, extra = []) {
  const byDate = new Map(primary.filter(r => r.statistics_period === '日').map(r => [dateISO(r.trade_date), r]));
  for (const row of extra) {
    const date = dateISO(row.trade_date), prior = byDate.get(date);
    if (prior && Number(prior.fund_share) !== Number(row.fund_share)) throw new Error('Conflicting share sources: ' + row.symbol + ' ' + date);
    if (!prior) byDate.set(date, row);
  }
  return [...byDate.values()];
}
export async function collectSnapshot({ config, universe, client, market, end, publicShares = [], recoveries = {}, builtAt = new Date().toISOString(), progress = () => {} }) {
  const funds = universe.funds.map(f => ({ ...f, ...(recoveries[f.symbol] ? { flow_evidence: recoveries[f.symbol] } : {}) }));
  if (funds.length < 20 || !BASKET.every(s => funds.some(f => f.symbol === s && f.is_core_basket))) throw new Error('Universe must include the 20 core ETFs');
  for (const f of funds) if (!f.inception_date || !f.listed_date || !CATEGORIES.includes(f.bucket)) throw new Error('Incomplete universe entry: ' + f.symbol);
  const indexStart = [config.flow_start, config.market_start].sort()[0];
  const pad = new Date(Date.parse((market ? indexStart : config.flow_start) + 'T00:00:00Z') - 14 * 86400000).toISOString().slice(0, 10);
  const rq = market && await market({ kind: 'history', symbols: [...BASKET, '000001.SH'], start: pad, end, market_start: config.market_start, minute_days: config.minute_days });
  const r = rq || await client.call('ft_v1_time_trading_calendar', { market: 'cn', start_date: dateInt(pad), end_date: dateInt(end) });
  const calendar = rq ? rq.calendar : (Array.isArray(r.data) ? r.data[0] : r.data).trade_dates.map(dateISO).sort();
  const slots = rq ? RQDATA_SLOTS : SLOTS;
  if (calendar.length < 2) throw new Error('Not enough trading days');
  const supplementary = new Map();
  for (const row of publicShares) { if (!supplementary.has(row.symbol)) supplementary.set(row.symbol, []); supplementary.get(row.symbol).push(row); }
  const raw = {}, concurrency = Math.max(1, Math.min(4, config.concurrency || 2)); let completed = 0;
  const rqFunds = rq && config.fund_history && (await market({ kind: 'fund_history', symbols: funds.map(fund => fund.symbol), start: pad, end })).funds;
  if (rqFunds) for (const fund of funds) {
    const rows = rqFunds[fund.symbol];
    const shares = rows.shares.map(([trade_date, fund_share]) => ({ trade_date, fund_share, statistics_period: '日' }));
    const navs = rows.navs.map(([nav_date, unit_nav, adjustment_factor]) => ({ nav_date, unit_nav, adjustment_factor }));
    raw[fund.symbol] = fundDaily(fund, shares, navs, calendar, { alignedShares: true, reportedSplits: new Map(rows.splits) });
  }
  if (!rq) await mapLimit(funds, concurrency, async fund => {
    const args = { etf_code: fund.symbol, start_date: dateInt(pad), end_date: dateInt(end) };
    const shares = mergeShares(await client.pages('ft_v2_etf_share', { ...args, stati_perd: '日' }), supplementary.get(fund.symbol));
    const navs = await client.pages('ft_v2_etf_net_value', args);
    raw[fund.symbol] = fundDaily(fund, shares, navs, calendar);
    if (++completed % 25 === 0) progress('Fund history ' + completed + '/' + funds.length);
  });
  const daily = aggregateFunds(funds, raw, calendar), broad = daily.broad;
  const offset = broad.dates.findIndex(d => d >= config.flow_start);
  if (offset < 0) throw new Error('No trading dates in selected flow interval');
  broad.dates = broad.dates.slice(offset); broad.total = broad.total.slice(offset);
  broad.issues = Object.fromEntries(Object.entries(broad.issues).filter(([d]) => d >= config.flow_start));
  for (const c of CATEGORIES) { broad.series[c] = broad.series[c].slice(offset); for (const key of ['valid', 'expected']) broad.coverage[c][key] = broad.coverage[c][key].slice(offset); }
  const flow = new Map(daily.dates.map((d, i) => [d, { value: daily.basket.netflow[i], valid: daily.basket.coverage[i], expected: daily.basket.expected[i] }]));
  const volume = {}, index = {}, fundsBySymbol = Object.fromEntries(funds.map(f => [f.symbol, f]));
  if (rq) for (const [symbol, ts, close, turnover] of rq.daily) {
    const date = chinaDate(ts);
    if (symbol === '000001.SH') { if (Number.isFinite(close)) index[date] = close; }
    else if (Number.isFinite(turnover) && turnover >= 0) (volume[date] ||= {})[symbol] = turnover / 1e8;
  }
  if (!rq) for (let year = Number(config.market_start.slice(0, 4)); year <= Number(end.slice(0, 4)); year++) {
    const start = year === Number(config.market_start.slice(0, 4)) ? config.market_start : year + '-01-01';
    const until = year === Number(end.slice(0, 4)) ? end : year + '-12-31';
    const args = { interval_unit: 'Day', adjust_kind: 'none', since_ts_millis: Date.parse(start + 'T00:00:00+08:00'), until_ts_millis: Date.parse(until + 'T23:59:59+08:00'), limit: 400 };
    await mapLimit(split(BASKET, 4), concurrency, async symbols => {
      const result = await client.call('ft_v2_etf_candlesticks_batch', { ...args, symbols });
      for (const row of result.data) { const date = chinaDate(row.ts_millis); (volume[date] ||= {})[row.symbol] = Number(row.turnover) / 1e8; }
    });
    progress('Daily market ' + year);
  }
  if (!rq) for (let year = Number(indexStart.slice(0, 4)); year <= Number(end.slice(0, 4)); year++) {
    const start = year === Number(indexStart.slice(0, 4)) ? indexStart : year + '-01-01';
    const until = year === Number(end.slice(0, 4)) ? end : year + '-12-31';
    const result = await client.call('ft_v1_index_candlesticks', { symbol: '000001.SH', interval_unit: 'Day', adjust_kind: 'none', since_ts_millis: Date.parse(start + 'T00:00:00+08:00'), until_ts_millis: Date.parse(until + 'T23:59:59+08:00'), limit: 400 });
    for (const row of result.data) index[chinaDate(row.ts_millis)] = Number(row.close);
  }
  const dates = calendar.filter(d => d >= indexStart), amount = dates.map(d => {
    if (d < config.market_start) return null;
    const symbols = BASKET.filter(s => d >= fundStartDate(fundsBySymbol[s]));
    return symbols.length && symbols.every(s => volume[d]?.[s] !== undefined) ? symbols.reduce((sum, s) => sum + volume[d][s], 0) : null;
  });
  const minuteDays = calendar.filter(d => d >= config.market_start && d < end).slice(-config.minute_days), minutes = {};
  const rqMinutes = new Map();
  if (rq) for (const [symbol, ts_millis, close, turnover] of rq.minutes) {
    const date = chinaDate(ts_millis);
    if (!rqMinutes.has(date)) rqMinutes.set(date, []);
    rqMinutes.get(date).push({ symbol, ts_millis, close, turnover });
  }
  if (config.minute_days > 0) await mapLimit(minuteDays, concurrency, async date => {
    const symbols = BASKET.filter(s => date >= fundStartDate(fundsBySymbol[s])), rows = rqMinutes.get(date) || [];
    if (symbols.length !== 20) return;
    if (!rq) for (const group of split(symbols, 4)) {
      const result = await client.call('ft_v2_etf_minutes_batch', { symbols: group, interval_value: 1, adjust_kind: 'none', since_ts_millis: Date.parse(date + 'T09:00:00+08:00'), until_ts_millis: Date.parse(date + 'T15:30:00+08:00'), limit: 1000 });
      rows.push(...result.data);
    }
    const m = aggregateMinute(rows, symbols, date, slots), total = m.bars.every(v => v !== null) ? m.bars.reduce((s, v) => s + v, 0) : null;
    const expected = symbols.every(s => volume[date]?.[s] !== undefined) ? symbols.reduce((s, symbol) => s + volume[date][symbol], 0) : null;
    minutes[date] = { bars: m.bars, cumulative: cumulative(m.bars), ...(total === null || expected === null || Math.abs(total - expected) > 1e-6 ? { quality: 'incomplete' } : {}) };
  });
  const latest = broad.dates.filter(d => Number.isFinite(flow.get(d)?.value)).at(-1) ?? null;
  if ((!rq || rqFunds) && !latest) throw new Error('No complete core-basket flow day. Check share/NAV permissions and date coverage; no snapshot was replaced.');
  if (rq && (!amount.some(Number.isFinite) || !Object.keys(index).length ||
      (config.minute_days > 0 && !Object.values(minutes).some(m => !m.quality)))) throw new Error('RQData history lacks a usable market sample');
  return { schema: 1, provider: rq ? 'RQData' : publicShares.length ? 'FTShare+PublicSources' : 'FTShare', sources: rq ? ['RQData'] : publicShares.length ? ['FTShare', 'User-supplied public share supplement'] : ['FTShare'], built_at: builtAt,
    formula: FORMULA, flow_policy: FLOW_POLICY, universe_policy: { policy: universe.policy, eligibility: 'max(inception_date, listed_date)' }, universe: funds, categories: CATEGORIES, colors: COLORS, calendar,
    history: { dates, amount, idx: dates.map(d => index[d] ?? null), netflow: dates.map(d => d < config.market_start ? null : flow.get(d)?.value ?? null), flow_coverage: dates.map(d => flow.get(d)?.valid ?? 0), flow_expected: dates.map(d => flow.get(d)?.expected ?? 20) },
    broad, minutes, core_fund_daily: latest ? { date: latest, rows: funds.filter(f => f.is_core_basket).map(f => ({ symbol: f.symbol, ...raw[f.symbol][latest] })), captured_at: builtAt } : null, latest_flow_date: latest, latest_market_date: Object.keys(index).sort().at(-1) || null, minute_slots: slots,
    source_audit: { provider: rq ? 'RQData' : 'FTShare', ...(rqFunds ? { fund_provider: 'RQData' } : {}), ...client?.stats?.(), missing_days: broad.dates.filter((d, i) => d <= latest && broad.total[i] === null).length } };
}
