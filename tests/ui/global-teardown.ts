// After the tests, even when some failed: starts again any service a test left
// stopped, then deactivates the products and accounts created by this run (there is
// no physical delete in the services). Existing data is never touched.
// docker compose down -v is never used.
import { auth, adminLogin, newApi } from './support/api';
import { readState, removeState, runId } from './support/state';
import { startService, type Service } from './support/services';

export default async function globalTeardown(): Promise<void> {
  const id = runId();
  const state = readState(id);

  for (const service of state.stoppedServices) {
    await startService(service as Service);
  }

  const api = await newApi();
  const problems: string[] = [];
  try {
    const token = await adminLogin(api);
    for (const productId of new Set(state.productIds)) {
      const res = await api.delete(`/api/products/${productId}`, { headers: auth(token) });
      if (![204, 404].includes(res.status())) problems.push(`product ${productId}: ${res.status()}`);
    }
    for (const userId of new Set(state.userIds)) {
      const res = await api.patch(`/api/users/${userId}`, { headers: auth(token), data: { is_active: false } });
      if (![200, 404].includes(res.status())) problems.push(`user ${userId}: ${res.status()}`);
    }
  } finally {
    await api.dispose();
  }
  console.log(
    `[ui6 cleanup] run ${id}: ${new Set(state.productIds).size} products and ` +
      `${new Set(state.userIds).size} accounts deactivated` + (problems.length ? `; problems: ${problems.join(', ')}` : ''),
  );
  if (problems.length === 0) removeState(id);
}
