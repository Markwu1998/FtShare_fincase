export function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[char]));
}

export function withSourceQuery(path, dailySource, realtimeSource) {
  const url = new URL(path, 'http://localhost');
  if (['/api/board', '/api/health', '/api/history', '/api/cumulative'].includes(url.pathname)) {
    url.searchParams.set('source', dailySource);
  }
  if (url.pathname === '/api/live') {
    url.searchParams.set('realtime_source', realtimeSource);
  }
  return url.pathname + url.search;
}

export function chooseSources(catalog, search, storedDaily, storedRealtime) {
  const sources = catalog?.sources || [];
  const realtime = catalog?.realtime_sources || [];
  const params = new URLSearchParams(search || '');
  const pick = (requested, list, fallback) =>
    list.some(item => item.id === requested && item.ready) ? requested : fallback;
  return {
    dailySource: pick(params.get('source') || storedDaily, sources, catalog?.default || 'rqdata'),
    realtimeSource: pick(params.get('realtime_source') || storedRealtime, realtime,
      catalog?.realtime_default || 'rqdata'),
  };
}

export function sourceOption(source, active, realtime = false) {
  const dot = source.ready ? '' : 'warn';
  const detail = source.ready
    ? (realtime ? '实时推送' : (source.latest_flow_date || source.latest_market_date || '—') + (source.default ? ' · 默认' : ''))
    : (source.reason || '未就绪');
  return '<button class="source-option' + (active ? ' is-active' : '') + '" data-source="' +
    escapeHtml(source.id) + '" role="option" aria-selected="' + active + '"' +
    (source.ready ? '' : ' aria-disabled="true" disabled') + '>' +
    '<span class="source-dot ' + dot + '"></span><span><b>' + escapeHtml(source.label) +
    '</b><small>' + escapeHtml(detail) + '</small></span>' +
    (active ? '<span class="source-check">✓</span>' : '') + '</button>';
}
