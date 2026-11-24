import { evaluateRadar, RADAR_DEFAULTS, burstAnnouncement, burstAccounting, visibleBurstSignals } from './radar-math.js';
const fmt = (v, n = 1) => Number.isFinite(v) ? Number(v).toLocaleString('zh-CN', { minimumFractionDigits: n, maximumFractionDigits: n }) : '—';
const signed = v => Number.isFinite(v) ? (v > 0 ? '+' : '') + fmt(v, 2) : '—';
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const billions = value => Number.isFinite(value) ? fmt(value / 1e8, 2) + ' 亿' : '—';
const percent = value => Number.isFinite(value) ? fmt(value * 100, 1) + '%' : '—';
const futuresStates = {
  missing_ticks: '期货 Tick 覆盖不足',
  no_net_increase: '同窗未见整体净增仓',
  side_unavailable: '同窗增仓，方向待核对',
  sell_side_evidence: '同窗增仓伴卖侧迹象',
  increase_without_sell_evidence: '同窗增仓，已识别区间未见卖侧增仓',
};
const defaultFuturesMetric = 'estimated_sell_cny';
const futuresMetricGroups = [
  ['常用', {
    estimated_sell_cny: '主动卖成交额估算',
    net_oi_cny: '全窗口净增仓名义额',
    estimated_sell_oi_cny: '卖侧增仓估算额（成交均价）',
    turnover_cny: '全窗口期货成交额',
    oi_increase_cny: '全窗口持仓增加额',
    oi_decrease_cny: '全窗口持仓减少额',
  }],
  ...[['sell', '卖侧'], ['buy', '买侧'], ['unclassified', '未分类']].map(([side, name]) => [
    name + '区间',
    {
      ...(side === 'sell' ? {} : { [side + '.turnover_cny']: name + '成交额' + (side === 'buy' ? '估算' : '') }),
      [side + '.oi_increase_cny']: name + '区间持仓增加额',
      [side + '.oi_decrease_cny']: name + '区间持仓减少额',
      [side + '.oi_net_cny']: name + '区间净持仓变化额',
    },
  ]),
];
const futuresMetrics = Object.assign({}, ...futuresMetricGroups.map(([, group]) => group));
function futuresMetricValue(row, metric) {
  const [field, subfield] = metric.split('.');
  return subfield ? row?.directions?.[field]?.[subfield] : row?.[field];
}
function futuresDetail(value, metric) {
  if (!value) return '—';
  const amount = futuresMetricValue(value, metric);
  const metricLabel = futuresMetrics[metric];
  const ratio = Number.isFinite(amount) && value.etf_turnover_cny > 0 ? fmt(amount / value.etf_turnover_cny, 2) + '×' : '—';
  const contracts = value.contracts.map(({code, tenor, values: row}) =>
    `<li>${esc(code)} · ${esc(tenor)} · ${billions(futuresMetricValue(row, metric))}${row ? ' · 净持仓变化 '+fmt(row.delta_oi, 0)+' 手' : ''}${Number.isFinite(row?.relative_return_bp) ? ' · 相对现货 '+signed(row.relative_return_bp)+' bp' : ''}</li>`).join('');
  const coverageLabel = value.covered < value.expected ? ' · 覆盖不足' : '';
  return `<details class="radar-futures-detail"><summary>${esc(value.product)} · ${billions(amount)}${coverageLabel}</summary>
    <p>${esc(futuresStates[value.status] || '待核对')}</p>
    <p>${esc(value.window_start)}–${esc(value.window_end)} · ETF ${billions(value.etf_turnover_cny)} / ${fmt(value.etf_volume_shares, 0)} 股</p>
    <p>${esc(metricLabel)} ${billions(amount)} · 与 ETF 成交额之比 ${ratio}</p>
    <p>有效合约 ${fmt(value.covered, 0)}/${fmt(value.expected, 0)} · 方向可分类成交额占比 ${percent(value.classified_share)}</p>
    <ul>${contracts}</ul></details>`;
}
export function createRadar(root) {
  let seed, live, at = null, expanded = false;
  root.innerHTML = `<div class="radar-heading"><div><p class="eyebrow">ACTIVITY RADAR</p><h2>ETF 异动雷达<span>观察成交与申赎的变化</span></h2></div><span class="radar-session" id="radarSession">等待缓存</span></div>
    <div class="radar-board"><div class="radar-summary"><div><span class="radar-overline">20只核心 ETF · 成交活跃度</span><h3 id="radarState">等待可比数据</h3><p id="radarDescription">从共享缓存读取，不触发额外采集。</p></div><span class="radar-state-badge" id="radarBadge">待齐</span></div>
    <div class="radar-metrics"><div><span>累计成交额</span><strong id="radarAmount">—</strong><small id="radarAmountNote">—</small></div><div><span>20日 · 同进度倍数</span><strong id="radarMultiple">—</strong><small id="radarMultipleNote">—</small></div><div><span>60日 · 同进度倍数</span><strong id="radarLongMultiple">—</strong><small id="radarLongNote">—</small></div><div><span>近5分钟 · 20日 / 60日</span><strong id="radarBurst">—</strong><small id="radarBurstNote">—</small></div><div><span>上证指数涨跌</span><strong id="radarIndex">—</strong><small>与观察时点对应</small></div></div>
    <div class="radar-controls"><span class="radar-baselines">20日近期基准 × 60日辅助基准</span><label class="radar-replay">时点回放<input id="radarTime" type="range" min="0" max="240" value="240" aria-label="回放观察时点"><output id="radarTimeLabel">—</output></label><button id="radarFollow" type="button">跟随最新快照</button></div>
    <details class="radar-rules"><summary>信号规则与样本</summary><div class="radar-rule-inputs"><label>双基准累计阈值<input id="radarCumThreshold" type="number" min="1.1" max="5" step="0.1" value="1.5">倍</label><label>短时放量阈值<input id="radarBurstThreshold" type="number" min="1.2" max="10" step="0.1" value="2">倍</label><label>单只日度净申赎<input id="radarFlowThreshold" type="number" min="1" max="100" step="1" value="10">亿元</label></div><p>20日与60日均与历史同一时点比较，两者累计倍数都达到阈值才提示「持续关注」；仅20日触发为「近期放量」，仅60日触发为「长周期偏高」。短时信号要求5分钟成交额至少5亿元，两个基准连续两个逐分钟窗口都达到阈值才确认。开盘前15个交易分钟仅观察；每个基准须满20／60个完整历史交易日，样本不足不作双基准确认。</p><p id="radarSamples"></p><p>日度净申赎由每日份额与原始净值估算，按归属日期独立展示。以上为测试版初始观察阈值，尚未经过历史回测校准，可自行调整。</p></details></div>
    <div class="radar-board" id="etfBurst" hidden>
      <div class="radar-funds-heading"><div><h3>单只 ETF · 盘中集中交易</h3><p id="burstStatus" role="status">等待分钟行情</p></div></div>
      <div class="radar-controls">
        <label>行情模式 <select id="burstDate"><option value="">实时</option></select></label>
        <label id="burstTimeLabel" hidden>回放时点 <input id="burstTime" type="time" step="60" value="15:00"></label>
        <button id="burstSound" type="button" aria-pressed="false">开启语音播报</button>
        <label id="futuresMetricControl" hidden>期货金额口径 <select id="futuresMetric">${futuresMetricGroups.map(([name, options]) =>
          `<optgroup label="${name}">${Object.entries(options).map(([key, label]) =>
            `<option value="${key}">${label}</option>`).join('')}</optgroup>`).join('')}</select></label>
      </div>
      <div class="radar-voice" id="burstVoicePanel">
        <span class="radar-voice-title">语音设置</span>
        <label>音色<select id="burstVoice"><option value="">系统默认</option></select></label>
        <label>语速<input id="burstRate" type="range" min="0.1" max="10" step="0.05" value="1.05"><output id="burstRateValue">1.05×</output></label>
        <label>音调<input id="burstPitch" type="range" min="0.8" max="1.3" step="0.05" value="1"><output id="burstPitchValue">1.00</output></label>
        <label>音量<input id="burstVolume" type="range" min="0" max="1" step="0.05" value="1"><output id="burstVolumeValue">100%</output></label>
        <button id="burstTest" type="button">试听</button>
        <span class="radar-voice-status" id="burstVoiceStatus">检测语音引擎…</span>
      </div>
      <div class="tscroll"><table><thead><tr><th>ETF</th><th>首报 / 最新</th><th>级别</th><th>成交额窗口</th><th>同窗额比 20／60日</th><th>全天额比 20／60日</th><th>交易证据</th><th id="futuresColumn">同窗股指期货</th></tr></thead><tbody id="burstEvents"></tbody></table></div>
      <p class="radar-candidate" id="futuresStatus" hidden></p>
      <p class="radar-candidate" id="burstCandidates" role="status">Tick 分钟内候选：等待行情；不触发正式告警。</p>
      <p class="radar-candidate" id="tickFactorCandidates" role="status">秒级持续放量候选：等待历史 Tick 基准。</p>
      <div class="radar-metrics burst-metrics"><div><span>确认窗口去重成交额</span><strong id="burstGross">—</strong><small>亿元 · 二级及以上窗口并集</small></div><div><span>正超额成交额</span><strong id="burstExcess">—</strong><small>亿元 · 相对同分钟 P75</small></div><div><span>同篮子日末净申赎</span><strong id="burstFlow">—</strong><small id="burstFlowNote">亿元 · 盘后归属值</small></div><div><span>净申赎 / 异常成交额</span><strong id="burstFlowRatio">—</strong><small>量级比较，不代表净买入比例</small></div></div>
      <details class="radar-rules"><summary>事件日志与复盘口径</summary><p>实盘追加保留候选、正式命中、结束和原始行情时间／接收时间。候选不计入确认成交额，不播报；重叠窗口只计一次。日末净申赎与盘中成交属于不同口径，不能据此确认交易主体。</p><a id="burstJournal" target="_blank" rel="noopener">正式命中日志</a> <a id="futuresJournal" target="_blank" rel="noopener" hidden>同窗期货分类日志</a></details>
      <details><summary>Tick 与供应商 bar 对账</summary><p id="tickComparison">等待对账</p></details>
      <div id="burstDetail" class="chart" style="height:250px" hidden aria-label="所选事件的成交量与同窗基准"></div>
    </div>
    <div class="radar-funds-heading"><div><h3>单只 ETF · 日度资金信号</h3><p id="radarFundDates">等待共享快照</p></div><div class="radar-fund-counts" id="radarFundCounts"></div></div><div class="radar-funds" id="radarFunds"></div><button class="radar-expand" id="radarExpand" type="button">展开全部20只 ETF</button>`;
  const $ = id => root.querySelector('#' + id), number = (id, fallback, min, max) => { const v = parseFloat($(id).value); return Number.isFinite(v) ? Math.max(min, Math.min(max, v)) : fallback; };
  let burst, eventStream, choice = 0, audio, sound = false, burstEnabled = false;
  let burstChart, selected, details = new Map();
  const savedFuturesMetric = localStorage.getItem('ftshare-futures-metric');
  if (Object.hasOwn(futuresMetrics, savedFuturesMetric)) $('futuresMetric').value = savedFuturesMetric;
  $('futuresMetric').addEventListener('change', () => {
    localStorage.setItem('ftshare-futures-metric', $('futuresMetric').value);
    renderBurst();
  });
  const levels = ['', '预警', '确认', '极端放量'];
  const names = () => new Map((seed?.universe || []).map(fund => [fund.symbol, fund.name]));
  for (const [id, key] of [['burstRate', 'rate'], ['burstPitch', 'pitch'], ['burstVolume', 'volume']]) {
    const saved = localStorage.getItem('ftshare-' + key);
    if (saved !== null) $(id).value = saved;
  }
  fillVoices();
  if ('speechSynthesis' in window) {
    window.speechSynthesis.addEventListener?.('voiceschanged', fillVoices);
  }
  paintVoice();
  function renderBurst() {
    if (!burst) return;
    if(burst.schema_version!==2||burst.metric!=='turnover'||burst.unit!=='CNY'){
      $('burstEvents').replaceChildren();$('burstDetail').hidden=true;
      $('burstStatus').textContent='旧归档指标或单位不兼容，请重新生成成交额回放';return;
    }
    const cutoff = burst.replay ? ($('burstTime').value || '15:00') : '15:00';
    const knownNames = names();
    const accounting = burstAccounting(seed, burst, cutoff);
    $('burstGross').textContent = fmt(accounting?.gross, 2);
    $('burstExcess').textContent = fmt(accounting?.excess, 2);
    $('burstFlow').textContent = signed(accounting?.flow);
    const flow = burst.daily_flow;
    let flowNote = '亿元 · 盘后归属值';
    if (flow?.date === burst.date && cutoff >= '15:00') {
      flowNote += ` · 覆盖 ${flow.valid}/${flow.expected}`;
      if (flow.valid > 0 && flow.valid < flow.expected) {
        const covered = (burst.symbols || []).map(s => flow.by_symbol?.[s]?.flow_cny).filter(Number.isFinite);
        const subtotal = covered.reduce((sum, value) => sum + value, 0) / 1e8;
        if (covered.length === flow.valid) flowNote += ` · 已覆盖小计 ${signed(subtotal)} 亿元`;
      }
    }
    $('burstFlowNote').textContent = flowNote;
    $('burstFlowRatio').textContent = Number.isFinite(accounting?.ratio) ? fmt(accounting.ratio, 2) + '×' : '—';
    $('burstJournal').href = '/api/etf-burst/journal?date=' + encodeURIComponent(burst.date) + '&kind=formal';
    $('burstJournal').hidden = !!burst.replay || !burst.journal?.enabled;
    $('futuresJournal').href = '/api/etf-burst/journal?date=' + encodeURIComponent(burst.date) + '&kind=futures_evidence';
    $('futuresJournal').hidden = !burst.journal?.enabled || !burst.futures_monitor;
    const visibleAt = c => Date.parse(c.received_at || c.as_of) <= Date.parse(burst.date + 'T' + cutoff + ':00+08:00');
    const tickHistory = burst.replay ? (burst.tick_candidate_history || []).filter(visibleAt).slice(-10) : burst.tick_candidates || [];
    const factor = burst.tick_factor_status;
    $('tickFactorCandidates').textContent = tickHistory.length ? '秒级持续放量候选（未确认、不播报）：' + tickHistory.map(c =>
      (knownNames.get(c.symbol) || c.symbol) + ' · ' + c.window_seconds + '秒 · ' + fmt(c.amount / 1e8, 2) + '亿元 · 同窗 ' + fmt(c.ratio, 1) + '× · 持续占比 ' + fmt(c.persistence * 100, 0) + '%').join('；')
      : burst.replay ? '该时点无秒级候选；仅记录 Tick 接收序列和历史秒级基准的回放可还原。'
      : '秒级持续放量候选：暂无；历史基准 ' + (factor?.state || '待准备') + '，覆盖 ' + (factor?.covered ?? 0) + '/' + (factor?.expected ?? burst.symbols?.length ?? 20) + '只。';
    const tickReplay=burst.replay&&burst.feed==='tick-replay';
    const candidates=tickReplay?(burst.candidate_history||[]).filter(visibleAt).slice(-10):burst.replay?[]:burst.candidates||[];
    $('burstCandidates').textContent=candidates.length?(tickReplay?'Tick 历史候选（仅展示、不播报）：':'Tick 分钟内候选（未确认、不播报）：')+candidates.map(c=>
      (knownNames.get(c.symbol)||c.symbol)+' · '+c.signal.window+'分钟 · '+levels[c.signal.level]+'候选 · '+fmt(c.signal.amount/1e8,2)+'亿元 · 行情 '+new Date(c.as_of).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})).join('；')
      :tickReplay?'该回放时点暂无 Tick 候选。':burst.replay?'历史分钟回放不还原分钟内 Tick 候选。':'Tick 分钟内候选：暂无；候选不计入正式事件，不触发播报。';
    const monitor=burst.tick_monitor;
    $('tickComparison').textContent=monitor?
      '正式分钟来源：'+(monitor.minute_driver==='tick'?'Tick 本地聚合':'供应商 bar（Tick 并行验证中）')+
      '；已对账 '+monitor.compared+' 个分钟格，金额不一致 '+monitor.mismatched+' 个，共同完成分钟的事件差异 '+(monitor.event_mismatches??0)+' 个，待补 '+monitor.unrecovered+' 个，已由 bar 补齐 '+monitor.recovered+' 个。'+
      (monitor.differences||[]).slice(-5).map(r=>r.symbol+' '+r.slot+' 差额 '+fmt(r.difference,2)+'元').join('；')
      :'该归档没有 Tick 对账信息。';
    const rows = burst.events.filter(event => event.first <= cutoff && (!burst.replay || visibleBurstSignals(event, burst.date, cutoff).length)).slice().sort((a,b) => b.first.localeCompare(a.first));
    const futuresMonitor = burst.futures_monitor;
    const metric = $('futuresMetric').value || defaultFuturesMetric;
    const futuresStatus = $('futuresStatus');
    futuresStatus.hidden = !futuresMonitor;
    $('futuresMetricControl').hidden = futuresStatus.hidden;
    $('futuresColumn').textContent = futuresStatus.hidden ? '同窗股指期货' : futuresMetrics[metric];
    futuresStatus.textContent = burst.replay && futuresMonitor
      ? '股指期货 Tick 回放 · ' + (burst.replay_clock === 'received' ? '按记录接收时间' : '按行情时间重建') +
        ' · 仅显示所选时点已计算的窗口。'
      : futuresMonitor ? '股指期货 Tick · ' + ({
      receiving: '已收到行情', waiting: '等待行情', contracts_unavailable: '挂牌合约不可用', feed_unavailable: '订阅暂不可用'
    }[futuresMonitor.state] || '待核对') + ' · '+ (futuresMonitor.products || []).join(' / ') +
      '。方向按 Tick 估算。持仓减少额用负数表示。' : '';
    details = new Map();
    const pair = (values, mode) => values.map(v => v == null ? '—' : mode === 'pct' ? fmt(v * 100, 1) + '%' : fmt(v, 1) + '×').join(' / ');
    const openFutures = new Set(Array.from($('burstEvents').querySelectorAll('.radar-futures-detail[open]'), node => node.closest('tr').dataset.eventId));
    const replayTime = Date.parse(burst.date + 'T' + cutoff + ':00+08:00');
    $('burstEvents').innerHTML = rows.map(event => {
      const updates = burst.replay ? visibleBurstSignals(event, burst.date, cutoff) : event.updates;
      const signal = burst.replay ? updates
        .reduce((best, v) => v.level > best.level || v.level === best.level &&
          Math.min(...v.daily) > Math.min(...best.daily) ? v : best) : event.peak;
      const {premium, premium_cross, pressure, peers, index, ring} = event.evidence;
      const evidence = burst.replay ? [] : [
        premium && '溢价 ' + signed(premium.bp) + ' bp',
        premium_cross && '持续转溢',
        pressure && '卖一上移伴成交 ' + pressure.ask_up_with_volume + '次',
        peers?.length && '同指数同步 ' + peers.join('、'),
        index && '指数2分钟 ' + signed(index.pct) + '%' + (index.above_p95 ? ' · 超P95' : ''),
        Number.isFinite(ring?.adjusted) && '调整后日内环比 ' + fmt(ring.adjusted, 1) + '×',
      ].filter(Boolean);
      const last = burst.replay ? updates.at(-1).end : event.last;
      const futures = burst.replay
        ? (event.evidence.futures_windows || []).findLast(item => Date.parse(item.observed_at) <= replayTime)
        : event.evidence.futures;
      details.set(event.id, signal);
      return `<tr data-event-id="${esc(event.id)}"><td>${esc(knownNames.get(event.symbol) || event.symbol)} <small>${esc(event.symbol)}</small></td>
        <td>${esc(event.first)} / ${esc(last)}</td><td>${levels[signal.level]}${event.recovered && !burst.replay ? ' · 补数' : ''}</td>
        <td><button type="button" data-burst-id="${esc(event.id)}" title="窗口成交额 ${fmt(signal.amount / 1e8, 2)} 亿元">${esc(signal.start)}–${esc(signal.end)}</button></td>
        <td>${pair(signal.ratios)}</td><td>${pair(signal.daily, 'pct')}</td><td>${esc(evidence.join(' · ') || '—')}</td>
        <td>${futuresDetail(futures, metric)}</td></tr>`;
    }).join('');
    for (const node of $('burstEvents').querySelectorAll('.radar-futures-detail')) {
      node.open = openFutures.has(node.closest('tr').dataset.eventId);
    }
    const now = new Date(Date.now() + 8 * 3600000).toISOString().slice(11,16);
    const continuous = (now >= '09:32' && now <= '11:30') || (now >= '13:02' && now <= '14:57');
    const lag = (Date.now() - Date.parse(burst.date + 'T' + (burst.through || '00:00') + ':00+08:00')) / 1000;
    const phase = burst.replay ? '历史回放' : continuous && lag > 150 ? '分钟行情延迟' : 'RQData 实时推送';
    $('burstStatus').textContent = phase +
      ' · ' + burst.date + ' · 同窗 P' + Math.round(burst.quantile * 100) + ' · 截至 ' +
      (burst.replay ? cutoff : burst.through || '—') + ' · ' + rows.length + ' 条疑似集中交易事件' +
      (burst.baseline_ready === false ? ' · 历史基准待齐' : '');
    renderBurstDetail();
  }
  function renderBurstDetail() {
    const signal = details.get(selected), node = $('burstDetail');
    node.hidden = !signal?.minutes || !globalThis.echarts;
    if (node.hidden) return;
    const color = document.documentElement.dataset.theme === 'dark' ? '#dfedf0' : '#163039';
    const [h,m] = signal.start.split(':').map(Number);
    const slots = signal.minutes.map((_,i) =>
      String(h + Math.floor((m+i)/60)).padStart(2,'0') + ':' + String((m+i)%60).padStart(2,'0'));
    burstChart ||= globalThis.echarts.init(node);
    burstChart.resize();
    burstChart.setOption({animation:false,textStyle:{color},legend:{textStyle:{color}},
      grid:{top:42,bottom:24,left:52,right:25,containLabel:true},tooltip:{trigger:'axis',confine:true},
      xAxis:{type:'category',data:slots,axisLabel:{color}},
      yAxis:{type:'value',name:'亿元',axisLabel:{color},nameTextStyle:{color}},
      series:[{name:'分钟成交额',type:'bar',data:signal.minutes.map(v=>v/1e8)},
        ...[0,1].map(i=>({name:[20,60][i]+'日同分钟基准',type:'line',
          data:signal.minute_refs.map(v=>v[i] == null ? null : v[i]/1e8)}))]},{notMerge:true});
  }
  function chirp() {
    if (!sound || !audio || audio.state !== 'running') return;
    const oscillator = audio.createOscillator(), gain = audio.createGain();
    oscillator.frequency.value = 760; gain.gain.value = .08;
    oscillator.connect(gain); gain.connect(audio.destination);
    oscillator.start(); oscillator.stop(audio.currentTime + .16);
  }
  async function ensureAudio() {
    try {
      audio ||= new (window.AudioContext || window.webkitAudioContext)();
      await audio.resume();
    } catch { /* speech synthesis may still work */ }
  }
  function announce(event) {
    if (!sound) return;
    chirp();
    speak(burstAnnouncement(event));
  }
  function voices() {
    return 'speechSynthesis' in window ? window.speechSynthesis.getVoices() : [];
  }
  function speak(text) {
    const status = $('burstVoiceStatus');
    if (!('speechSynthesis' in window) || !('SpeechSynthesisUtterance' in window)) {
      if (status) status.textContent = '浏览器不支持语音合成';
      return false;
    }
    const list = voices();
    if (!list.length) {
      if (status) status.textContent = '未检测到系统语音，已使用提示音';
      return false;
    }
    try {
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = 'zh-CN';
      utterance.rate = number('burstRate', 1.05, .1, 10);
      utterance.pitch = number('burstPitch', 1, 0, 2);
      utterance.volume = number('burstVolume', 1, 0, 1);
      const uri = $('burstVoice')?.value;
      utterance.voice = list.find(voice => voice.voiceURI === uri)
        || list.find(voice => /^(zh|cmn)/i.test(voice.lang)) || null;
      utterance.onstart = () => {
        if (status) status.textContent = '正在播报' + (utterance.voice ? ' · ' + utterance.voice.name : '');
      };
      utterance.onerror = event => {
        if (status) status.textContent = '语音失败：' + (event.error || '未知错误');
      };
      utterance.onend = () => { if (status) status.textContent = '语音引擎就绪'; };
      window.speechSynthesis.cancel();
      window.speechSynthesis.speak(utterance);
      if (status) status.textContent = '已发送语音 · ' + (utterance.voice?.name || '系统默认');
      return true;
    } catch (error) {
      if (status) status.textContent = '语音异常：' + (error?.message || '未知错误');
      return false;
    }
  }
  function paintSound() {
    $('burstSound').setAttribute('aria-pressed', String(sound));
    $('burstSound').textContent = sound ? '关闭语音播报' : '开启语音播报';
  }
  function fillVoices() {
    const select = $('burstVoice');
    if (!select) return;
    const saved = localStorage.getItem('ftshare-voice') || select.value;
    const all = voices(), chinese = all.filter(voice => /^(zh|cmn)/i.test(voice.lang));
    const list = chinese.length ? chinese : all;
    select.replaceChildren(new Option('系统默认', ''));
    for (const voice of list) select.add(new Option(voice.name + ' · ' + voice.lang, voice.voiceURI));
    select.value = list.some(voice => voice.voiceURI === saved) ? saved : '';
    const status = $('burstVoiceStatus');
    if (status) status.textContent = !('speechSynthesis' in window)
      ? '浏览器不支持语音合成'
      : all.length ? '语音引擎：' + all.length + ' 个可用' : '未检测到系统语音';
  }
  function paintVoice() {
    $('burstRateValue').textContent = number('burstRate', 1.05, .1, 10).toFixed(2) + '×';
    $('burstPitchValue').textContent = number('burstPitch', 1, 0, 2).toFixed(2);
    $('burstVolumeValue').textContent = Math.round(number('burstVolume', 1, 0, 1) * 100) + '%';
    localStorage.setItem('ftshare-voice', $('burstVoice').options.length > 1 ? $('burstVoice').value : (localStorage.getItem('ftshare-voice') || ''));
    localStorage.setItem('ftshare-rate', $('burstRate').value);
    localStorage.setItem('ftshare-pitch', $('burstPitch').value);
    localStorage.setItem('ftshare-volume', $('burstVolume').value);
  }
  async function selectBurst() {
    const revision = ++choice, date = $('burstDate').value;
    eventStream?.close(); eventStream = null;
    burst = null;
    selected = null;
    $('burstDetail').hidden = true;
    $('burstEvents').replaceChildren();
    $('burstTimeLabel').hidden = !date;
    $('burstSound').disabled = !!date;
    $('burstStatus').textContent = '等待分钟行情';
    if (date) {
      try {
        const response = await fetch('/api/etf-burst?date=' + encodeURIComponent(date));
        if (!response.ok) throw new Error();
        const value = await response.json();
        if (revision === choice) { burst = value; renderBurst(); }
      } catch { if (revision === choice) $('burstStatus').textContent = '该日事件归档暂不可用'; }
      return;
    }
    if (!('EventSource' in window)) { $('burstStatus').textContent = '浏览器暂时无法接收实时事件'; return; }
    let settled = false;
    eventStream = new EventSource('/api/etf-burst/stream');
    eventStream.addEventListener('burst', message => {
      if (revision !== choice) return;
      const value = JSON.parse(message.data);
      if(value.schema_version!==2||value.metric!=='turnover'||value.unit!=='CNY')return;
      const known = new Map((burst?.events || []).map(event => [event.id, event.level]));
      const fresh = value.events.filter(event => !event.recovered && event.detected_at &&
          Date.now() - Date.parse(event.detected_at) < 15000 && event.level >= 1 && event.level > (known.get(event.id) || 0));
      if (settled && fresh.length) fresh.slice(0, 3).forEach(announce);
      burst = value;
      settled = true;
      renderBurst();
    });
    eventStream.onerror = () => {
      settled = false;
      if (revision === choice) $('burstStatus').textContent = '实时事件连接中断，正在重连';
    };
  }
  async function openBurst() {
    try {
      const response = await fetch('/api/etf-burst/dates');
      if (!response.ok) return;
      const {dates} = await response.json();
      for (const date of dates) $('burstDate').add(new Option('回放 ' + date, date));
      $('etfBurst').hidden = false;
      burstEnabled = true;
      selectBurst();
    } catch {}
  }
  $('burstDate').addEventListener('change', selectBurst);
  $('burstTime').addEventListener('change', renderBurst);
  $('burstEvents').addEventListener('click', event => {
    const button = event.target.closest('[data-burst-id]');
    if (button) { selected = button.dataset.burstId; renderBurstDetail(); }
  });
  window.addEventListener('resize', () => { if (!$('burstDetail').hidden) burstChart?.resize(); });
  window.addEventListener('ftshare:theme-change', renderBurstDetail);
  $('burstSound').addEventListener('click', () => {
    sound = !sound;
    paintSound();
    if (sound) {
      // Speak synchronously inside the click: Chrome can drop the user
      // activation if speech is deferred behind an awaited AudioContext.
      speak('语音播报已开启');
      ensureAudio().then(chirp);
    }
  });
  for (const id of ['burstRate', 'burstPitch', 'burstVolume']) $(id).addEventListener('input', paintVoice);
  $('burstVoice').addEventListener('change', paintVoice);
  $('burstTest').addEventListener('click', () => {
    if (!sound) { sound = true; paintSound(); }
    const sample = '五一零三三零，14点57分，5分钟20.78亿元，预警，为20日同窗P75的10.3倍';
    const ok = speak(sample);
    ensureAudio().then(chirp);
    if (!ok) $('burstTest').textContent = '语音不可用';
  });
  if (document.documentElement.dataset.dataMode !== 'demo') openBurst();
  function render(nextSeed = seed, nextLive = live) {
    seed = nextSeed; live = nextLive; if (!seed || !live) return;
    const s = evaluateRadar(seed, live, { at, cumulative: number('radarCumThreshold', RADAR_DEFAULTS.cumulative, 1.1, 5), burst: number('radarBurstThreshold', RADAR_DEFAULTS.burst, 1.2, 10), fundFlow: number('radarFlowThreshold', RADAR_DEFAULTS.fundFlow, 1, 100) });
    const labels = { normal: '成交处于常态区间', warming: '开盘观察中', insufficient: '20日基准待齐', incomplete: '成交数据待齐' };
    const reasons = { dual_cumulative: ['持续关注 · 双基准放量', '20日与60日的同进度累计成交倍数均达到阈值。', '双基准确认'], dual_burst: ['持续关注 · 短时放量', '20日与60日的同窗短时倍数均已连续两个滚动窗口达到阈值。', '双基准确认'], recent: ['近期放量 · 继续观察', '20日累计倍数达到阈值，60日基准尚未确认。', '近期放量'], long: ['长周期偏高 · 继续观察', '仅60日累计倍数达到阈值，20日近期基准未触发。', '长周期偏高'], burst: ['短时放量 · 继续观察', '短时成交达到观察阈值，尚未满足双基准连续窗口确认。', '短时观察'] };
    let [title, description, badge] = reasons[s.reason] || [labels[s.state], s.state === 'incomplete' ? '当前累计包含缺失分钟，累计倍数暂不判断。' : s.state === 'insufficient' ? '至少需要20个完整历史交易日，样本不足时不产生放量判断。' : s.state === 'warming' ? '开盘初段先观察成交节奏，满15个交易分钟后判定放量。' : '当前可比成交数据未触及所设放量阈值。', ({ normal: '常态', warming: '开盘观察', insufficient: '样本不足', incomplete: '数据待齐' })[s.state]];
    if (!s.long.ready) description += ' 60日样本待齐（' + s.long.samples + '/60），不作双基准确认。';
    root.dataset.state = s.state; $('radarState').textContent = title; $('radarDescription').textContent = description; $('radarBadge').textContent = badge;
    const now = new Date(Date.now() + 8 * 3600000).toISOString(), phase = s.date < now.slice(0, 10) ? '历史快照' : now.slice(11, 16) >= '15:00' ? '已收盘' : now.slice(11, 16) >= '11:30' && now.slice(11, 16) < '13:00' ? '午间休市' : '盘中观察';
    $('radarSession').textContent = phase + ' · ' + s.date;
    $('radarAmount').innerHTML = fmt(s.amount) + '<em>亿元</em>'; $('radarAmountNote').textContent = s.time ? '截至 ' + s.time + (at === null ? ' · 最新缓存' : ' · 回放时点') : '等待完整分钟';
    $('radarMultiple').innerHTML = fmt(s.multiple, 2) + '<em>×</em>'; $('radarMultipleNote').textContent = '均值 ' + fmt(s.reference) + '亿 · ' + s.recent.samples + '/20日';
    $('radarLongMultiple').innerHTML = fmt(s.long.multiple, 2) + '<em>×</em>'; $('radarLongNote').textContent = '均值 ' + fmt(s.long.reference) + '亿 · ' + s.long.samples + '/60日';
    $('radarBurst').innerHTML = fmt(s.recent.short.ratio, 2) + '<em> / </em>' + fmt(s.long.short.ratio, 2) + '<em>×</em>'; $('radarBurstNote').textContent = s.short.range ? seed.minute_slots[s.short.range[0]] + '—' + s.time + ' · ' + fmt(s.short.amount) + '亿' : '当前时段未满5分钟';
    $('radarIndex').textContent = signed(s.indexPct) + (s.indexPct === null ? '' : '%'); $('radarIndex').className = s.indexPct > 0 ? 'positive' : s.indexPct < 0 ? 'negative' : '';
    $('radarTime').max = Math.max(0, s.last); $('radarTime').value = Math.max(0, s.end); $('radarTimeLabel').textContent = s.time || '—'; $('radarFollow').classList.toggle('is-following', at === null);
    if (burstEnabled && burst?.replay && burst.date === live.date && at !== null && s.time) {
      $('burstTime').value = s.time;
      renderBurst();
    }
    $('radarSamples').textContent = [s.recent,s.long].map(b => b.window + '日基准：' + b.samples + '/' + b.window + '日' + (b.samples ? '（' + b.sampleDates.at(-1) + '—' + b.sampleDates[0] + '）' : '')).join('；') + '。仅使用观察日之前的完整快照。';
    $('radarFundDates').textContent = '行情 ' + live.date + ' ' + (live.last_complete_minute || '—') + ' ｜ 净申赎归属 ' + (s.flowDate || '待归档') + '，与回放时点分开展示';
    $('radarFundCounts').innerHTML = `<span class="flow-creation">净申购偏大 ${s.creations}只</span><span class="flow-redemption">净赎回偏大 ${s.redemptions}只</span>`;
    const funds = expanded ? s.table : s.table.slice(0, 6), names = new Map(seed.universe.map(f => [f.symbol, f]));
    $('radarFunds').innerHTML = funds.map(f => { const meta = names.get(f.symbol), badge = f.flowSignal === 'creation' ? '日度净申购偏大' : f.flowSignal === 'redemption' ? '日度净赎回偏大' : !Number.isFinite(f.flow) ? '日度数据待齐' : '未触及日度阈值'; return `<article class="radar-fund ${f.flowSignal || ''}"><div class="radar-fund-title"><h4 title="${esc(meta?.name || f.name)}">${esc(meta?.name || f.name)}</h4><span>${badge}</span></div><p class="radar-fund-code">${esc(f.symbol)} · ${esc(meta?.bucket || '')}</p><div class="radar-fund-values"><div><small>最新价</small><b>${fmt(f.price, 4)}</b></div><div><small>行情涨跌幅</small><b class="${f.pct > 0 ? 'positive' : f.pct < 0 ? 'negative' : ''}">${signed(f.pct)}${f.pct === null ? '' : '%'}</b></div><div><small>当日成交额</small><b>${fmt(f.amount, 2)}<em>亿</em></b></div></div><div class="radar-fund-flow"><span>日度净申购估算<small>${esc(f.flowDate || '数据待齐')}</small></span><strong class="${f.flow > 0 ? 'positive' : f.flow < 0 ? 'negative' : ''}">${signed(f.flow)}<em>亿</em></strong></div></article>`; }).join('');
    $('radarExpand').textContent = expanded ? '收起为重点6只 ETF' : '展开全部' + s.table.length + '只 ETF';
  }
  for (const id of ['radarCumThreshold', 'radarBurstThreshold', 'radarFlowThreshold']) $(id).addEventListener('input', () => render());
  $('radarTime').addEventListener('input', () => { at = Number($('radarTime').value); render(); });
  $('radarFollow').addEventListener('click', () => { at = null; render(); });
  $('radarExpand').addEventListener('click', () => { expanded = !expanded; render(); });
  function updateQuotes(value){
    if(at!==null||value?.replay)return;
    if(Number.isFinite(value?.index_quote?.price)&&value.pre_close>0){
      const pct=(value.index_quote.price/value.pre_close-1)*100;
      $('radarIndex').textContent=signed(pct)+'%';
      $('radarIndex').className=pct>0?'positive':pct<0?'negative':'';
    }
    const quotes=new Map((value?.table||[]).map(row=>[row.symbol,row]));
    root.querySelectorAll('.radar-fund').forEach(card=>{
      const symbol=card.querySelector('.radar-fund-code')?.textContent.split(' · ')[0],quote=quotes.get(symbol);
      if(!quote)return;
      const cells=card.querySelectorAll('.radar-fund-values b');
      cells[0].textContent=fmt(quote.price,4);
      cells[1].textContent=signed(quote.pct)+(quote.pct==null?'':'%');
      cells[1].className=quote.pct>0?'positive':quote.pct<0?'negative':'';
      cells[2].innerHTML=fmt(quote.amount,2)+'<em>亿</em>';
    });
  }
  return { render, updateQuotes };
}
