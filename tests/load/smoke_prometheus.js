// Integration only: exactly eight anonymous catalogue GETs from one VU.
// Never creates products, customers, orders, or stock adjustments.
import http from 'k6/http';
import { check, sleep } from 'k6';

const runId = __ENV.RUN_ID;
if (!runId || !/^smoke-[A-Za-z0-9_-]+$/.test(runId)) throw new Error('A unique smoke RUN_ID is required');
export const options = {
  scenarios: { integration: { executor: 'shared-iterations', vus: 1, iterations: 8, maxDuration: '1m', gracefulStop: '3s' } },
  thresholds: { http_req_failed: ['rate==0'], checks: ['rate==1'], http_reqs: ['count==8'] },
  summaryTrendStats: ['avg', 'min', 'med', 'p(95)', 'max'],
};

export default function () {
  const response = http.get(`${__ENV.BASE_URL || 'http://gateway'}/api/products?limit=1`, {
    timeout: '2s', redirects: 0, responseCallback: http.expectedStatuses(200),
    headers: { 'X-Request-ID': `${runId}-${__ITER}` },
    tags: { name: 'catalogue_smoke', phase: 'smoke' },
  });
  check(response, { 'catalogue: HTTP 200': (r) => r.status === 200 });
  sleep(5);
}

export function handleSummary(data) {
  return { stdout: `Integration smoke ${runId}: ${data.metrics.http_reqs.values.count} GETs\n`,
    [`${__ENV.RESULTS_DIR || '/results'}/${runId}.json`]: JSON.stringify({
      run_id: runId, scenario: 'integration_smoke', generated_at: new Date().toISOString(), ...data,
    }, null, 2) };
}
