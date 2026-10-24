// Before the tests: checks that the system answers through the gateway with the
// E2E frontend (VITE_PAYMENT_DEMO=true) and creates the state of this run.
import { randomBytes } from 'node:crypto';
import { adminCredentials, GATEWAY_URL } from './support/env';
import { createState } from './support/state';

export default async function globalSetup(): Promise<void> {
  adminCredentials(); // fails early, without printing anything, if they are missing

  const health = await fetch(`${GATEWAY_URL}/health`).catch(() => null);
  const products = await fetch(`${GATEWAY_URL}/api/products?limit=1`).catch(() => null);
  if (health?.status !== 200 || products?.status !== 200) {
    throw new Error(
      `The gateway does not answer on ${GATEWAY_URL}. Start the system with ` +
        'compose.yaml + compose.override.example.yaml + compose.e2e.yaml (see tests/ui/README.md).',
    );
  }

  // The payment options exist only in the E2E build: check the bundle actually served
  const html = await (await fetch(`${GATEWAY_URL}/`)).text();
  const script = /src="(\/assets\/index-[\w-]+\.js)"/.exec(html)?.[1];
  const bundle = script ? await (await fetch(GATEWAY_URL + script)).text() : '';
  if (!bundle.includes('Opciones de demostración')) {
    throw new Error(
      'The frontend served by the gateway was built with VITE_PAYMENT_DEMO=false. Start it with ' +
        'compose.e2e.yaml (image ecommerce-uce/frontend:e2e) before running the UI tests.',
    );
  }

  // Short and unique: it goes into e-mails and SKUs (up to 32 characters)
  const runId = `${Date.now().toString(36).slice(-5)}${randomBytes(1).toString('hex')}`;
  process.env.UI_RUN_ID = runId;
  createState(runId);
}
