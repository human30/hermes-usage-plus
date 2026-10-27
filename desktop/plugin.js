import React from 'react';
import { host, useValue, useQuery, gatewayActivationEpoch, ROUTES_AREA, SIDEBAR_NAV_AREA } from '@hermes/plugin-sdk';

// Kept dependency-free and mirrored in the dashboard IIFE; parity is tested.
const h = React.createElement;
const finite = v => typeof v === 'number' && Number.isFinite(v);
const numberFormat = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 2 });
const moneyFormat = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 6 });
const number = v => finite(v) ? numberFormat.format(v) : '미확인';
const money = v => finite(v) ? `$${moneyFormat.format(v)}` : '미확인';
const date = v => {
  if (v == null) return '미확인';
  const d = new Date(typeof v === 'number' ? v * 1000 : v);
  return Number.isNaN(d.getTime()) ? '미확인' : d.toLocaleString('ko-KR');
};
const usageCSS = `
.usage-plus {
  --up-text: var(--ui-text-primary, var(--color-foreground, var(--vscode-foreground, CanvasText)));
  --up-muted: var(--ui-text-secondary, var(--color-muted-foreground, var(--vscode-descriptionForeground, GrayText)));
  --up-line: var(--ui-stroke-secondary, var(--color-border, var(--vscode-panel-border, ButtonBorder)));
  --up-accent: var(--ui-accent, var(--color-primary, var(--vscode-focusBorder, Highlight)));
  color: var(--up-text); font-family: inherit; font-size: 13px; line-height: 1.55;
  padding: 24px; height: 100%; min-width: 0; overflow: auto; box-sizing: border-box;
  font-variant-numeric: tabular-nums;
}
.usage-plus h1, .usage-plus h2, .usage-plus h3, .usage-plus p { margin: 0; }
.usage-plus h1 { font-size: 22px; font-weight: 650; letter-spacing: -.025em; line-height: 1.3; }
.usage-plus h2 { font-size: 15px; font-weight: 600; line-height: 1.4; }
.usage-plus h3 { font-size: 14px; font-weight: 600; }
.usage-plus p + p { margin-top: 4px; }
.usage-plus .usage-plus-muted { color: var(--up-muted); font-size: 12px; }
.usage-plus .usage-plus-header { display: flex; align-items: center; justify-content: space-between; gap: 12px 24px; flex-wrap: wrap; margin-bottom: 16px; }
.usage-plus .usage-plus-header > p { flex-basis: 100%; max-width: 90ch; }
.usage-plus .usage-plus-toolbar { display: flex; flex-wrap: wrap; gap: 6px; }
.usage-plus button { color: inherit; background: transparent; border: 1px solid var(--up-line); border-radius: 5px; padding: 5px 10px; font: inherit; cursor: pointer; }
.usage-plus button[aria-pressed="true"] { color: var(--up-accent); border-color: var(--up-accent); font-weight: 600; }
.usage-plus button:disabled { opacity: .55; cursor: default; }
.usage-plus button:focus-visible, .usage-plus summary:focus-visible, .usage-plus .usage-plus-table-scroll:focus-visible { outline: 2px solid var(--up-accent); outline-offset: 3px; }
.usage-plus .usage-plus-status { color: var(--up-muted); font-size: 12px; overflow-wrap: anywhere; margin: 4px 0; }
.usage-plus .usage-plus-warning { color: var(--up-text); border-left: 2px solid var(--up-accent); padding-left: 10px; }
.usage-plus .usage-plus-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 280px), 1fr)); gap: 20px 32px; }
.usage-plus .usage-plus-metrics { margin-top: 24px; padding: 20px 0; border-top: 1px solid var(--up-line); border-bottom: 1px solid var(--up-line); }
.usage-plus .usage-plus-metric, .usage-plus .usage-plus-account { min-width: 0; overflow-wrap: anywhere; }
.usage-plus .usage-plus-metric h2 { color: var(--up-muted); font-size: 12px; }
.usage-plus .usage-plus-metric > strong { display: block; font-size: 30px; font-weight: 600; letter-spacing: -.035em; line-height: 1.25; margin: 8px 0 12px; }
.usage-plus .usage-plus-section { margin-top: 28px; min-width: 0; }
.usage-plus .usage-plus-section > h2 { margin-bottom: 10px; }
.usage-plus .usage-plus-section > .usage-plus-grid { margin-top: 16px; }
.usage-plus .usage-plus-notes { margin-top: 40px; padding-top: 20px; border-top: 1px solid var(--up-line); }
.usage-plus .usage-plus-notes h2 { color: var(--up-muted); font-size: 13px; }
.usage-plus .usage-plus-account { border-top: 1px solid var(--up-line); padding-top: 12px; }
.usage-plus .usage-plus-account h3 { margin-bottom: 6px; }
.usage-plus .usage-plus-window { margin: 14px 0 10px; }
.usage-plus progress { display: block; width: 100%; height: 6px; margin: 8px 0; accent-color: var(--up-accent); }
.usage-plus .usage-plus-table-scroll { overflow-x: auto; }
.usage-plus table { border-collapse: collapse; width: 100%; font-size: 12px; }
.usage-plus caption { text-align: left; color: var(--up-muted); padding: 0 0 12px; }
.usage-plus th, .usage-plus td { text-align: right; vertical-align: top; padding: 12px 14px; border-bottom: 1px solid var(--up-line); min-width: 135px; }
.usage-plus thead th { color: var(--up-muted); font-size: 11px; font-weight: 500; padding-top: 8px; padding-bottom: 8px; white-space: nowrap; }
.usage-plus th:first-child { text-align: left; padding-left: 0; min-width: 170px; max-width: 260px; overflow-wrap: anywhere; }
.usage-plus tbody th { font-weight: 600; }
.usage-plus tbody th p { font-weight: 400; }
.usage-plus td:last-child, .usage-plus th:last-child { padding-right: 0; }
.usage-plus td > p { margin-top: 4px; }
.usage-plus td > p:first-child { margin-top: 0; }
.usage-plus .usage-plus-note { margin-top: 12px; max-width: 100ch; }
.usage-plus summary { cursor: pointer; width: fit-content; }
.usage-plus details > p { margin-top: 8px; }
@media (max-width: 600px) { .usage-plus { padding: 16px; } .usage-plus h1 { font-size: 20px; } }
`;
class UsageBoundary extends React.Component {
  constructor(props) { super(props); this.state = { failed: false }; }
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    return this.state.failed ? h('section', { role: 'alert', className: 'usage-plus' }, h('style', null, usageCSS),
      h('p', null, 'Usage+ 화면을 표시하지 못했습니다. 다른 화면에는 영향을 주지 않습니다.'),
      h('button', { className: 'usage-plus-button', onClick: () => this.setState({ failed: false }) }, '화면 다시 시도')) : this.props.children;
  }
}
function QueryStatus({ title, query }) {
  const data = query.data;
  const failed = query.error || data?.status === 'error';
  const stale = data?.status === 'stale';
  // Once data is healthy and settled, stay silent: caveats live in the notes at the bottom.
  if (!failed && !stale && !query.isLoading && !query.isFetching) return null;
  return h('div', { role: failed ? 'alert' : 'status', 'aria-live': 'polite', className: `usage-plus-status${failed || stale ? ' usage-plus-warning' : ''}` },
    `${title}: `,
    query.isLoading ? '불러오는 중…' : failed ? '조회 오류' : query.isFetching ? '새로고침 중…' : '오래된 데이터',
    failed ? ` · ${String(data?.error || query.error?.message || '조회할 수 없습니다.')}` : '',
    (failed || stale) && data ? ' · 마지막 데이터 표시 (오래된 데이터일 수 있음)' : '');
}
function AccountCards({ data }) {
  const accounts = (Array.isArray(data?.accounts) ? data.accounts : []).filter(a => a && a.status === 'ok');
  if (!accounts.length) return null;
  return h('section', { className: 'usage-plus-section', 'aria-label': '계정 한도' }, h('h2', null, '계정 한도 및 제공자 정보'),
    accounts.length ? h('div', { className: 'usage-plus-grid' }, ...accounts.map((a, i) => h('article', { key: `${a.provider}-${i}`, className: 'usage-plus-account' },
      h('h3', null, String(a.provider || '제공자 미확인')),
      h('p', null, ({ ok: '정상', unavailable: '조회 불가', unsupported: '미지원', error: '조회 오류' })[a.status] || '상태 미확인'),
      a.plan ? h('p', null, `플랜: ${a.plan}`) : null,
      h('p', { className: 'usage-plus-muted' }, `출처: ${a.source || '미확인'} · 수집 시각: ${date(a.fetched_at)}`),
      a.reason ? h('p', null, String(a.reason)) : null,
      a.status === 'ok' ? (Array.isArray(a.windows) ? a.windows : []).map((w, j) => h('div', { key: j, className: 'usage-plus-window' },
        h('strong', null, String(w.label || '한도')),
        h('p', null, `${w.scope === 'model' ? '모델 범위' : '계정 공유 범위'} · 사용 ${finite(w.used_percent) ? number(w.used_percent) + '%' : '미확인'} · 잔여 ${finite(w.remaining_percent) ? number(w.remaining_percent) + '%' : '미확인'}`),
        finite(w.used_percent) ? h('progress', { value: Math.max(0, Math.min(100, w.used_percent)), max: 100, 'aria-label': `${a.provider} ${w.label} 사용률` }) : null,
        h('p', { className: 'usage-plus-muted' }, `초기화: ${date(w.reset_at)}`), w.detail ? h('p', null, String(w.detail)) : null)) : h('p', null, '잔여 한도 미확인 — 0으로 간주하지 않습니다.'),
      ...(Array.isArray(a.details) ? a.details : []).map((d, j) => h('p', { key: `detail-${j}` }, `제공자 계정 정보: ${String(d)}`))))) : h('p', null, '표시할 계정 정보가 없습니다.'));
}
function ModelTable({ data }) {
  const models = Array.isArray(data?.models) ? data.models : [];
  const headers = ['모델 / 제공자', '호출 / 세션', '입력·출력 tokens', '캐시 / prompt tokens', '금액 (USD)', '요청 속도 / 시간'];
  return h('section', { className: 'usage-plus-section', 'aria-label': '모델별 사용량' }, h('h2', null, '모델별 사용량'),
    h('div', { className: 'usage-plus-table-scroll', tabIndex: 0, role: 'region', 'aria-label': '모델 표 가로 스크롤' },
      h('table', null,
        h('caption', null, '모델별 사용량 (기간 내 누적)'),
        h('thead', null, h('tr', null, ...headers.map(v => h('th', { key: v, scope: 'col' }, v)))),
        h('tbody', null, ...models.map((m, i) => h('tr', { key: `${m.provider}-${m.model}-${i}` },
          h('th', { scope: 'row' }, String(m.model), h('p', { className: 'usage-plus-muted' }, `${m.provider || '미확인'} · ${m.billing_mode || '결제 방식 미확인'}`)),
          h('td', null, `API 호출 ${number(m.api_calls)}`, h('p', null, `main ${number(m.main_calls)} / aux ${number(m.aux_calls)}`), h('p', null, `세션 ${number(m.sessions)}`)),
          h('td', null, `입력 (캐시 제외) ${number(m.input_tokens)}`, h('p', null, `출력 ${number(m.output_tokens)}`), h('p', null, `reasoning ${number(m.reasoning_tokens)}`)),
          h('td', null, `캐시 읽기 ${number(m.cache_read_tokens)}`, h('p', null, `캐시 쓰기 ${number(m.cache_write_tokens)}`), h('p', null, `prompt ${number(m.prompt_tokens)}`),
            h('p', null, `적중률 ${finite(m.cache_hit_pct) ? number(m.cache_hit_pct) + '%' : '미보고 / 미확인'} · ${m.cache_status || '상태 미확인'}`)),
          h('td', null, h('p', null, `실제 청구 ${money(m.actual_cost_usd)}`), h('p', null, `API 환산 ${money(m.api_value_usd)}`), h('p', { className: 'usage-plus-muted' }, `추정 비용 (실제 청구 아님) ${money(m.estimated_cost_usd)}`)),

          h('td', null, `평균 ${number(m.avg_tps)} tokens/s`, h('p', null, `평균 요청 시간 ${number(m.avg_latency_s)}초`), h('p', null, `timing 표본 ${number(m.timing_samples)}`))))))),
    !models.length ? h('p', null, '선택한 기간에 표시할 모델이 없습니다.') : null);
}
function PricingStatus({ data }) {
  if (!data) return null;
  return h('section', { className: 'usage-plus-section', 'aria-label': '공식 가격 자동 갱신' },
    h('h2', null, '공식 가격 자동 갱신'),
    h('p', null, `일일 갱신 · 저장 가격 ${number(data.price_count)}개 · ${data.stale ? '가격 오래됨 / 미확인' : '가격 최신'}`),
    ...Object.entries(data.sources || {}).map(([name, info]) => h('p', { key: name, className: info.status !== 'ok' || info.stale ? 'usage-plus-warning' : 'usage-plus-muted' },
      `${name}: ${info.status === 'ok' ? '정상' : '갱신 실패 — 마지막 정상 단가 유지'} · ${number(info.count)}개 · 마지막 정상 확인 ${date(info.checked_at)}${info.stale ? ' · 가격 오래됨' : ''}`)),
    data.notice ? h('p', { className: 'usage-plus-muted' }, String(data.notice)) : null);
}
function UsageNotes({ days, summary, accounts }) {
  const warnings = [...(Array.isArray(summary?.warnings) ? summary.warnings : []),
                    ...(Array.isArray(accounts?.warnings) ? accounts.warnings : [])];
  return h('section', { className: 'usage-plus-section usage-plus-notes', 'aria-label': '안내 및 참고사항' }, h('h2', null, '안내 및 참고사항'),
    h('p', { className: 'usage-plus-muted' }, `최근 ${days}일 안에 시작한 세션의 전체 생애 누적 사용량입니다 (session_started_at 기준). 정확한 일별 지출이 아닙니다. 기간 밖에서 발생한 해당 세션의 사용량도 포함됩니다.`),
    h('p', { className: 'usage-plus-muted' }, '입력 tokens는 캐시 제외 · prompt tokens는 입력 + 캐시 읽기 + 캐시 쓰기. 과거 캐시 0은 미보고일 수 있으며 0% 적중의 증거가 아닙니다.'),
    h('p', { className: 'usage-plus-muted' }, 'API 환산은 가격 스냅샷 기준이며 실제 청구액이 아닙니다. 미확인 모델 비용은 합산하지 않습니다.'),
    h('p', { className: 'usage-plus-muted' }, '계정 잔액·크레딧은 제공자가 전달한 값이며, 실제 청구 합계나 API 환산액이 아닙니다. 모델 범위와 계정 공유 한도를 구분합니다.'),
    h('details', { className: 'usage-plus-note usage-plus-muted' }, h('summary', null, '요청 속도와 표본 안내'), h('p', null, 'provider/model 전체 속도 (billing mode 공통 표본) = 출력 tokens / 전체 API 요청 시간 (first-token latency 포함). 순수 decode tokens/s가 아닙니다. timing 표본은 새로 수집한 요청에만 있습니다.')),
    summary?.fetched_at ? h('p', { className: 'usage-plus-muted' }, `사용량 수집 시각 ${date(summary.fetched_at)}${summary.period_days ? ` · 최근 ${summary.period_days}일` : ''}`) : null,
    accounts?.fetched_at ? h('p', { className: 'usage-plus-muted' }, `계정 수집 시각 ${date(accounts.fetched_at)}`) : null,
    ...warnings.map((w, i) => h('p', { key: `warning-${i}`, className: 'usage-plus-muted' }, String(w))));
}
function UsageView({ days, setDays, summary, accounts, refresh }) {
  const t = summary.data?.totals;
  return h('section', { className: 'usage-plus', 'aria-label': 'Usage+ 사용량 분석' }, h('style', null, usageCSS),
    h('header', { className: 'usage-plus-header' }, h('h1', null, 'Usage+ · 사용량과 계정 한도'),
      h('div', { role: 'group', 'aria-label': '기간 선택', className: 'usage-plus-toolbar' },
        ...[7, 30, 90].map(d => h('button', { key: d, type: 'button', 'aria-pressed': days === d, onClick: () => setDays(d) }, `${d}일`)),
        h('button', { type: 'button', className: 'usage-plus-button', disabled: summary.isFetching || accounts.isFetching, onClick: refresh }, '새로고침'))),
    h(QueryStatus, { title: '사용량', query: summary }), h(QueryStatus, { title: '계정', query: accounts }),
    t ? h('div', { className: 'usage-plus-grid usage-plus-metrics' },
      h('article', { className: 'usage-plus-metric' }, h('h2', null, 'API 호출'), h('strong', null, number(t.api_calls)), h('p', null, `입력 (캐시 제외) ${number(t.input_tokens)} · 출력 ${number(t.output_tokens)}`), h('p', null, `prompt ${number(t.prompt_tokens)} · 캐시 읽기 ${number(t.cache_read_tokens)}`)),
      h('article', { className: 'usage-plus-metric' }, h('h2', null, `API 환산 ${t.priced_models < t.models ? '부분 합계' : '합계'}`), h('strong', null, money(t.api_value_usd)), h('p', null, `가격 적용 ${number(t.priced_models)} / ${number(t.models)} 모델`))) : null,
    h(AccountCards, { data: accounts.data }), h(ModelTable, { data: summary.data }),
    h(PricingStatus, { data: summary.data?.pricing }),
    h(UsageNotes, { days, summary: summary.data, accounts: accounts.data }));
}
function DesktopUsage({ ctx }) {
  const [days, setDays] = React.useState(30);
  const profile = useValue(host.state.profile);
  const connection = useValue(host.state.gateway);
  const epoch = gatewayActivationEpoch();
  const query = (kind, interval) => useQuery({
    queryKey: ['usage-plus', profile, connection, epoch, kind, ...(kind === 'summary' ? [days] : [])],
    queryFn: async ({ signal }) => {
      const data = await ctx.rest(kind === 'summary' ? `/summary?days=${days}` : '/accounts', { timeoutMs: 45000 });
      // ctx.rest has no AbortSignal option. Never publish a response from an old route.
      if (signal.aborted || profile !== host.state.profile.get() || epoch !== gatewayActivationEpoch()) {
        const error = new Error('연결이 변경되어 이전 응답을 폐기했습니다.'); error.name = 'AbortError'; throw error;
      }
      return data;
    },
    refetchInterval: interval, staleTime: interval, retry: false, refetchOnWindowFocus: false
  });
  const summary = query('summary', 60000);
  const accounts = query('accounts', 300000);
  return h(UsageView, { days, setDays, summary, accounts, refresh: () => { void summary.refetch({ cancelRefetch: false }); void accounts.refetch({ cancelRefetch: false }); } });
}
export default {
  id: 'usage-plus', name: 'Usage+', version: '0.1.2', defaultEnabled: true,
  register(ctx) {
    ctx.registerMany([
      { id: 'page', area: ROUTES_AREA, data: { path: '/usage-plus' }, render: () => h(UsageBoundary, null, h(DesktopUsage, { ctx })) },
      { id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: '/usage-plus', label: 'Usage+', codicon: 'graph' } }
    ]);
  }
};
