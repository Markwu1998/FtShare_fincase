import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { collectSnapshot } from '../scripts/lib/collect.mjs';
import { mergeSnapshot } from '../scripts/lib/merge-snapshot.mjs';
import { validateSeed } from '../scripts/lib/seed.mjs';
import { createReadOnlyService } from '../src/server/read-only.js';
import { BASKET, RQDATA_SLOTS, SLOTS, fundDaily } from '../src/server/model.js';

const universe = JSON.parse(await readFile(new URL('../config/universe.json', import.meta.url), 'utf8'));
const at = (date, time) => Date.parse(date + 'T' + time + ':00+08:00');
const day = '2026-11-13';

test('RQData SZ shares use their own trading date and require NAV confirmation for a reported split', () => {
  const calendar = ['2026-11-11', '2026-11-12'];
  const fund = { symbol: '159915.SZ', inception_date: '2011-09-20', listed_date: '2011-12-09' };
  const shares = calendar.map((trade_date, i) => ({ trade_date, fund_share: i ? 206 : 100, statistics_period: '日' }));
  const navs = calendar.map((nav_date, i) => ({ nav_date, unit_nav: i ? 1 : 2, adjustment_factor: i ? 2 : 1 }));
  const result = fundDaily(fund, shares, navs, calendar, { alignedShares: true, reportedSplits: new Map([[calendar[1], 2]]) });
  assert.equal(result[calendar[1]].shares_record_date, calendar[1]);
  assert.equal(result[calendar[1]].previous_shares_record_date, calendar[0]);
  assert.equal(result[calendar[1]].flow, 6 / 1e8);
  assert.equal(fundDaily(fund, shares, navs, calendar, { alignedShares: true, reportedSplits: new Map([[calendar[1], 3]]) })[calendar[1]].flow, null);
});

test('RQData market history builds on 240 slots without fund data and discards incompatible minute archives', async () => {
  const calendar = ['2026-11-12', day];
  const market = async () => ({
    calendar,
    daily: calendar.flatMap(date => [...BASKET, '000001.SH'].map(symbol => [symbol, at(date, '15:00'), 3, 240])),
    minutes: BASKET.flatMap(symbol => RQDATA_SLOTS.map(slot => [symbol, at(day, slot), 3, 1]))
  });
  const seed = validateSeed(await collectSnapshot({
    config: { flow_start: calendar[0], market_start: calendar[0], minute_days: 1 },
    universe, end: '2026-11-14', market
  }), 'live');
  assert.equal(seed.latest_flow_date, null);
  assert.deepEqual(seed.minute_slots, RQDATA_SLOTS);
  assert.equal(seed.minutes[day].quality, undefined);
  assert.equal(seed.minutes[day].bars.length, 240);
  const previous = {
    ...seed, provider: 'FTShare', minute_slots: SLOTS,
    minutes: { '2026-11-11': { bars: SLOTS.map(() => 0), cumulative: SLOTS.map(() => 0) } },
    live_snapshot: { provider: 'FTShare' }
  };
  const merged = validateSeed(mergeSnapshot(previous, seed), 'live');
  assert.deepEqual(Object.keys(merged.minutes), [day]);
  assert.equal(merged.live_snapshot, undefined);
});

test('the read-only RQData endpoint isolates source cache and gates replay', async () => {
  const service = createReadOnlyService({ provider: 'RQData', minute_slots: RQDATA_SLOTS });
  const value = { provider: 'RQData', slots: RQDATA_SLOTS, replay: true, fetched_at: new Date().toISOString() };
  const FTSHARE_CACHE = { get: async key => {
    assert.equal(key, 'live:rqdata:snapshot');
    return { value };
  } };
  await assert.rejects(service.live({ FTSHARE_CACHE }), { code: 'cache_not_ready' });
  assert.equal((await service.live({ FTSHARE_CACHE, RQDATA_ALLOW_REPLAY: '1' })).provider, 'RQData');
});
