import test from 'node:test';
import assert from 'node:assert/strict';
import { burstAccounting, visibleBurstSignals } from '../src/assets/radar-math.js';

const burst = {date: '2026-11-20', symbols: ['a'], accounting: {minutes: [
  ['a','09:31',1e8,2e7,'09:35'], ['a','09:32',2e8,4e7,'09:36'],
  ['a','09:31',1e8,2e7,'09:36']]}};
test('accounting filters by confirmation time and deduplicates overlapping windows', () => {
  const seed = {core_fund_daily: {date: burst.date, rows: [{symbol:'a',flow:6}]}};
  assert.equal(burstAccounting(seed, burst, '09:35').gross, 1);
  assert.deepEqual(burstAccounting(seed, burst), {gross:3,excess:2.4,flow:6,ratio:2,minutes:2});
  assert.equal(burstAccounting(seed, burst, '09:30').ratio, null);
});
test('missing data and another attribution date cannot become zero or same-day netflow', () => {
  assert.equal(burstAccounting({}, burst).flow, null);
  assert.equal(burstAccounting({}, {}), null);
});
test('day replay displays only complete same-day flows at the post-close cutoff', () => {
  const replay = {...burst, replay:true, daily_flow:{date:burst.date, unit:'CNY', by_symbol:{a:{flow_cny:-6e8}}}};
  const seed = {core_fund_daily:{date:burst.date, rows:[{symbol:'a', flow:99}]}};
  assert.equal(burstAccounting(seed, replay, '14:59').flow, null);
  assert.equal(burstAccounting(seed, replay, '14:59').ratio, null);
  assert.equal(burstAccounting(seed, replay).flow, -6);
  assert.equal(burstAccounting(seed, replay).ratio, -2);
  assert.equal(burstAccounting(seed, {...replay, symbols:['a','b']}).flow, null);
  assert.equal(burstAccounting({}, {...replay, daily_flow:{...replay.daily_flow, date:'2026-11-19'}}).flow, null);
});
test('reception-time replay cannot see late confirmation before it arrived', () => {
  const replay = {...burst, replay:true, accounting:{minutes:[['a','09:31',1e8,0,'09:35','2026-11-20T09:36:01+08:00']]}};
  assert.equal(burstAccounting({}, replay, '09:36').gross, 0);
  assert.equal(burstAccounting({}, replay, '09:37').gross, 1);
});
test('late formal confirmation is hidden until its recorded reception time', () => {
  const event = {updates: [{end:'09:35', received_at:'2026-11-20T09:36:01+08:00'}]};
  assert.equal(visibleBurstSignals(event, '2026-11-20', '09:36').length, 0);
  assert.equal(visibleBurstSignals(event, '2026-11-20', '09:37').length, 1);
  assert.equal(visibleBurstSignals({updates:[{end:'09:35'}]}, '2026-11-20', '09:35').length, 1);
});
