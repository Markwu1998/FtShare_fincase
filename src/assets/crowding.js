const byId = id => document.getElementById(id);
const format = (value, digits = 2, suffix = '') => typeof value === 'number'
  ? value.toLocaleString('zh-CN', {minimumFractionDigits: digits, maximumFractionDigits: digits}) + suffix : '—';
const charts = new Map();
let snapshot;
let pending;
const modules = {};
// 观测台切换是整页导航：保存各筛选控件的选择，切回本页时恢复上次视图
const PERSISTED = ['marketDays', 'marketTableDays', 'marketTier', 'marketIndex',
  'industryIndex', 'industryMetric', 'industryChoice', 'fundIndustry', 'financeIndex'];
const choiceKey = id => 'ftshare-crowding-' + id;
// 读取已保存且当前控件仍然存在的选项值，无效时返回 null
function readChoice(id) {
  const saved = localStorage.getItem(choiceKey(id));
  return saved !== null && [...byId(id).options].some(option => option.value === saved) ? saved : null;
}
// 页面初始化时把保存的选择写回下拉框
function restoreChoices() {
  for (const id of PERSISTED) {
    const saved = readChoice(id);
    if (saved !== null) byId(id).value = saved;
  }
}
// 控件变化时写入本地存储
function saveChoice(id) { localStorage.setItem(choiceKey(id), byId(id).value); }

function draw(id, title, dates, series, axes) {
  const dark = document.documentElement.dataset.theme === 'dark';
  const textColor = dark ? '#dfedf0' : '#163039';
  const gridColor = dark ? '#28414b' : '#dce7e8';
  if (!charts.has(id)) charts.set(id, echarts.init(byId(id)));
  charts.get(id).setOption({
    animation: false,
    textStyle: {color: textColor},
    title: {text: title, textStyle: {color: textColor, fontSize: 14}},
    legend: {top: 29, type: 'scroll', textStyle: {color: textColor}},
    grid: {top: 85, bottom: 54, left: 14, right: 14, containLabel: true},
    tooltip: {trigger: 'axis', confine: true},
    xAxis: {type: 'category', data: dates, axisLabel: {hideOverlap: true, color: textColor}},
    yAxis: axes.map((name, i) => ({type: 'value', name, scale: true, position: i ? 'right' : 'left',
      offset: i === 2 ? 60 : 0, axisLabel: {color: textColor},
      nameTextStyle: {color: textColor}, splitLine: {show: i === 0, lineStyle: {color: gridColor}}})),
    dataZoom: [{type: 'inside'}, {type: 'slider', height: 16, bottom: 10}],
    series: series.map((item, index) => ({type: 'line', showSymbol: false, connectNulls: false,
      color: index ? '#e47638' : dark ? '#59d8bd' : '#087f73', ...item})),
  }, {notMerge: true});
}

function renderConcentration() {
  const {rows, indexes} = snapshot;
  const level = byId('marketTier').value;
  const index = byId('marketIndex').value;
  const series = [{name: '前 ' + level + '% 集中度', data: rows.map(r => r.concentration.tiers[level].share)}];
  if (index) series.push({name: indexes[index], yAxisIndex: 1, data: rows.map(r => r.indexes[index] ?? null)});
  draw('marketConcentrationChart', 'A 股成交额集中度', rows.map(r => r.date),
    series, index ? ['集中度（%）', '指数（点）'] : ['集中度（%）']);
}

function renderTable() {
  const level = byId('marketTier').value;
  byId('marketTableTitle').textContent = '前 ' + level + '% 成交额明细';
  const rows = snapshot.table_rows.slice(-Number(byId('marketTableDays').value)).reverse();
  byId('marketTable').replaceChildren(...rows.map(row => {
    const market = row.concentration;
    const tier = market.tiers[level];
    const line = document.createElement('tr');
    for (const [i, value] of [row.date, market.total == null ? '—' : tier.count,
      format(tier.amount, 1), format(tier.share, 1, '%'),
      tier.percentile == null ? '—' : format(tier.percentile, 1, '%') + '（' + tier.sample + ' 日）',
      market.traded + ' / ' + market.suspended + ' / ' + (market.expected - market.covered)].entries()) {
      const cell = line.insertCell();
      cell.textContent = value;
      cell.classList.toggle('num', i > 0);
    }
    return line;
  }));
}

function renderStyle() {
  const {rows, capital_stats: stats} = snapshot;
  const dates = rows.map(r => r.date);
  const capital = {name: '总市值比值', data: rows.map(r => r.style.capital)};
  if (stats.count > 1) {
    capital.markLine = {silent: true, symbol: 'none', lineStyle: {type: 'dashed'},
      label: {position: 'insideEndTop', formatter: '{b}'},
      data: [-2, -1, 0, 1, 2].map(step => ({
        name: step === 0 ? '均值' : (step > 0 ? '+' : '') + step + 'σ',
        yAxis: stats.mean + step * stats.std,
      }))};
  }
  draw('marketCapitalChart', '沪深300 / 中证1000 · 总市值比值', dates, [capital], ['倍']);
  draw('marketTurnoverChart', '沪深300 / 中证1000 · 成交额比值', dates,
    [{name: '成交额比值', data: rows.map(r => r.style.turnover)}], ['倍']);
}

function renderSummary() {
  const {rows, first_date: first, last_date: last} = snapshot;
  const current = rows.at(-1);
  const tier = current.concentration.tiers['1'];
  byId('marketTop1').textContent = format(tier.share, 1, '%');
  byId('marketTopDate').textContent = current.date + ' · ' + current.concentration.covered + '/' + current.concentration.expected + ' 只';
  byId('marketPercentile').textContent = format(tier.percentile, 1, '%');
  byId('marketSample').textContent = '近五年窗口内 · ' + tier.sample + ' 个有效交易日';
  byId('marketCapRatio').textContent = format(current.style.capital, 2, ' 倍');
  byId('marketCashRatio').textContent = format(current.style.turnover, 2, ' 倍');
  byId('marketCapDate').textContent = byId('marketCashDate').textContent = current.date;
  const built = snapshot.built_at ? new Date(snapshot.built_at).toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai', hour12: false}) : '—';
  byId('marketStatus').textContent = 'RQData · 日度 · 沪深 A 股 · 归档 ' + first + ' 至 ' + last + ' · 当前图表 ' + rows.length + ' 个交易日' + ' · 快照 ' + built;
}

function renderIndustrySeries(id, title, rows, weights, choice) {
  const selected = byId(choice).value;
  const values = rows.map(row => weights(row) ?? {});
  const top = values.map(data => Object.keys(data).sort((a, b) => data[b] - data[a] || a.localeCompare(b))[0]);
  const names = [...new Set(selected ? [selected] : top.filter(Boolean))];
  const series = names.map(name => {
    const data = values.map((v, i) => selected || name === top[i] ? v[name] ?? null : null);
    const peak = Math.max(...data.filter(v => v != null));
    const i = data.indexOf(peak);
    return {name, data, showSymbol: id === 'marketFundChart',
      markPoint: i < 0 ? undefined : {data: [{coord: [rows[i].date, peak], value: format(peak, 1) + '%'}]}};
  });
  draw(id, title, rows.map(r => r.date), series, ['占比（%）']);
}

// 重建行业下拉项，优先保留已保存或当前选择
function setIndustries(id, values) {
  const select = byId(id), current = select.value, saved = localStorage.getItem(choiceKey(id));
  const names = [...new Set(values.flatMap(v => Object.keys(v ?? {})))].sort((a, b) => a.localeCompare(b, 'zh-CN'));
  while (select.options.length > 1) select.remove(1);
  for (const name of names) select.add(new Option(name, name));
  const preferred = names.includes(current) ? current : saved !== null && names.includes(saved) ? saved : '';
  select.value = preferred;
}

function renderIndustry() {
  const {rows, label_frequency: labelFrequency} = modules.industry;
  const index = byId('industryIndex'), metric = byId('industryMetric');
  const weights = row => row.indexes[index.value]?.[metric.value];
  setIndustries('industryChoice', rows.map(weights));
  const recent = rows.filter(row => Object.keys(weights(row) ?? {}).length).at(-1);
  const coverage = recent?.indexes[index.value];
  byId('industryStatus').textContent = recent ? 'RQData · 日度 · 最近有效日 ' + recent.date + ' · 行业覆盖 ' +
    coverage.classified + '/' + coverage.expected + ' 只' +
    (labelFrequency === 'weekly' ? ' · 行业分类按周缓存' : '') : '所选指标暂缺有效数据';
  renderIndustrySeries('marketIndustryChart', index.selectedOptions[0].text + ' · ' + metric.selectedOptions[0].text,
    rows, weights, 'industryChoice');
}

function renderFunds() {
  const {rows} = modules.funds;
  const recent = rows.at(-1);
  setIndustries('fundIndustry', rows.map(r => r.weights));
  byId('fundStatus').textContent = 'RQData · 季度 · 报告期 ' + recent.date + ' · 披露截至 ' + recent.published +
    ' · 有持仓数据 ' + recent.funds + '/' + recent.expected + ' 只';
  renderIndustrySeries('marketFundChart', '季度前十大重仓股行业占比', rows, row => row.weights, 'fundIndustry');
}

function renderFinancing() {
  const {rows, indexes = {}} = modules.financing;
  const picker = byId('financeIndex');
  if (picker.options.length === 1) {
    for (const [code, name] of Object.entries(indexes)) picker.add(new Option(name, code));
    // 指数选项生成后恢复上次选择
    picker.value = readChoice('financeIndex') ?? picker.value;
  }
  const dates = rows.map(r => r.date);
  const lastDate = key => rows.findLast(r => r[key] != null)?.date ?? '待发布';
  byId('financingStatus').textContent = 'RQData · 日度 · 融资余额截至 ' + lastDate('balance') +
    ' · 市值/GDP 截至 ' + lastDate('gdp_share');
  const series = (name, key, axis = 0) => ({name, yAxisIndex: axis, data: rows.map(r => r[key] ?? null)});
  const index = picker.value ? [{name: indexes[picker.value], yAxisIndex: 2, data: rows.map(r => r.indexes?.[picker.value] ?? null)}] : [];
  draw('marketBalanceChart', '沪深融资余额与近 20 日日均净增', dates,
    [series('融资余额', 'balance'), series('日均净增', 'change', 1), ...index],
    ['余额（亿元）', '日均净增（亿元）', ...index.map(() => '指数（点）')]);
  draw('marketLeverageChart', '融资余额增速与占比', dates,
    [series('周均余额同比', 'weekly_yoy', 1), series('日均净增/余额', 'change_share'),
      series('融资余额/沪深 A 股市值', 'capital_share'), series('融资余额/沪深流通 A 股市值', 'floating_share'),
      series('融资余额/M2', 'm2_share'), ...index], ['占比（%）', '同比（%）', ...index.map(() => '指数（点）')]);
  draw('marketValuationChart', '沪深 A 股市值 / 名义 GDP', dates, [series('市值/GDP', 'gdp_share')], ['占比（%）']);
}

const moduleViews = {
  industry: [renderIndustry, 'industryStatus'],
  funds: [renderFunds, 'fundStatus'],
  financing: [renderFinancing, 'financingStatus'],
};

async function loadModule(name) {
  const [render, status] = moduleViews[name];
  try {
    const response = await fetch('/api/crowding/' + name);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    modules[name] = data;
    try { render(); } catch (error) { delete modules[name]; throw error; }
  } catch (error) {
    const message = byId(status);
    message.textContent = error.message + '。';
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.textContent = '重试';
    retry.addEventListener('click', () => {
      message.textContent = '正在读取快照';
      loadModule(name);
    }, {once: true});
    message.append(retry);
  }
}

async function load() {
  pending?.abort();
  const request = new AbortController();
  pending = request;
  byId('marketStatus').textContent = '正在读取市场日度快照';
  try {
    const query = new URLSearchParams({days: byId('marketDays').value, table_days: byId('marketTableDays').value});
    const response = await fetch('/api/crowding?' + query, {cache: 'no-store', signal: request.signal});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error);
    if (request.signal.aborted) return;
    snapshot = result;
    for (const id of ['marketDays', 'marketTableDays']) {
      let coversArchive = false;
      for (const option of byId(id).options) {
        option.disabled = coversArchive && !option.selected;
        coversArchive ||= Number(option.value) >= snapshot.archived_days;
      }
    }
    if (byId('marketIndex').options.length === 1) {
      for (const [code, name] of Object.entries(snapshot.indexes)) byId('marketIndex').add(new Option(name, code));
      // 指数选项动态生成后才能恢复上次选择，缺省选第一个指数
      byId('marketIndex').value = readChoice('marketIndex') ?? byId('marketIndex').options[1].value;
    }
    renderSummary();
    renderConcentration();
    renderStyle();
    renderTable();
  } catch (error) {
    if (error.name !== 'AbortError') byId('marketStatus').textContent = '市场拥挤度暂不可用 · ' + error.message;
  }
}

byId('marketDays').addEventListener('change', load);
byId('marketTier').addEventListener('change', () => { if (snapshot) { renderConcentration(); renderTable(); } });
byId('marketIndex').addEventListener('change', () => { if (snapshot) renderConcentration(); });
byId('marketTableDays').addEventListener('change', load);
window.addEventListener('resize', () => charts.forEach(chart => chart.resize()));
for (const id of ['industryIndex', 'industryMetric', 'industryChoice']) {
  byId(id).addEventListener('change', () => { if (modules.industry) renderIndustry(); });
}
byId('fundIndustry').addEventListener('change', () => { if (modules.funds) renderFunds(); });
byId('financeIndex').addEventListener('change', () => { if (modules.financing) renderFinancing(); });
// 所有筛选控件变化时写入本地存储，切换观测台后恢复
for (const id of PERSISTED) byId(id).addEventListener('change', () => saveChoice(id));
window.addEventListener('ftshare:theme-change', () => {
  if (snapshot) { renderConcentration(); renderStyle(); }
  for (const [name, [render]] of Object.entries(moduleViews)) if (modules[name]) render();
});
const extraSections = {marketIndustry: 'industry', marketFunds: 'funds', marketFinancing: 'financing'};
const observer = new IntersectionObserver(entries => {
  for (const entry of entries) {
    if (!entry.isIntersecting) continue;
    observer.unobserve(entry.target);
    loadModule(extraSections[entry.target.id]);
  }
}, {rootMargin: '200px'});
for (const id of Object.keys(extraSections)) observer.observe(byId(id));
restoreChoices();
load();
