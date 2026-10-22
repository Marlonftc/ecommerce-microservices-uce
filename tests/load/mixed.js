// Scenario 3: mixed load. Target: about 80 % of the PUBLIC HTTP requests are catalogue
// browsing and 20 % belong to purchases. Both parts run at a fixed arrival rate:
//   browsing  3 visits/s  x 3 requests      = ~9 req/s
//   purchases 0.5/s       x ~4-5 requests   = ~2-2.5 req/s
// The real proportion is measured (public_requests counter here, gateway log in run_load.py).
import { Counter } from 'k6/metrics';
import { adminToken, browseOnce, catalogIds, createProducts, deactivateProducts, newRunId, summary } from './lib.js';
import { purchaseOnce, purchasesLine } from './purchase_flow.js';

const publicRequests = new Counter('public_requests');
const BROWSE_RATE = Number(__ENV.BROWSE_RATE || 3);
const PURCHASE_RATE_PER_MIN = Number(__ENV.PURCHASE_RATE_PER_MIN || 30);
const BUYERS = Number(__ENV.BUYERS || 4);

export const options = {
  scenarios: {
    browse: {
      executor: 'constant-arrival-rate', exec: 'browse', rate: BROWSE_RATE, timeUnit: '1s',
      duration: '2m', preAllocatedVUs: 10, maxVUs: 30,
    },
    purchase: {
      // maxVUs = number of buyers: each buyer VU has its own customer
      executor: 'constant-arrival-rate', exec: 'purchase', rate: PURCHASE_RATE_PER_MIN, timeUnit: '1m',
      duration: '2m', preAllocatedVUs: BUYERS, maxVUs: BUYERS, gracefulStop: '40s',
    },
  },
  setupTimeout: '3m',
  thresholds: {
    'http_req_duration{name:list}': ['p(95)<500'],
    'http_req_duration{name:detail}': ['p(95)<500'],
    'http_req_duration{name:stock}': ['p(95)<500'],
    'http_req_duration{name:cart}': ['p(95)<1000'],
    'http_req_duration{name:checkout}': ['p(95)<3000'],
    'http_req_duration{name:pay}': ['p(95)<3000'],
    purchase_success: ['rate>0.99'],
    'http_req_failed{phase:main}': ['rate<0.01'],
    checks: ['rate>0.99'],
    // Listed only so that both counts appear in the summary
    'public_requests{kind:browse}': ['count>=0'],
    'public_requests{kind:purchase}': ['count>=0'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'max'],
};

export function setup() {
  const runId = newRunId('mixed');
  const token = adminToken(runId);
  const loadIds = createProducts(runId, token, 5, 100000);
  return { runId, token, loadIds, productIds: [...catalogIds(runId), ...loadIds] };
}

export function browse(data) {
  browseOnce(data, publicRequests);
}

export function purchase(data) {
  purchaseOnce(data, publicRequests);
}

export function teardown(data) {
  deactivateProducts(data.runId, data.token, data.loadIds);
}

const shareLine = (m) => {
  const b = m['public_requests{kind:browse}'] ? m['public_requests{kind:browse}'].values.count : 0;
  const p = m['public_requests{kind:purchase}'] ? m['public_requests{kind:purchase}'].values.count : 0;
  return b + p ? `public requests: browse ${b} (${(100 * b / (b + p)).toFixed(1)} %), purchase ${p} (${(100 * p / (b + p)).toFixed(1)} %)` : null;
};

export function handleSummary(data) {
  return summary('mixed', data, [purchasesLine, shareLine]);
}
