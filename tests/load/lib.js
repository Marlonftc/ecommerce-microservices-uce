// Shared helpers for the k6 load tests.
// Every request goes to the gateway (BASE_URL) and carries an X-Request-ID that
// starts with the run id, so the gateway log can be filtered per run.
// Credentials come only from environment variables; customer passwords are
// random and live only in memory. Nothing secret is printed or saved.
import http from 'k6/http';
import { check, fail, sleep } from 'k6';
import { randomBytes } from 'k6/crypto';
import exec from 'k6/execution';

export const BASE = __ENV.BASE_URL || 'http://gateway';
export const RESULTS_DIR = __ENV.RESULTS_DIR || '/results';
const JSON_HEADERS = { 'Content-Type': 'application/json' };

// 2xx answers, including 202 (RESERVING / PAYMENT_PENDING), are valid; anything else counts as failed
http.setResponseCallback(http.expectedStatuses(200, 201, 202, 204));

export function hex(bytes) {
  return Array.from(new Uint8Array(randomBytes(bytes)), (b) => b.toString(16).padStart(2, '0')).join('');
}

export function newRunId(scenario) {
  return __ENV.RUN_ID || `${scenario}-${Date.now().toString(36)}-${hex(2)}`;
}

let requestCounter = 0;

// Request parameters: tags for per-endpoint metrics, request id, optional token and headers
export function params(runId, name, { token = null, headers = {}, phase = 'main', expected = null } = {}) {
  requestCounter += 1;
  const h = { ...JSON_HEADERS, ...headers, 'X-Request-ID': `${runId}-${exec.vu.idInTest}-${requestCounter}`.slice(0, 64) };
  if (token) h.Authorization = `Bearer ${token}`;
  const p = { headers: h, tags: { name, phase } };
  if (expected) p.responseCallback = http.expectedStatuses(...expected);
  return p;
}

// Login respecting the gateway limit (10/min, burst 5): on 429 it waits Retry-After and repeats.
export function login(runId, email, password) {
  for (let attempt = 1; attempt <= 20; attempt++) {
    const r = http.post(`${BASE}/api/auth/login`, JSON.stringify({ email, password }),
      params(runId, 'login', { phase: 'prep', expected: [200, 429] }));
    if (r.status === 200) return r.json('access_token');
    if (r.status !== 429) fail(`login failed with HTTP ${r.status}`);
    sleep(Number(r.headers['Retry-After'] || 6) + 0.5);
  }
  fail('login still limited after 20 attempts');
}

export function adminToken(runId) {
  if (!__ENV.ADMIN_EMAIL || !__ENV.ADMIN_PASSWORD) fail('ADMIN_EMAIL and ADMIN_PASSWORD must be set');
  return login(runId, __ENV.ADMIN_EMAIL, __ENV.ADMIN_PASSWORD);
}

// A customer only for this run (register is not rate limited)
export function newCustomer(runId, label) {
  const email = `${runId}-${label}@example.com`.toLowerCase();
  const password = hex(16);
  const r = http.post(`${BASE}/api/auth/register`,
    JSON.stringify({ email, full_name: 'Cliente de carga', password }),
    params(runId, 'register', { phase: 'prep' }));
  if (r.status !== 201) fail(`register failed with HTTP ${r.status}`);
  return { id: r.json('id'), token: login(runId, email, password) };
}

// LOAD- products with large stock, created through the admin API of the gateway
export function createProducts(runId, token, count, stock) {
  const ids = [];
  const tag = runId.replace(/[^A-Za-z0-9]/g, '').slice(-12).toUpperCase();
  for (let i = 1; i <= count; i++) {
    const sku = `LOAD-${tag}-${i}`.slice(0, 32);
    const created = http.post(`${BASE}/api/products`, JSON.stringify({
      sku, name: `LOAD- ${runId} producto ${i}`.slice(0, 120), category: 'carga', price: '10.00',
    }), params(runId, 'admin_product', { token, phase: 'prep' }));
    if (created.status !== 201) fail(`POST /api/products returned HTTP ${created.status} (admin route required)`);
    const id = created.json('id');
    const adjusted = http.post(`${BASE}/api/inventory/${id}/adjustments`,
      JSON.stringify({ delta: stock, reason: `load test ${runId}` }),
      params(runId, 'admin_stock', { token, phase: 'prep', headers: { 'Idempotency-Key': `${runId}-stock-${id}` } }));
    if (adjusted.status !== 201) fail(`POST /api/inventory/${id}/adjustments returned HTTP ${adjusted.status}`);
    ids.push(id);
  }
  return ids;
}

// Teardown: the products of this run are deactivated (soft delete), others are untouched
export function deactivateProducts(runId, token, ids) {
  for (const id of ids) {
    http.del(`${BASE}/api/products/${id}`, null, params(runId, 'admin_cleanup', { token, phase: 'prep' }));
  }
}

export function catalogIds(runId) {
  const r = http.get(`${BASE}/api/products?limit=100`, params(runId, 'list', { phase: 'prep' }));
  if (r.status !== 200) fail(`GET /api/products returned HTTP ${r.status}`);
  return r.json('items').map((p) => p.id);
}

export function pick(list) {
  return list[Math.floor(Math.random() * list.length)];
}

// One catalogue visit: list, product detail and stock (3 public requests)
export function browseOnce(data, publicRequests) {
  const r1 = http.get(`${BASE}/api/products?limit=20&offset=${Math.floor(Math.random() * 3) * 5}`,
    params(data.runId, 'list'));
  check(r1, { 'list: 200 with items': (r) => r.status === 200 && r.json('items').length > 0 });
  const id = pick(data.productIds);
  const r2 = http.get(`${BASE}/api/products/${id}`, params(data.runId, 'detail'));
  check(r2, { 'detail: 200 same product': (r) => r.status === 200 && r.json('id') === id });
  const ids = [pick(data.productIds), pick(data.productIds), pick(data.productIds)];
  const r3 = http.get(`${BASE}/api/inventory?ids=${ids.join(',')}`, params(data.runId, 'stock'));
  check(r3, { 'stock: 200 with entries': (r) => r.status === 200 && Array.isArray(r.json()) });
  if (publicRequests) publicRequests.add(3, { kind: 'browse' });
}

// --- summary ---------------------------------------------------------------------------

function fmt(v, digits = 1) {
  return v === undefined || v === null ? '-' : Number(v).toFixed(digits);
}

export function summary(scenario, data, extraLines = []) {
  const runId = (data.setup_data && data.setup_data.runId) || 'unknown';
  // setup_data holds tokens: it is never written to disk
  delete data.setup_data;
  const m = data.metrics;
  const lines = [`run ${runId} (${scenario})`];
  if (m.http_reqs) lines.push(`http_reqs: ${m.http_reqs.values.count} (${fmt(m.http_reqs.values.rate)} req/s)`);
  for (const key of Object.keys(m).sort()) {
    if (key.startsWith('http_req_duration{') || key === 'http_req_duration') {
      const v = m[key].values;
      lines.push(`${key}: avg ${fmt(v.avg)} ms, p95 ${fmt(v['p(95)'])} ms, max ${fmt(v.max)} ms`);
    }
  }
  for (const key of Object.keys(m).sort()) {
    if (key.startsWith('http_req_failed')) lines.push(`${key}: ${fmt(m[key].values.rate * 100, 2)} %`);
  }
  if (m.checks) lines.push(`checks: ${fmt(m.checks.values.rate * 100, 2)} % passed`);
  if (m.dropped_iterations) lines.push(`dropped_iterations: ${m.dropped_iterations.values.count}`);
  lines.push(...extraLines.map((f) => f(m)).filter(Boolean));
  const failed = Object.entries(m).filter(([, v]) => v.thresholds && Object.values(v.thresholds).some((t) => !t.ok))
    .map(([k]) => k);
  lines.push(failed.length ? `THRESHOLDS NOT MET: ${failed.join(', ')}` : 'thresholds: all met');
  const report = { run_id: runId, scenario, generated_at: new Date().toISOString(), ...data };
  return {
    stdout: `\n${lines.join('\n')}\n`,
    [`${RESULTS_DIR}/${runId}.json`]: JSON.stringify(report, null, 2),
  };
}
