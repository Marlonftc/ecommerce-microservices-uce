// Scenario 4: product-service with 1 or 3 replicas (set before the run by run_load.py).
// Same load in every run. Each iteration = product list + product detail
// (2 requests, anonymous, read only).
//   warm-up    20 s, 10 it/s fixed rate                      (excluded from the comparison)
//   sustained  60 s, SUSTAINED_RATE it/s fixed rate          (same offered load: latency comparison)
//   ramp       60 s from 0 to MAX_VUS users, then 30 s hold  (gradual: maximum throughput)
// The ramp is a closed model: each virtual user waits for its answer before the next
// request, so at most MAX_VUS requests are in flight. An open model past saturation
// made the service queue requests and need minutes to recover (see 09-carga.md).
import http from 'k6/http';
import { check } from 'k6';
import { BASE, catalogIds, newRunId, params, pick, summary } from './lib.js';

const SUSTAINED = Number(__ENV.SUSTAINED_RATE || 40);
const MAX_VUS = Number(__ENV.MAX_VUS || 40);
const WARMUP_S = 20;
const SUSTAINED_S = 60;
const RAMP_UP_S = 60;
const HOLD_S = 30;

const tags = (name) => ({ exec: 'visit', tags: { phase: name } });

export const options = {
  scenarios: {
    warmup: { ...tags('warmup'), executor: 'constant-arrival-rate', rate: 10, timeUnit: '1s',
      duration: `${WARMUP_S}s`, preAllocatedVUs: 10, maxVUs: 50 },
    sustained: { ...tags('sustained'), executor: 'constant-arrival-rate', startTime: `${WARMUP_S}s`,
      rate: SUSTAINED, timeUnit: '1s', duration: `${SUSTAINED_S}s`, preAllocatedVUs: 20, maxVUs: 100 },
    ramp: { ...tags('ramp'), executor: 'ramping-vus', startTime: `${WARMUP_S + SUSTAINED_S}s`, startVUs: 0,
      stages: [{ duration: `${RAMP_UP_S}s`, target: MAX_VUS }, { duration: `${HOLD_S}s`, target: MAX_VUS }],
      gracefulRampDown: '5s' },
  },
  thresholds: {
    'http_req_duration{phase:sustained}': ['p(95)<500'],
    'http_req_failed{phase:sustained}': ['rate<0.01'],
    'http_reqs{phase:sustained}': ['count>=0'],
    'dropped_iterations{phase:sustained}': ['count>=0'],
    'http_req_duration{phase:ramp}': ['p(95)<500'],
    'http_req_failed{phase:ramp}': ['rate<0.01'],
    'http_reqs{phase:ramp}': ['count>=0'],
    checks: ['rate>0.99'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'max'],
};

export function setup() {
  const runId = newRunId('scale');
  return { runId, productIds: catalogIds(runId) };
}

export function visit(data) {
  // The phase tag of the scenario is kept: params() would overwrite it with "main"
  const tag = (name) => {
    const p = params(data.runId, name);
    delete p.tags.phase;
    return p;
  };
  const list = http.get(`${BASE}/api/products?limit=20`, tag('list'));
  check(list, { 'list: 200': (r) => r.status === 200 });
  const id = pick(data.productIds);
  const detail = http.get(`${BASE}/api/products/${id}`, tag('detail'));
  check(detail, { 'detail: 200': (r) => r.status === 200 && r.json('id') === id });
}

function phaseLine(m, name, seconds) {
  const reqs = m[`http_reqs{phase:${name}}`];
  const dur = m[`http_req_duration{phase:${name}}`];
  const failed = m[`http_req_failed{phase:${name}}`];
  const dropped = m[`dropped_iterations{phase:${name}}`];
  if (!reqs || !dur) return null;
  return `${name}: ${reqs.values.count} req (${(reqs.values.count / seconds).toFixed(1)} req/s), ` +
    `avg ${dur.values.avg.toFixed(1)} ms, p95 ${dur.values['p(95)'].toFixed(1)} ms, ` +
    `errors ${failed ? (failed.values.rate * 100).toFixed(2) : '0.00'} %` +
    (dropped ? `, dropped iterations ${dropped.values.count}` : '');
}

export function handleSummary(data) {
  data.scale_config = { sustained_rate_it_s: SUSTAINED, max_vus: MAX_VUS, warmup_s: WARMUP_S,
    sustained_s: SUSTAINED_S, ramp_up_s: RAMP_UP_S, hold_s: HOLD_S };
  return summary('scale', data, [
    (m) => phaseLine(m, 'sustained', SUSTAINED_S),
    (m) => phaseLine(m, 'ramp', RAMP_UP_S + HOLD_S),
  ]);
}
