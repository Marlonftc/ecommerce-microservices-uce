// @vitest-environment jsdom
// Shared reads of CartContext: one GET /cart per session and generation, released on
// success and on failure, never reused after a cart change or by another session.
import { act, cleanup, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CartOut } from '../api/types';
import { AuthProvider, useAuth, type AuthContextValue } from '../auth/AuthContext';
import { createFakeApi, deferred, json, type Deferred, type FakeApi } from '../test/fakeApi';
import { cart, otherCustomer, signIn } from '../test/shop';
import { makeToken } from '../test/tokens';
import { CartProvider, useCart, type CartContextValue } from './CartContext';

let api: FakeApi;
let shop: CartContextValue;
let auth: AuthContextValue;

function Probe() {
  shop = useCart();
  auth = useAuth();
  return null;
}

function renderProvider() {
  return render(
    <AuthProvider>
      <CartProvider>
        <Probe />
      </CartProvider>
    </AuthProvider>,
  );
}

const reads = () => api.callsTo('GET /api/cart').length;
const conflict = (code: string) => json(409, { detail: { code, message: 'x' } });

/** Next GET /cart answers when the test says so */
function holdNextRead(): Deferred {
  const answer = deferred();
  api.once('GET', '/api/cart', () => answer.promise);
  return answer;
}

/** Signed in, first read done: cart version 5, two units of product 1 */
async function ready(initial: CartOut = cart(5, [{ id: 1, quantity: 2 }])) {
  signIn(api);
  api.on('GET', '/api/cart', () => json(200, initial));
  renderProvider();
  await waitFor(() => expect(shop.cart?.version).toBe(initial.version));
  await waitFor(() => expect(shop.refreshing).toBe(false));
}

beforeEach(() => {
  api = createFakeApi();
  vi.stubGlobal('fetch', api.fetchMock);
  sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('shared reads', () => {
  it('several simultaneous reads send ONE GET /cart and all get its answer', async () => {
    await ready();
    const answer = holdNextRead();
    const before = reads();
    let results: (CartOut | null)[] = [];
    await act(async () => {
      const pending = [shop.reload(), shop.reload(), shop.reload()];
      answer.resolve(json(200, cart(6, [{ id: 1, quantity: 3 }])));
      results = await Promise.all(pending);
    });
    expect(reads()).toBe(before + 1);
    expect(results.map((r) => r?.version)).toEqual([6, 6, 6]);
    expect(shop.cart?.version).toBe(6);
  });

  it('a slow read is shared while it lasts; refreshing stays true until it ends', async () => {
    await ready();
    const slow = holdNextRead();
    const before = reads();
    let first!: Promise<CartOut | null>;
    act(() => {
      first = shop.reload();
    });
    expect(shop.refreshing).toBe(true);
    // More requests arrive while it is still pending (tab visible again, "Reintentar"...)
    let later!: Promise<CartOut | null>[];
    act(() => {
      later = [shop.reload(), shop.reload()];
    });
    expect(reads()).toBe(before + 1);
    expect(shop.refreshing).toBe(true);

    await act(async () => {
      slow.resolve(json(200, cart(5, [{ id: 1, quantity: 2 }])));
      await Promise.all([first, ...later]);
    });
    expect(shop.refreshing).toBe(false);
    // Released: the next read is a new request
    await act(async () => {
      await shop.reload();
    });
    expect(reads()).toBe(before + 2);
  });

  it('a failed read is not reused: the next one is a new GET and clears the error', async () => {
    await ready();
    const failing = holdNextRead();
    let results: (CartOut | null)[] = [];
    await act(async () => {
      const pending = [shop.reload(), shop.reload()];
      failing.resolve(json(502, { detail: { code: 'UPSTREAM_UNAVAILABLE', message: 'x' } }));
      results = await Promise.all(pending);
    });
    expect(results).toEqual([null, null]);
    expect(shop.loadError).not.toBeNull();
    expect(shop.cart?.version).toBe(5); // the last confirmed cart is kept

    const before = reads();
    api.on('GET', '/api/cart', () => json(200, cart(6, [{ id: 1, quantity: 4 }])));
    await act(async () => {
      await shop.reload();
    });
    expect(reads()).toBe(before + 1);
    expect(shop.loadError).toBeNull();
    expect(shop.cart?.version).toBe(6);
  });

  it('a read started before a PUT is not reused after it, and its old answer is ignored', async () => {
    await ready();
    const oldRead = holdNextRead();
    let oldResult!: Promise<CartOut | null>;
    act(() => {
      oldResult = shop.reload();
    });
    api.on('PUT', '/api/cart/items/1', () => json(200, cart(6, [{ id: 1, quantity: 4 }])));
    await act(async () => {
      await shop.setQuantity(1, 4);
    });
    expect(shop.cart?.version).toBe(6);

    // A read asked for after the change does not join the old one
    const before = reads();
    api.on('GET', '/api/cart', () => json(200, cart(6, [{ id: 1, quantity: 4 }])));
    await act(async () => {
      await shop.reload();
    });
    expect(reads()).toBe(before + 1);

    // The old answer (version 5) arrives last and changes nothing
    await act(async () => {
      oldRead.resolve(json(200, cart(5, [{ id: 1, quantity: 2 }])));
      await oldResult;
    });
    expect(shop.cart?.version).toBe(6);
    expect(shop.quantityOf(1)).toBe(4);
  });

  it('CART_CHANGED: the re-read is a NEW GET even if an older one is still pending', async () => {
    await ready();
    const oldRead = holdNextRead();
    act(() => {
      void shop.reload();
    });
    const before = reads();
    api.on('PUT', '/api/cart/items/1', () => conflict('CART_CHANGED'));
    api.on('GET', '/api/cart', () => json(200, cart(7, [{ id: 1, quantity: 9 }])));
    let result!: Awaited<ReturnType<CartContextValue['setQuantity']>>;
    await act(async () => {
      result = await shop.setQuantity(1, 3);
    });
    expect(reads()).toBe(before + 1);
    expect(result.ok).toBe(false);
    expect(result.cart?.version).toBe(7); // from the new read, not from the pending old one
    expect(shop.quantityOf(1)).toBe(9);
    expect(api.callsTo('PUT /api/cart/items/1')).toHaveLength(1); // not repeated

    await act(async () => {
      oldRead.resolve(json(200, cart(5, [{ id: 1, quantity: 2 }])));
    });
    expect(shop.cart?.version).toBe(7);
  });

  it('a read started WHILE a change runs is not reused for the re-read after it', async () => {
    await ready();
    const putAnswer = deferred();
    api.on('PUT', '/api/cart/items/1', () => putAnswer.promise);
    let change!: Promise<Awaited<ReturnType<CartContextValue['setQuantity']>>>;
    act(() => {
      change = shop.setQuantity(1, 3);
    });
    await waitFor(() => expect(api.callsTo('PUT /api/cart/items/1')).toHaveLength(1));
    const duringRead = holdNextRead(); // e.g. the tab became visible meanwhile
    act(() => {
      void shop.reload();
    });
    const before = reads();
    api.on('GET', '/api/cart', () => json(200, cart(7, [{ id: 1, quantity: 9 }])));
    let result!: Awaited<typeof change>;
    await act(async () => {
      putAnswer.resolve(conflict('CART_CHANGED'));
      result = await change;
    });
    expect(reads()).toBe(before + 1);
    expect(result.cart?.version).toBe(7);
    await act(async () => {
      duringRead.resolve(json(200, cart(5, [{ id: 1, quantity: 2 }])));
    });
    expect(shop.cart?.version).toBe(7);
  });

  it('locked changes without a new version: an older read cannot unlock the cart again', async () => {
    await ready(); // version 5, not locked
    const oldRead = holdNextRead();
    act(() => {
      void shop.reload();
    });
    api.on('DELETE', '/api/cart/items/1', () => conflict('CART_LOCKED'));
    api.on('GET', '/api/cart', () => json(200, { ...cart(5, [{ id: 1, quantity: 2 }]), locked: true }));
    await act(async () => {
      await shop.removeItem(1);
    });
    expect(shop.cart?.locked).toBe(true);
    expect(shop.cart?.version).toBe(5); // same version as before

    // The read sent before the DELETE answers "not locked" with the same version 5
    await act(async () => {
      oldRead.resolve(json(200, cart(5, [{ id: 1, quantity: 2 }])));
    });
    expect(shop.cart?.locked).toBe(true);
  });

  it('an older read finishing does not release a newer shared read', async () => {
    await ready();
    const oldRead = holdNextRead();
    act(() => {
      void shop.reload();
    });
    api.on('PUT', '/api/cart/items/1', () => json(200, cart(6, [{ id: 1, quantity: 3 }])));
    await act(async () => {
      await shop.setQuantity(1, 3);
    });
    const newRead = holdNextRead();
    act(() => {
      void shop.reload(); // new generation: a second, newer shared read
    });
    const before = reads();
    await act(async () => {
      oldRead.resolve(json(200, cart(5, [{ id: 1, quantity: 2 }])));
    });
    // The newer read is still the shared one: no extra GET
    act(() => {
      void shop.reload();
    });
    expect(reads()).toBe(before);
    await act(async () => {
      newRead.resolve(json(200, cart(6, [{ id: 1, quantity: 3 }])));
    });
    expect(shop.cart?.version).toBe(6);
  });

  it('a caller that stops waiting does not cancel the read shared with others', async () => {
    await ready();
    const answer = holdNextRead();
    const before = reads();
    const leaving = new AbortController();
    let left!: Promise<CartOut | null>;
    let staying!: Promise<CartOut | null>;
    act(() => {
      left = shop.reload({ signal: leaving.signal });
      staying = shop.reload();
    });
    act(() => leaving.abort());
    expect(await left).toBeNull();
    const call = api.callsTo('GET /api/cart').at(-1)!;
    expect(call.init?.signal?.aborted).toBe(false); // the request itself goes on

    await act(async () => {
      answer.resolve(json(200, cart(6, [{ id: 1, quantity: 3 }])));
    });
    expect((await staying)?.version).toBe(6);
    expect(reads()).toBe(before + 1);
    // Cleaned up afterwards: a new read is a new request
    await act(async () => {
      await shop.reload();
    });
    expect(reads()).toBe(before + 2);
  });

  it('sign out and another user: the pending read is not reused and its answer is ignored', async () => {
    await ready(cart(5, [{ id: 1, quantity: 2 }]));
    const anaRead = holdNextRead(); // the server answers late and ignores the abort
    act(() => {
      void shop.reload();
    });
    const anaCall = api.callsTo('GET /api/cart').at(-1)!;

    act(() => auth.logout());
    expect(shop.cart).toBeNull();
    expect(anaCall.init?.signal?.aborted).toBe(true); // the old session's request was cancelled

    const luisToken = makeToken({ sub: '8', role: 'customer' }, 'luis');
    api.on('POST', '/api/auth/login', () => json(200, { access_token: luisToken, token_type: 'bearer', expires_in: 1800 }));
    api.on('GET', '/api/users/me', () => json(200, otherCustomer));
    api.on('GET', '/api/cart', () => json(200, cart(1, [{ id: 4, quantity: 1 }])));
    const before = reads();
    await act(async () => {
      await auth.login(otherCustomer.email, 'luis-password');
    });
    await waitFor(() => expect(shop.cart?.version).toBe(1));
    expect(reads()).toBe(before + 1); // Luis's read is his own request

    await act(async () => {
      anaRead.resolve(json(200, cart(9, [{ id: 1, quantity: 7 }])));
    });
    expect(shop.cart?.version).toBe(1);
    expect(shop.quantityOf(1)).toBe(0);
    expect(shop.quantityOf(4)).toBe(1);
  });

  it('a read sent while the PUT was still running cannot settle an uncertain outcome', async () => {
    await ready();
    const putAnswer = deferred();
    api.on('PUT', '/api/cart/items/1', () => putAnswer.promise);
    let change!: Promise<unknown>;
    act(() => {
      change = shop.setQuantity(1, 3);
    });
    await waitFor(() => expect(api.callsTo('PUT /api/cart/items/1')).toHaveLength(1));
    const duringRead = holdNextRead(); // sent after the PUT, before its failure
    act(() => {
      void shop.reload();
    });
    const lateRead = deferred();
    api.on('GET', '/api/cart', () => lateRead.promise);
    await act(async () => {
      putAnswer.reject(new TypeError('Failed to fetch'));
    });
    await waitFor(() => expect(shop.needsReload).toBe(true));

    // The PUT may have been committed after this read was answered: still unknown
    await act(async () => {
      duringRead.resolve(json(200, cart(5, [{ id: 1, quantity: 2 }])));
    });
    expect(shop.needsReload).toBe(true);
    expect((await shop.setQuantity(1, 4)).ok).toBe(false); // changes stay blocked

    await act(async () => {
      lateRead.resolve(json(200, cart(6, [{ id: 1, quantity: 3 }])));
      await change;
    });
    expect(shop.needsReload).toBe(false);
  });

  it('after an uncertain failure, only a read sent after it settles the outcome', async () => {
    await ready();
    const earlyRead = holdNextRead(); // started before the PUT failed
    act(() => {
      void shop.reload();
    });
    const lateRead = deferred();
    api.on('PUT', '/api/cart/items/1', () => Promise.reject(new TypeError('Failed to fetch')));
    api.on('GET', '/api/cart', () => lateRead.promise);
    let change!: Promise<unknown>;
    act(() => {
      change = shop.setQuantity(1, 3);
    });
    await waitFor(() => expect(shop.needsReload).toBe(true));

    // The early read answers first: it cannot say whether the PUT was applied
    await act(async () => {
      earlyRead.resolve(json(200, cart(5, [{ id: 1, quantity: 2 }])));
    });
    expect(shop.needsReload).toBe(true);

    await act(async () => {
      lateRead.resolve(json(200, cart(6, [{ id: 1, quantity: 3 }])));
      await change;
    });
    expect(shop.needsReload).toBe(false);
    expect(shop.quantityOf(1)).toBe(3);
  });
});
