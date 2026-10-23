const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const base = path.resolve(__dirname, '..');
const core = process.env.HERMES_CORE_DIR || path.resolve(base, '../../hermes-agent');
const req = createRequire(path.join(core, 'package.json'));
const React = req('react');
const { createRoot } = req('react-dom/client');
const { QueryClient, QueryClientProvider, useQuery } = req('@tanstack/react-query');
const { JSDOM } = req('jsdom');
const dom = new JSDOM('<!doctype html><html><body></body></html>');
global.window = dom.window;
global.document = dom.window.document;
global.IS_REACT_ACT_ENVIRONMENT = true;
const { act } = React;
const summary = () => ({ status: 'ok', fetched_at: 1700000000, period_days: 30, period_basis: 'session_started_at', models: [{ model: '<img src=x onerror=alert(1)>', provider: 'fixture', billing_mode: 'subscription', api_calls: 3, main_calls: 2, aux_calls: 1, sessions: 1, input_tokens: 100, output_tokens: 20, cache_read_tokens: 40, cache_write_tokens: 10, prompt_tokens: 150, reasoning_tokens: 5, cache_hit_pct: null, cache_status: 'unreported', actual_cost_usd: 0, estimated_cost_usd: null, api_value_usd: null, pricing_source: null, pricing_version: null, avg_tps: 2, avg_latency_s: 10, timing_samples: 1 }], totals: { api_calls: 3, input_tokens: 100, output_tokens: 20, prompt_tokens: 150, cache_read_tokens: 40, api_value_usd: null, priced_models: 0, models: 1 }, warnings: ['테스트 경고'] });
const accounts = () => ({ status: 'ok', fetched_at: 1700000000, accounts: [{ provider: 'unsupported-provider', status: 'unsupported', source: null, fetched_at: null, plan: null, windows: [], details: [], reason: '공급자 미지원' }, { provider: 'working-provider', status: 'ok', source: 'provider', fetched_at: 1700000000, plan: 'Pro', windows: [{ label: '5시간', used_percent: 25, remaining_percent: 75, reset_at: '2026-01-01T00:00:00Z', scope: 'account', detail: '공유 한도' }], details: ['제공자 크레딧 잔액: USD 12'], reason: null }], warnings: [] });
function atom(value) { const subs = new Set(); return { get: () => value, subscribe: cb => (subs.add(cb), () => subs.delete(cb)), set(v) { value = v; subs.forEach(cb => cb()); } }; }
function loadDesktop(fetcher) {
  const contributions = [], options = [];
  const host = { state: { profile: atom('fixture-profile'), gateway: atom('open') } };
  const sdk = { host, gatewayActivationEpoch: () => host.epoch || 0, useValue: a => React.useSyncExternalStore(a.subscribe, a.get, a.get), useQuery: o => { options.push(o); return useQuery(o); }, ROUTES_AREA: 'routes', SIDEBAR_NAV_AREA: 'nav' };
  const context = vm.createContext({ React, ...sdk, console, URL, AbortController, setTimeout, clearTimeout, setInterval, clearInterval });
  const source = fs.readFileSync(path.join(base, 'desktop/plugin.js'), 'utf8').replace(/^import .*?;?\s*$/gm, '').replace('export default', 'globalThis.plugin =');
  vm.runInContext(source, context);
  context.plugin.register({ rest: fetcher, registerMany: c => contributions.push(...c) });
  const route = contributions.find(c => c.area === 'routes');
  return { Page: () => route.render(), contributions, options, plugin: context.plugin, host };
}
async function mount(Page, query = false) {
  const container = document.createElement('div'); document.body.append(container);
  const root = createRoot(container);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  await act(async () => { root.render(query ? React.createElement(QueryClientProvider, { client }, React.createElement(Page)) : React.createElement(Page)); });
  await flush();
  return { container, root, client, async close() { await act(async () => root.unmount()); client.clear(); container.remove(); } };
}
async function flush() { await act(async () => { await new Promise(r => setTimeout(r, 20)); }); }
function text(ui) { return ui.container.textContent; }
function button(ui, label) { return [...ui.container.querySelectorAll('button')].find(b => b.textContent.includes(label)); }
async function click(ui, label) { await act(async () => button(ui, label).click()); await flush(); }
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
function loadDashboard(fetcher) {
  const registrations = [], slots = [], timers = new Map(); let id = 0;
  const context = vm.createContext({ window: { __HERMES_PLUGIN_SDK__: { React, fetchJSON: fetcher }, __HERMES_PLUGINS__: { register: (...args) => registrations.push(args), registerSlot: (...args) => slots.push(args) } }, console, URL, AbortController, setInterval: (fn, ms) => { timers.set(++id, { fn, ms }); return id; }, clearInterval: id => timers.delete(id), setTimeout, clearTimeout });
  vm.runInContext(fs.readFileSync(path.join(base, 'dashboard/dist/index.js'), 'utf8'), context);
  return { Page: registrations[0]?.[1], registrations, slots, timers };
}

test('dashboard adds its own page and analytics section, polls safely, and renders account/money semantics', async () => {
  const calls = [];
  const d = loadDashboard(async (url, options) => { calls.push({ url, options }); return url.includes('/summary') ? summary() : accounts(); });
  assert.equal(d.registrations[0][0], 'usage-plus');
  assert.deepEqual(d.slots.map(s => s.slice(0, 2)), [['usage-plus', 'analytics:top']]);
  assert.equal(d.slots[0][2], d.Page);
  const ui = await mount(d.Page);
  try {
    assert.equal(calls.length, 2);
    assert.ok(calls.every(c => c.url.startsWith('/api/plugins/usage-plus/')));
    assert.match(text(ui), /실제 청구.*\$0\.00/s); assert.match(text(ui), /API 환산.*미확인/s);
    assert.doesNotMatch(text(ui), /공급자 미지원/); assert.match(text(ui), /working-provider/);
    const unsupported = [...ui.container.querySelectorAll('article')].find(a => a.textContent.includes('unsupported-provider'));
    assert.equal(unsupported, undefined);
    assert.match(text(ui), /캐시 쓰기/); assert.match(text(ui), /timing 표본 1/);
    assert.equal(ui.container.querySelector('img'), null);
    assert.deepEqual([...d.timers.values()].map(t => t.ms).sort((a,b) => a-b), [60000, 300000]);
    await click(ui, '7일'); assert.ok(calls.some(c => c.url.endsWith('days=7')));
    await click(ui, '90일'); assert.ok(calls.some(c => c.url.endsWith('days=90')));
    const before = calls.length; await click(ui, '새로고침'); assert.equal(calls.length, before + 2);
  } finally { await ui.close(); }
  assert.equal(d.timers.size, 0);
});

test('dashboard retains global errors but hides failed account cards', async () => {
  let fail = false; const pending = deferred();
  const d = loadDashboard(async url => {
    if (url.includes('/summary')) { if (fail) throw new Error('테스트 연결 오류'); return pending.promise; }
    const a = accounts(); a.accounts[0].status = 'error'; a.accounts[0].reason = '개별 조회 실패'; return a;
  });
  const ui = await mount(d.Page);
  try {
    assert.match(text(ui), /불러오는 중/); assert.doesNotMatch(text(ui), /개별 조회 실패/); assert.match(text(ui), /working-provider/);
    await act(async () => { pending.resolve({ ...summary(), status: 'stale' }); }); await flush();
    assert.match(text(ui), /오래된 데이터/);
    fail = true; await click(ui, '새로고침');
    assert.match(text(ui), /테스트 연결 오류/); assert.match(text(ui), /마지막 데이터/); assert.match(text(ui), /API 호출 3/);
  } finally { await ui.close(); }
});

test('dashboard ignores late filter results, aborts on cleanup and prevents polling overlap', async () => {
  const pending = [], calls = [];
  const d = loadDashboard((url, options) => {
    calls.push({ url, options });
    if (!url.includes('/summary')) return Promise.resolve(accounts());
    const p = deferred(); pending.push({ ...p, url, options }); return p.promise;
  });
  const ui = await mount(d.Page);
  try {
    const tick = [...d.timers.values()].find(t => t.ms === 60000);
    await act(async () => { tick.fn(); tick.fn(); }); assert.equal(pending.length, 1);
    await click(ui, '7일'); assert.equal(pending.length, 2); assert.equal(pending[0].options.signal.aborted, true);
    await act(async () => pending[1].resolve({ ...summary(), models: [{ ...summary().models[0], model: 'NEW-7-DAY' }] })); await flush();
    await act(async () => pending[0].resolve({ ...summary(), models: [{ ...summary().models[0], model: 'OLD-30-DAY' }] })); await flush();
    assert.match(text(ui), /NEW-7-DAY/); assert.doesNotMatch(text(ui), /OLD-30-DAY/);
  } finally { await ui.close(); }
  assert.equal(d.timers.size, 0); assert.ok(calls.every(c => c.options.signal.aborted));
});

test('desktop filters, refresh and profile/connection changes discard late responses', async () => {
  const pending = [], calls = [];
  const d = loadDesktop(p => {
    calls.push(p); if (p === '/accounts') return Promise.resolve(accounts());
    const request = deferred(); pending.push(request); return request.promise;
  });
  const ui = await mount(d.Page, true);
  try {
    assert.match(text(ui), /불러오는 중/);
    await click(ui, '7일'); assert.equal(pending.length, 2);
    await act(async () => pending[1].resolve({ ...summary(), models: [{ ...summary().models[0], model: 'SEVEN' }] })); await flush();
    assert.match(text(ui), /SEVEN/);
    await act(async () => { d.host.state.profile.set('second-profile'); }); await flush();
    assert.doesNotMatch(text(ui), /SEVEN/); assert.equal(pending.length, 3);
    await act(async () => pending[2].resolve({ ...summary(), models: [{ ...summary().models[0], model: 'SECOND-PROFILE' }] })); await flush();
    await act(async () => pending[0].resolve({ ...summary(), models: [{ ...summary().models[0], model: 'OLD-PROFILE' }] })); await flush();
    assert.match(text(ui), /SECOND-PROFILE/); assert.doesNotMatch(text(ui), /OLD-PROFILE/);
    await act(async () => { d.host.epoch = 1; d.host.state.gateway.set('connecting'); }); await flush();
    assert.doesNotMatch(text(ui), /SECOND-PROFILE/); assert.equal(pending.length, 4);
    await act(async () => pending[3].resolve({ ...summary(), models: [{ ...summary().models[0], model: 'NEW-CONNECTION' }] })); await flush();
    assert.match(text(ui), /NEW-CONNECTION/);
    await click(ui, '새로고침'); assert.equal(pending.length, 5);
    const count = calls.length; await click(ui, '새로고침'); assert.equal(calls.length, count);
    await act(async () => pending[4].reject(new Error('데스크톱 조회 실패'))); await flush();
    assert.match(text(ui), /데스크톱 조회 실패/); assert.match(text(ui), /NEW-CONNECTION/);
    await click(ui, '90일'); assert.ok(calls.includes('/summary?days=90')); assert.doesNotMatch(text(ui), /NEW-CONNECTION/);
  } finally { await ui.close(); }
});

test('both surfaces preserve known zero versus unknown money and label partial API coverage', async () => {
  for (const surface of ['desktop', 'dashboard']) {
    const s = summary(); s.totals.api_value_usd = 0; s.totals.models = 2; s.totals.priced_models = 1;
    s.models[0] = { ...s.models[0], actual_cost_usd: null, estimated_cost_usd: 0, api_value_usd: 0, cache_hit_pct: 0, cache_status: 'reported', pricing_source: 'fixture-snapshot', pricing_version: 'test-v1', avg_tps: null };
    const a = accounts(); a.accounts[1].windows[0].remaining_percent = null;
    const d = surface === 'desktop' ? loadDesktop(async p => p.includes('/summary') ? s : a) : loadDashboard(async p => p.includes('/summary') ? s : a);
    const ui = await mount(d.Page, surface === 'desktop');
    try {
      const cells = [...ui.container.querySelectorAll('td')].map(c => c.textContent);
      const moneyCell = cells.find(c => c.includes('실제 청구'));
      assert.match(moneyCell, /^실제 청구 미확인API 환산 \$0\.00추정 비용 \(실제 청구 아님\) \$0\.00$/);
      assert.match(text(ui), /API 환산 부분 합계/); assert.match(text(ui), /가격 적용 1 \/ 2/);
      assert.match(text(ui), /적중률 0%/); assert.match(text(ui), /잔여 미확인/);
      assert.doesNotMatch(text(ui), /fixture-snapshot|test-v1/);
      assert.match(text(ui), /평균 미확인 tokens\/s/);
    } finally { await ui.close(); }
  }
});

test('both surfaces show daily official price freshness and source failures', async () => {
  for (const surface of ['desktop', 'dashboard']) {
    const s = summary();
    s.pricing = { price_count: 10, stale: true, sources: { official: { status: 'error', checked_at: 1700000000, count: 10, stale: true } } };
    const fetcher = async p => p.includes('/summary') ? s : accounts();
    const d = surface === 'desktop' ? loadDesktop(fetcher) : loadDashboard(fetcher);
    const ui = await mount(d.Page, surface === 'desktop');
    try {
      assert.match(text(ui), /공식 가격 자동 갱신/);
      assert.match(text(ui), /마지막 정상 단가 유지/);
      assert.match(text(ui), /official/);
      assert.match(text(ui), /가격 오래됨/);
    } finally { await ui.close(); }
  }
});

test('both surfaces omit unavailable accounts and the empty account section', async () => {
  for (const surface of ['desktop', 'dashboard']) {
    const a = accounts();
    a.accounts = ['unsupported', 'unavailable', 'error', 'unknown'].map(status => ({ ...a.accounts[0], provider: 'hidden-' + status, status }));
    const fetcher = async p => p.includes('/summary') ? summary() : a;
    const d = surface === 'desktop' ? loadDesktop(fetcher) : loadDashboard(fetcher);
    const ui = await mount(d.Page, surface === 'desktop');
    try {
      assert.equal(Boolean(ui.container.querySelector('[aria-label="계정 한도"]')), false);
      assert.doesNotMatch(text(ui), /hidden-|표시할 계정 정보가 없습니다/);
      assert.match(text(ui), /모델별 사용량/);
    } finally { await ui.close(); }
  }
});

test('both surfaces remove the entire price-source column but preserve amounts', async () => {
  for (const surface of ['desktop', 'dashboard']) {
    const s = summary();
    s.models[0] = { ...s.models[0], api_value_usd: 12.34,
      pricing_source_url: 'https://prices.example.test/models',
      pricing_source: 'https://prices.example.test/models | long pricing conditions',
      pricing_version: 'snapshot-version', pricing_basis: 'native_reference' };
    const fetcher = async p => p.includes('/summary') ? s : accounts();
    const d = surface === 'desktop' ? loadDesktop(fetcher) : loadDashboard(fetcher);
    const ui = await mount(d.Page, surface === 'desktop');
    try {
      assert.equal(ui.container.querySelectorAll('thead th').length, 6);
      assert.equal(ui.container.querySelectorAll('tbody a').length, 0);
      assert.doesNotMatch(text(ui), /가격 출처|원제공자 가격|가격 버전|https:|snapshot-version|long pricing conditions/);
      assert.match(text(ui), /API 환산 \$12\.34/);
    } finally { await ui.close(); }
  }
});

test('both surfaces provide scoped responsive hierarchy without hiding financial or health caveats', async () => {
  for (const surface of ['desktop', 'dashboard']) {
    const s = summary();
    s.status = 'stale';
    s.pricing = { price_count: 10, stale: true, sources: { official: { status: 'error', checked_at: 1700000000, count: 10, stale: true } } };
    const fetcher = async p => p.includes('/summary') ? s : accounts();
    const d = surface === 'desktop' ? loadDesktop(fetcher) : loadDashboard(fetcher);
    const ui = await mount(d.Page, surface === 'desktop');
    try {
      const page = ui.container.querySelector('.usage-plus');
      assert.ok(page, 'view has a scoped theme root');
      const styles = page.querySelectorAll('style');
      assert.equal(styles.length, 1, 'one constant stylesheet, not per-row styles');
      const css = styles[0].textContent;
      for (const token of ['--ui-text-primary', '--ui-text-secondary', '--ui-stroke-secondary', '--ui-accent', '--color-foreground', '--vscode-foreground']) assert.ok(css.includes(token));
      assert.match(css, /font-family:\s*inherit/);
      assert.match(css, /font-variant-numeric:\s*tabular-nums/);
      assert.match(css, /:focus-visible/);
      assert.match(css, /overflow-x:\s*auto/);
      assert.match(css, /repeat\(auto-fit,\s*minmax/);
      assert.doesNotMatch(css, /animation|transition|gradient|backdrop-filter|@import|url\(/);
      assert.doesNotMatch(css, /#[0-9a-f]{3,8}\b/i, 'fallback colors follow the host or browser system theme');
      assert.ok(page.querySelector('header.usage-plus-header h1'));
      assert.equal(page.querySelectorAll('.usage-plus-metric > strong').length, 2);
      assert.equal(page.querySelector('[aria-pressed="true"]').textContent, '30일');
      const scroll = page.querySelector('.usage-plus-table-scroll');
      assert.equal(scroll.tabIndex, 0);
      assert.equal(scroll.getAttribute('role'), 'region');
      assert.ok(scroll.getAttribute('aria-label'));
      assert.ok(scroll.querySelector('caption'));
      assert.equal(scroll.querySelectorAll('th[scope="col"]').length, 6);
      assert.equal(scroll.querySelectorAll('th[scope="row"]').length, 1);
      assert.equal(page.querySelectorAll('[style]').length, 0, 'presentation uses shared CSS');
      const visibleText = [...page.querySelectorAll('p, caption, [role="status"], [role="alert"]')]
        .filter(el => !el.closest('details:not([open])')).map(el => el.textContent).join('\n');
      for (const warning of ['정확한 일별 지출이 아닙니다', '실제 청구액이 아닙니다', '미확인 모델 비용은 합산하지 않습니다', '0% 적중의 증거가 아닙니다', '실제 청구 합계나 API 환산액이 아닙니다', '갱신 실패', '가격 오래됨', '오래된 데이터', '테스트 경고']) assert.ok(visibleText.includes(warning), warning);
    } finally { await ui.close(); }
  }
});

test('both surfaces cache numeric formatters while preserving locale and unknown semantics', () => {
  for (const file of ['desktop/plugin.js', 'dashboard/dist/index.js']) {
    const source = fs.readFileSync(path.join(base, file), 'utf8');
    let constructions = 0;
    const context = vm.createContext({ React, Intl: { NumberFormat: class extends Intl.NumberFormat {
      constructor(...args) { super(...args); constructions++; }
    } } });
    vm.runInContext(source.slice(source.indexOf('// Kept dependency-free'), source.indexOf('class UsageBoundary')) + '\nglobalThis.format = { number, money, date };', context);
    assert.equal(constructions, 2, 'number and money formatters are created once at module scope');
    for (let repeat = 0; repeat < 3; repeat++) {
      for (const value of [null, undefined, NaN, Infinity, -Infinity, '12', 0, -0, 1234567.891, -0.000123, 12.3456789]) {
        const known = typeof value === 'number' && Number.isFinite(value);
        assert.equal(context.format.number(value), known ? value.toLocaleString('ko-KR', { maximumFractionDigits: 2 }) : '미확인');
        assert.equal(context.format.money(value), known ? `$${value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 6 })}` : '미확인');
      }
    }
    assert.equal(constructions, 2, 'formatting cells never constructs more formatters');
    for (const value of [null, undefined, '', 'invalid', NaN, 0, 1700000000, '2026-01-01T00:00:00Z', '1700000000']) {
      const d = new Date(typeof value === 'number' ? value * 1000 : value);
      assert.equal(context.format.date(value), value == null || Number.isNaN(d.getTime()) ? '미확인' : d.toLocaleString('ko-KR'));
    }
  }
});

test('shared view stays identical and desktop imports only approved modules', () => {
  const desktop = fs.readFileSync(path.join(base, 'desktop/plugin.js'), 'utf8');
  const dashboard = fs.readFileSync(path.join(base, 'dashboard/dist/index.js'), 'utf8');
  const start = '// Kept dependency-free';
  assert.equal(desktop.slice(desktop.indexOf(start), desktop.indexOf('function DesktopUsage')).trim(), dashboard.slice(dashboard.indexOf(start), dashboard.indexOf('// Each resource owns')).trim());
  const modules = [...desktop.matchAll(/^import .* from '([^']+)'/gm)].map(m => m[1]);
  assert.ok(modules.every(m => ['@hermes/plugin-sdk', 'react', 'react/jsx-runtime'].includes(m)));
  assert.doesNotMatch(desktop + dashboard, /dangerouslySetInnerHTML|\.innerHTML|https?:\/\/|localStorage|sessionStorage/);
});

test('both surfaces isolate render failures in a React error boundary', async () => {
  const broken = { ...summary(), models: [null] };
  for (const surface of ['desktop', 'dashboard']) {
    const fetcher = async p => p.includes('/summary') ? broken : accounts();
    const d = surface === 'desktop' ? loadDesktop(fetcher) : loadDashboard(fetcher);
    const errors = []; const previous = console.error; console.error = (...args) => errors.push(args);
    let ui;
    try { ui = await mount(d.Page, surface === 'desktop'); assert.match(text(ui), /다른 화면에는 영향을 주지 않습니다/); assert.ok(button(ui, '화면 다시 시도')); assert.ok(errors.length); }
    finally { console.error = previous; if (ui) await ui.close(); }
  }
});

test('desktop registers a standalone page and renders the real schema without unsafe money assumptions', async () => {
  const calls = [];
  const d = loadDesktop(async p => { calls.push(p); return p.startsWith('/summary') ? summary() : accounts(); });
  assert.equal(d.plugin.id, 'usage-plus'); assert.equal(d.plugin.defaultEnabled, true);
  assert.equal(d.contributions.find(c => c.area === 'routes').data.path, '/usage-plus');
  assert.equal(d.contributions.find(c => c.area === 'nav').data.label, 'Usage+');
  const ui = await mount(d.Page, true);
  try {
    assert.ok(calls.includes('/summary?days=30')); assert.ok(calls.includes('/accounts'));
    assert.match(text(ui), /실제 청구.*\$0\.00/s);
    assert.match(text(ui), /API 환산.*미확인/s);
    assert.match(text(ui), /가격 적용.*0\s*\/\s*1/s);
    assert.match(text(ui), /시작한 세션/); assert.match(text(ui), /정확한 일별 지출이 아/);
    assert.match(text(ui), /first-token latency/); assert.match(text(ui), /새로 수집/);
    assert.match(text(ui), /미보고/); assert.doesNotMatch(text(ui), /공급자 미지원/);
    assert.match(text(ui), /working-provider/); assert.match(text(ui), /75%/);
    assert.match(text(ui), /<img src=x/); assert.equal(ui.container.querySelector('img'), null);
    assert.match(text(ui), /캐시 쓰기/); assert.match(text(ui), /reasoning/);
    assert.ok(d.options.some(o => o.refetchInterval >= 60000 && o.queryKey.includes('summary')));
    assert.ok(d.options.some(o => o.refetchInterval >= 300000 && o.queryKey.includes('accounts')));
    assert.ok(d.options.every(o => o.queryKey.includes('fixture-profile') && o.queryKey.includes('open')));
  } finally { await ui.close(); }
});
