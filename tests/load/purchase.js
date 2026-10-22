// Scenario 2: purchases. 4 concurrent buyers, each with its own customer and token,
// buying one after another for 2 minutes (APPROVE payment, no SLOW_APPROVE).
import { sleep } from 'k6';
import { adminToken, catalogIds, createProducts, deactivateProducts, newRunId, summary } from './lib.js';
import { purchaseOnce, purchasesLine } from './purchase_flow.js';

const BUYERS = Number(__ENV.BUYERS || 4);

export const options = {
  scenarios: {
    purchase: { executor: 'constant-vus', vus: BUYERS, duration: '2m', gracefulStop: '40s' },
  },
  setupTimeout: '3m',
  thresholds: {
    // Reference values: checkout and pay call several services, so 500 ms is not applied to them
    'http_req_duration{name:cart}': ['p(95)<1000'],
    'http_req_duration{name:checkout}': ['p(95)<3000'],
    'http_req_duration{name:pay}': ['p(95)<3000'],
    'http_req_duration{name:order_poll}': ['p(95)<500'],
    purchase_total_duration: ['p(95)<6000'],
    purchase_success: ['rate>0.99'],
    'http_req_failed{phase:main}': ['rate<0.01'],
    checks: ['rate>0.99'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'max'],
};

export function setup() {
  const runId = newRunId('purchase');
  const token = adminToken(runId);
  const loadIds = createProducts(runId, token, 5, 100000);
  return { runId, token, loadIds, productIds: [...catalogIds(runId), ...loadIds] };
}

export default function (data) {
  purchaseOnce(data, null);
  sleep(1);
}

export function teardown(data) {
  deactivateProducts(data.runId, data.token, data.loadIds);
}

export function handleSummary(data) {
  return summary('purchase', data, [purchasesLine]);
}
