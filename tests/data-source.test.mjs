import assert from 'node:assert/strict';
import test from 'node:test';

import {chooseSources, sourceOption, withSourceQuery} from '../src/assets/data-source.js';

const catalog = {
  default: 'rqdata',
  realtime_default: 'rqdata',
  sources: [
    {id: 'rqdata', label: 'RQData', ready: true, latest_flow_date: '2026-11-17', default: true},
    {id: 'ddb', label: 'DDB', ready: false, reason: 'not ready'},
  ],
  realtime_sources: [{id: 'rqdata', label: 'RQData', ready: true, default: true}],
};

test('source query parameters are scoped by endpoint', () => {
  assert.equal(withSourceQuery('/api/board', 'ddb', 'rqdata'), '/api/board?source=ddb');
  assert.equal(withSourceQuery('/api/live', 'ddb', 'rqdata'), '/api/live?realtime_source=rqdata');
  assert.equal(withSourceQuery('/api/sources', 'ddb', 'rqdata'), '/api/sources');
});

test('source selection prefers URL, then storage, then default', () => {
  assert.deepEqual(chooseSources(catalog, '?source=rqdata', 'ddb', null), {
    dailySource: 'rqdata', realtimeSource: 'rqdata',
  });
  assert.deepEqual(chooseSources(catalog, '', 'ddb', null), {
    dailySource: 'rqdata', realtimeSource: 'rqdata',
  });
  assert.deepEqual(chooseSources(catalog, '?source=ddb', null, null), {
    dailySource: 'rqdata', realtimeSource: 'rqdata',
  });
});

test('source options render disabled and active states safely', () => {
  const active = sourceOption(catalog.sources[0], true);
  assert.match(active, /is-active/);
  assert.match(active, /RQData/);
  const disabled = sourceOption(catalog.sources[1], false);
  assert.match(disabled, /disabled/);
  assert.match(disabled, /not ready/);
  assert.doesNotMatch(disabled, /is-active/);
});
