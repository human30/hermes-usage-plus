(function () {
'use strict';
const SDK = window.__HERMES_PLUGIN_SDK__;
const { React } = SDK;
// Kept dependency-free and mirrored in the dashboard IIFE; parity is tested.
const h = React.createElement;
const finite = v => typeof v === 'number' && Number.isFinite(v);
const number = v => finite(v) ? v.toLocaleString('ko-KR', { maximumFractionDigits: 2 }) : '미확인';
const money = v => finite(v) ? `$${v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 6 })}` : '미확인';
const date = v => {
  if (v == null) return '미확인';
  const d = new Date(typeof v === 'number' ? v * 1000 : v);
  return Number.isNaN(d.getTime()) ? '미확인' : d.toLocaleString('ko-KR');
};
const styles = {
  page: { padding: '20px', color: 'var(--color-foreground, var(--vscode-foreground))', fontFamily: 'inherit', lineHeight: 1.6, overflow: 'auto', height: '100%', boxSizing: 'border-box' },
  card: { border: '1px solid var(--color-border, var(--vscode-panel-border))', borderRadius: 'var(--radius, 8px)', background: 'var(--color-card, var(--vscode-editor-background))', padding: '16px', minWidth: 0 },
  grid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 260px), 1fr))', gap: '12px' },
  muted: { color: 'var(--color-muted-foreground, var(--vscode-descriptionForeground))', fontSize: '0.9em' },
  button: { color: 'inherit', background: 'var(--color-secondary, var(--vscode-button-secondaryBackground))', border: '1px solid var(--color-border, var(--vscode-button-border))', borderRadius: 'var(--radius, 6px)', padding: '6px 12px', cursor: 'pointer', font: 'inherit' },
  cell: { textAlign: 'left', verticalAlign: 'top', padding: '10px', borderBottom: '1px solid var(--color-border, var(--vscode-panel-border))', minWidth: '135px', overflowWrap: 'anywhere' }
};
class UsageBoundary extends React.Component {
  constructor(props) { super(props); this.state = { failed: false }; }
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    return this.state.failed ? h('section', { role: 'alert', style: styles.card },
      h('p', null, 'Usage+ 화면을 표시하지 못했습니다. 다른 화면에는 영향을 주지 않습니다.'),
      h('button', { style: styles.button, onClick: () => this.setState({ failed: false }) }, '화면 다시 시도')) : this.props.children;
  }
}
function QueryStatus({ title, query }) {
  const data = query.data;
  const failed = query.error || data?.status === 'error';
  return h('div', { role: failed ? 'alert' : 'status', 'aria-live': 'polite', style: styles.muted },
    `${title}: `,
    query.isLoading ? '불러오는 중…' : failed ? '조회 오류' : query.isFetching ? '새로고침 중…' : data?.status === 'stale' ? '오래된 데이터' : '조회 완료',
    (failed || data?.status === 'stale') && data ? ' · 마지막 데이터 표시 (오래된 데이터일 수 있음)' : '',
    failed ? ` · ${String(data?.error || query.error?.message || '조회할 수 없습니다.')}` : '',
    data ? ` · 수집 시각 ${date(data.fetched_at)}` : '',
    ...(Array.isArray(data?.warnings) ? data.warnings : []).map((w, i) => h('p', { key: i }, String(w))));
}
function AccountCards({ data }) {
  const accounts = Array.isArray(data?.accounts) ? data.accounts : [];
  return h('section', { 'aria-label': '계정 한도' }, h('h2', null, '계정 한도 및 제공자 정보'),
    h('p', { style: styles.muted }, '계정 잔액·크레딧은 제공자가 전달한 값이며, 실제 청구 합계나 API 환산액이 아닙니다. 모델 범위와 계정 공유 한도를 구분합니다.'),
    accounts.length ? h('div', { style: styles.grid }, ...accounts.map((a, i) => h('article', { key: `${a.provider}-${i}`, style: styles.card },
      h('h3', null, String(a.provider || '제공자 미확인')),
      h('p', null, ({ ok: '정상', unavailable: '조회 불가', unsupported: '미지원', error: '조회 오류' })[a.status] || '상태 미확인'),
      a.plan ? h('p', null, `플랜: ${a.plan}`) : null,
      h('p', { style: styles.muted }, `출처: ${a.source || '미확인'} · 수집 시각: ${date(a.fetched_at)}`),
      a.reason ? h('p', null, String(a.reason)) : null,
      a.status === 'ok' ? (Array.isArray(a.windows) ? a.windows : []).map((w, j) => h('div', { key: j },
        h('strong', null, String(w.label || '한도')),
        h('p', null, `${w.scope === 'model' ? '모델 범위' : '계정 공유 범위'} · 사용 ${finite(w.used_percent) ? number(w.used_percent) + '%' : '미확인'} · 잔여 ${finite(w.remaining_percent) ? number(w.remaining_percent) + '%' : '미확인'}`),
        finite(w.used_percent) ? h('progress', { value: Math.max(0, Math.min(100, w.used_percent)), max: 100, 'aria-label': `${a.provider} ${w.label} 사용률`, style: { width: '100%', accentColor: 'var(--color-primary, var(--vscode-progressBar-background))' } }) : null,
        h('p', { style: styles.muted }, `초기화: ${date(w.reset_at)}`), w.detail ? h('p', null, String(w.detail)) : null)) : h('p', null, '잔여 한도 미확인 — 0으로 간주하지 않습니다.'),
      ...(Array.isArray(a.details) ? a.details : []).map((d, j) => h('p', { key: `detail-${j}` }, `제공자 계정 정보: ${String(d)}`))))) : h('p', null, '표시할 계정 정보가 없습니다.'));
}
function ModelTable({ data }) {
  const models = Array.isArray(data?.models) ? data.models : [];
  const headers = ['모델 / 제공자', '호출 / 세션', '입력·출력 tokens', '캐시 / prompt tokens', '금액 (USD)', '가격 출처', '요청 속도 / 시간'];
  return h('section', { 'aria-label': '모델별 사용량' }, h('h2', null, '모델별 사용량'),
    h('div', { style: { overflowX: 'auto' }, tabIndex: 0, role: 'region', 'aria-label': '모델 표 가로 스크롤' },
      h('table', { style: { borderCollapse: 'collapse', width: '100%' } },
        h('caption', { style: { ...styles.muted, textAlign: 'left' } }, '입력 tokens는 캐시 제외 · prompt tokens는 입력 + 캐시 읽기 + 캐시 쓰기. 과거 캐시 0은 미보고일 수 있으며 0% 적중의 증거가 아닙니다.'),
        h('thead', null, h('tr', null, ...headers.map(v => h('th', { key: v, scope: 'col', style: styles.cell }, v)))),
        h('tbody', null, ...models.map((m, i) => h('tr', { key: `${m.provider}-${m.model}-${i}` },
          h('th', { scope: 'row', style: styles.cell }, String(m.model), h('p', { style: styles.muted }, `${m.provider || '미확인'} · ${m.billing_mode || '결제 방식 미확인'}`)),
          h('td', { style: styles.cell }, `API 호출 ${number(m.api_calls)}`, h('p', null, `main ${number(m.main_calls)} / aux ${number(m.aux_calls)}`), h('p', null, `세션 ${number(m.sessions)}`)),
          h('td', { style: styles.cell }, `입력 (캐시 제외) ${number(m.input_tokens)}`, h('p', null, `출력 ${number(m.output_tokens)}`), h('p', null, `reasoning ${number(m.reasoning_tokens)}`)),
          h('td', { style: styles.cell }, `캐시 읽기 ${number(m.cache_read_tokens)}`, h('p', null, `캐시 쓰기 ${number(m.cache_write_tokens)}`), h('p', null, `prompt ${number(m.prompt_tokens)}`),
            h('p', null, `적중률 ${finite(m.cache_hit_pct) ? number(m.cache_hit_pct) + '%' : '미보고 / 미확인'} · ${m.cache_status || '상태 미확인'}`)),
          h('td', { style: styles.cell }, h('p', null, `실제 청구 ${money(m.actual_cost_usd)}`), h('p', null, `API 환산 ${money(m.api_value_usd)}`), h('p', { style: styles.muted }, `추정 비용 (실제 청구 아님) ${money(m.estimated_cost_usd)}`)),
          h('td', { style: styles.cell }, String(m.pricing_source || '미확인'), h('p', null, `가격 버전: ${m.pricing_version || '미확인'}`)),
          h('td', { style: styles.cell }, `평균 ${number(m.avg_tps)} tokens/s`, h('p', null, `평균 요청 시간 ${number(m.avg_latency_s)}초`), h('p', null, `timing 표본 ${number(m.timing_samples)}`))))))),
    !models.length ? h('p', null, '선택한 기간에 표시할 모델이 없습니다.') : null,
    h('p', { style: styles.muted }, 'provider/model 전체 속도 (billing mode 공통 표본) = 출력 tokens / 전체 API 요청 시간 (first-token latency 포함). 순수 decode tokens/s가 아닙니다. timing 표본은 새로 수집한 요청에만 있습니다.'));
}
function PricingStatus({ data }) {
  if (!data) return null;
  return h('section', { style: styles.card, 'aria-label': '공식 가격 자동 갱신' },
    h('h2', null, '공식 가격 자동 갱신'),
    h('p', null, `일일 갱신 · 저장 가격 ${number(data.price_count)}개 · ${data.stale ? '가격 오래됨 / 미확인' : '가격 최신'}`),
    ...Object.entries(data.sources || {}).map(([name, info]) => h('p', { key: name, style: styles.muted },
      `${name}: ${info.status === 'ok' ? '정상' : '갱신 실패 — 마지막 정상 단가 유지'} · ${number(info.count)}개 · 마지막 정상 확인 ${date(info.checked_at)}${info.stale ? ' · 가격 오래됨' : ''}`)));
}
function UsageView({ days, setDays, summary, accounts, refresh }) {
  const t = summary.data?.totals;
  return h('section', { style: styles.page, 'aria-label': 'Usage+ 사용량 분석' },
    h('header', null, h('h1', null, 'Usage+ · 사용량과 계정 한도'),
      h('div', { role: 'group', 'aria-label': '기간 선택', style: { display: 'flex', gap: '8px', flexWrap: 'wrap' } },
        ...[7, 30, 90].map(d => h('button', { key: d, type: 'button', style: { ...styles.button, outline: days === d ? '2px solid var(--color-primary, var(--vscode-focusBorder))' : undefined }, 'aria-pressed': days === d, onClick: () => setDays(d) }, `${d}일`)),
        h('button', { type: 'button', style: styles.button, disabled: summary.isFetching || accounts.isFetching, onClick: refresh }, '새로고침')),
      h('p', { style: styles.muted }, `최근 ${days}일 안에 시작한 세션의 전체 생애 누적 사용량입니다 (session_started_at 기준). 정확한 일별 지출이 아닙니다. 기간 밖에서 발생한 해당 세션의 사용량도 포함됩니다.`)),
    h(QueryStatus, { title: '사용량', query: summary }), h(QueryStatus, { title: '계정', query: accounts }),
    t ? h('div', { style: styles.grid },
      h('article', { style: styles.card }, h('h2', null, 'API 호출'), h('strong', null, number(t.api_calls)), h('p', null, `입력 (캐시 제외) ${number(t.input_tokens)} · 출력 ${number(t.output_tokens)}`), h('p', null, `prompt ${number(t.prompt_tokens)} · 캐시 읽기 ${number(t.cache_read_tokens)}`)),
      h('article', { style: styles.card }, h('h2', null, `API 환산 ${t.priced_models < t.models ? '부분 합계' : '합계'}`), h('strong', null, money(t.api_value_usd)), h('p', null, `가격 적용 ${number(t.priced_models)} / ${number(t.models)} 모델`), h('p', { style: styles.muted }, '가격 스냅샷 기준 API 환산액이며 실제 청구액이 아닙니다. 미확인 모델 비용은 합산하지 않습니다.'))) : null,
    h(PricingStatus, { data: summary.data?.pricing }), h(AccountCards, { data: accounts.data }), h(ModelTable, { data: summary.data }));
}

// Each resource owns its generation, timer and in-flight request. A filter
// change clears data immediately; a late response cannot populate the new view.
function usePolling(path, interval) {
  const [state, setState] = React.useState({ path, data: undefined, error: null, isLoading: true, isFetching: true });
  const loader = React.useRef(null);
  React.useEffect(() => {
    let active = true;
    let inFlight = false;
    let controller;
    async function load() {
      if (!active || inFlight) return;
      inFlight = true;
      controller = new AbortController();
      setState(old => ({ path, data: old.path === path ? old.data : undefined, error: null, isLoading: old.path !== path || !old.data, isFetching: true }));
      try {
        const data = await SDK.fetchJSON('/api/plugins/usage-plus' + path, { signal: controller.signal });
        if (active && !controller.signal.aborted) setState({ path, data, error: null, isLoading: false, isFetching: false });
      } catch (error) {
        if (active && !controller.signal.aborted) setState(old => ({ ...old, error, isLoading: false, isFetching: false }));
      } finally { inFlight = false; }
    }
    loader.current = load;
    void load();
    const timer = setInterval(load, interval);
    return () => {
      active = false;
      clearInterval(timer);
      controller?.abort();
      if (loader.current === load) loader.current = null;
    };
  }, [path, interval]);
  const refetch = React.useCallback(() => loader.current?.(), []);
  const current = state.path === path ? state : { data: undefined, error: null, isLoading: true, isFetching: true };
  return { ...current, refetch };
}
function DashboardUsage() {
  const [days, setDays] = React.useState(30);
  const summary = usePolling('/summary?days=' + days, 60000);
  const accounts = usePolling('/accounts', 300000);
  return h(UsageView, { days, setDays, summary, accounts, refresh: () => { void summary.refetch(); void accounts.refetch(); } });
}
function Page() { return h(UsageBoundary, null, h(DashboardUsage)); }
window.__HERMES_PLUGINS__.register('usage-plus', Page);
window.__HERMES_PLUGINS__.registerSlot('usage-plus', 'analytics:top', Page);
})();
