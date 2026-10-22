// One complete purchase per call: cart -> checkout -> APPROVE payment -> order PAID and
// inventory CONFIRMED. Each buyer VU uses its own customer, created on its first
// iteration (k6 has no stable "VU number inside a scenario", so a pool prepared in
// setup could give the same customer to two concurrent VUs).
import http from 'k6/http';
import { check, sleep } from 'k6';
import exec from 'k6/execution';
import { Counter, Rate, Trend } from 'k6/metrics';
import { BASE, newCustomer, params, pick } from './lib.js';

export const purchaseTotal = new Trend('purchase_total_duration', true);
export const purchasesCompleted = new Counter('purchases_completed');
export const purchasesFailed = new Counter('purchases_failed');
export const purchaseSuccess = new Rate('purchase_success');

let customer = null;      // VU-local: one customer per buyer VU
let iteration = 0;

const RETRYABLE = [0, 502, 503, 504];

// Retries only network errors and gateway errors, always with the same Idempotency-Key
function sendWithRetry(send) {
  let r = send();
  for (let attempt = 1; attempt <= 2 && RETRYABLE.includes(r.status); attempt++) {
    sleep(1);
    r = send();
  }
  return r;
}

function getOrder(data, id, publicRequests) {
  const r = http.get(`${BASE}/api/orders/${id}`, params(data.runId, 'order_poll', { token: customer.token }));
  if (publicRequests) publicRequests.add(1, { kind: 'purchase' });
  return r.status === 200 ? r.json() : null;
}

function waitFor(data, id, done, publicRequests, timeoutSeconds = 30) {
  const deadline = Date.now() + timeoutSeconds * 1000;
  let order = null;
  while (Date.now() < deadline) {
    order = getOrder(data, id, publicRequests);
    if (order && done(order)) return order;
    sleep(0.5);
  }
  return order;
}

function failed(data, step, orderId, publicRequests) {
  purchasesFailed.add(1, { step });
  purchaseSuccess.add(false);
  // Leave the customer free for the next purchase: an unpaid order is cancelled
  if (orderId) {
    http.post(`${BASE}/api/orders/${orderId}/cancel`, null, params(data.runId, 'cancel', { token: customer.token }));
    if (publicRequests) publicRequests.add(1, { kind: 'purchase' });
  }
}

export function purchaseOnce(data, publicRequests) {
  if (!customer) customer = newCustomer(data.runId, `buyer${exec.vu.idInTest}`);
  iteration += 1;
  const count = (n) => publicRequests && publicRequests.add(n, { kind: 'purchase' });
  const start = Date.now();
  const productId = pick(data.loadIds);

  const cart = http.put(`${BASE}/api/cart/items/${productId}`, JSON.stringify({ quantity: 1 }),
    params(data.runId, 'cart', { token: customer.token }));
  count(1);
  if (!check(cart, { 'cart: 200': (r) => r.status === 200 })) return failed(data, 'cart', null, publicRequests);

  const checkoutKey = `${data.runId}-ck-${exec.vu.idInTest}-${iteration}`;
  const placed = sendWithRetry(() => {
    count(1);
    return http.post(`${BASE}/api/orders`, JSON.stringify({ cart_version: cart.json('version') }),
      params(data.runId, 'checkout', { token: customer.token, headers: { 'Idempotency-Key': checkoutKey } }));
  });
  if (!check(placed, { 'checkout: 201/200/202': (r) => [200, 201, 202].includes(r.status) })) {
    return failed(data, 'checkout', null, publicRequests);
  }
  let order = placed.json();
  if (order.status === 'RESERVING') {
    // 202: inventory did not answer in time; the order is completed in the background
    order = waitFor(data, order.id, (o) => o.status !== 'RESERVING', publicRequests);
  }
  if (!order || order.status !== 'PENDING_PAYMENT') return failed(data, 'reserve', order && order.id, publicRequests);

  const payKey = `${data.runId}-pay-${order.id}`;
  const paid = sendWithRetry(() => {
    count(1);
    return http.post(`${BASE}/api/orders/${order.id}/pay`, JSON.stringify({ simulation: 'APPROVE' }),
      params(data.runId, 'pay', { token: customer.token, headers: { 'Idempotency-Key': payKey } }));
  });
  // 200 PAID, or 202 PAYMENT_PENDING (valid: no final answer yet)
  if (!check(paid, { 'pay: 200/202': (r) => [200, 202].includes(r.status) })) {
    return failed(data, 'pay', order.id, publicRequests);
  }

  const final = waitFor(data, order.id, (o) => o.status === 'PAID' && o.inventory_status === 'CONFIRMED',
    publicRequests);
  const ok = check(final, { 'order: PAID and CONFIRMED': (o) => o && o.status === 'PAID' && o.inventory_status === 'CONFIRMED' });
  if (!ok) return failed(data, 'confirm', null, publicRequests);

  purchaseTotal.add(Date.now() - start);
  purchasesCompleted.add(1);
  purchaseSuccess.add(true);
}

// Summary line shared by purchase.js and mixed.js
export const purchasesLine = (m) => `purchases: ${m.purchases_completed ? m.purchases_completed.values.count : 0} completed, ` +
  `${m.purchases_failed ? m.purchases_failed.values.count : 0} failed; total time avg ` +
  `${m.purchase_total_duration ? m.purchase_total_duration.values.avg.toFixed(0) : '-'} ms, p95 ` +
  `${m.purchase_total_duration ? m.purchase_total_duration.values['p(95)'].toFixed(0) : '-'} ms`;
