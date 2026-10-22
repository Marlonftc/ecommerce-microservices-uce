// Scenario 1: catalogue browsing (read only), 0 -> 20 virtual users in about 2 minutes.
import { sleep } from 'k6';
import { Counter } from 'k6/metrics';
import { adminToken, browseOnce, catalogIds, createProducts, deactivateProducts, newRunId, summary } from './lib.js';

const publicRequests = new Counter('public_requests');

export const options = {
  scenarios: {
    browse: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 10 },
        { duration: '60s', target: 20 },
        { duration: '30s', target: 0 },
      ],
      gracefulRampDown: '10s',
    },
  },
  setupTimeout: '3m',
  thresholds: {
    'http_req_duration{name:list}': ['p(95)<500'],
    'http_req_duration{name:detail}': ['p(95)<500'],
    'http_req_duration{name:stock}': ['p(95)<500'],
    'http_req_failed{phase:main}': ['rate<0.01'],
    checks: ['rate>0.99'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'max'],
};

export function setup() {
  const runId = newRunId('browse');
  const token = adminToken(runId);
  const loadIds = createProducts(runId, token, 5, 1000);
  return { runId, token, loadIds, productIds: [...catalogIds(runId), ...loadIds] };
}

export default function (data) {
  browseOnce(data, publicRequests);
  sleep(1);   // think time of a person reading the page
}

export function teardown(data) {
  deactivateProducts(data.runId, data.token, data.loadIds);
}

export function handleSummary(data) {
  return summary('browse', data);
}
