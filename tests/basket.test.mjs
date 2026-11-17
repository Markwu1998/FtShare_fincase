import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const read = path => JSON.parse(readFileSync(path, 'utf8'));
const basket = read('config/basket.json').symbols;
const universe = read('config/universe.json').funds;
const model = readFileSync('src/server/model.js', 'utf8');
const listed = model.match(/export const BASKET = \[([^\]]*)\];/);
const modelBasket = listed[1].split(',').map(item => item.trim().replace(/^'|'$/g, '')).filter(Boolean);

test('basket config is the single source for Python and Node', () => {
    assert.equal(basket.length, 20);
    assert.equal(new Set(basket).size, basket.length);
    assert.deepEqual(modelBasket, basket);
    const core = universe.filter(fund => fund.is_core_basket).map(fund => fund.symbol).sort();
    assert.deepEqual(core, [...basket].sort());
    for (const symbol of basket) assert.ok(universe.some(fund => fund.symbol === symbol), symbol);
});

test('burst config follows the basket unless it overrides symbols', () => {
    const burst = read('config/etf-burst.json');
    if (burst.symbols) assert.deepEqual(burst.symbols, basket);
});
