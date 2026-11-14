// Private administrator process for Node deployments. The web server cannot start it.
import { readFile, writeFile, mkdir, rename } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { collectMinute, tradingMinute } from '../src/background/collector.js';
import { loadAPIKey } from './lib/key.mjs';
import { marketProvider } from './lib/rqdata-market.mjs';
import { rqdataLive } from './lib/rqdata-live.mjs';
import universe from '../config/universe.json' with { type: 'json' };
const provider = await marketProvider();
const file = resolve(process.env.FTSHARE_LIVE_CACHE_FILE || (provider === 'RQData' ? 'data/private/rqdata-live.json' : 'data/private/live-cache.json'));
const replayIndex = process.argv.indexOf('--replay');
const replay = replayIndex < 0 ? undefined : process.argv[replayIndex + 1];
if (replayIndex >= 0 && (provider !== 'RQData' || !/^\d{4}-\d{2}-\d{2}$/.test(replay || '') ||
    process.argv.includes('--watch') || !process.env.RQDATA_REPLAY_CACHE_FILE)) throw new Error('Replay requires RQData, a date and RQDATA_REPLAY_CACHE_FILE');
const cacheFile = replay ? resolve(process.env.RQDATA_REPLAY_CACHE_FILE) : file;
if (replay && cacheFile === file) throw new Error('Replay requires a separate cache file');
const key = provider === 'FTShare' ? await loadAPIKey() : null;
if (provider === 'FTShare' && !key) throw new Error('Configure an authorized FtShare collector key');
let entries = {}; try { entries = JSON.parse(await readFile(cacheFile, 'utf8')).entries || {}; } catch (e) { if (e.code !== 'ENOENT') throw e; }
await mkdir(dirname(cacheFile), { recursive: true }); let pending = Promise.resolve();
const cache = {
  async get(k, type) { return entries[k] === undefined ? null : type === 'json' ? JSON.parse(entries[k]) : entries[k]; },
  put(k, value) {
    entries[k] = value;
    pending = pending.catch(() => {}).then(async () => { await writeFile(cacheFile + '.pending', JSON.stringify({ schema: 1, entries }), { mode: 0o600 }); await rename(cacheFile + '.pending', cacheFile); });
    return pending;
  }
};
async function run() {
  if (provider === 'FTShare') {
    console.log(JSON.stringify(await collectMinute({ FTSHARE_CACHE: cache, FTSHARE_API_KEY: key }, { force: process.argv.includes('--now') })));
    return;
  }
  if (!replay && !process.argv.includes('--now') && !tradingMinute(Date.now())) {
    console.log(JSON.stringify({ skipped: 'outside_trading_session' }));
    return;
  }
  const cacheKey = 'live:rqdata:snapshot', prior = await cache.get(cacheKey, 'json');
  try {
    const value = await rqdataLive(universe.funds, { replay });
    if (!value || (!replay && prior?.value?.date === value.date &&
      (prior.value.last_complete_minute > value.last_complete_minute || prior.value.fetched_at > value.fetched_at))) {
      throw Object.assign(new Error('RQData snapshot incomplete'), { code: 'incomplete_snapshot' });
    }
    await cache.put(cacheKey, JSON.stringify({ value, checked_at: new Date().toISOString() }));
    console.log(JSON.stringify({ provider, saved: true, date: value.date, through: value.last_complete_minute, replay: !!replay }));
  } catch (error) {
    if (!process.argv.includes('--watch')) throw error;
    if (prior) await cache.put(cacheKey, JSON.stringify({ ...prior, last_error: error.code || 'upstream_error', checked_at: new Date().toISOString() }));
    console.log(JSON.stringify({ provider, saved: false, code: error.code || 'upstream_error' }));
  }
}
await run();
if (process.argv.includes('--watch')) for (;;) { await new Promise(resolve => setTimeout(resolve, 60000)); await run(); }
