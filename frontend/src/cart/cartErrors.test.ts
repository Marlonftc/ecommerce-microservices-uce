import { describe, expect, it } from 'vitest';
import { ApiError } from '../api/errors';
import { classifyCartError, describeProblem, isSilent, problem } from './cartErrors';

const fromBody = (status: number, body: unknown) => ApiError.fromResponse(status, body, 'req-1', null);

describe('classifyCartError', () => {
  it.each([
    ['CART_CHANGED', 'changed'],
    ['CART_LOCKED', 'locked'],
    ['CART_FULL', 'full'],
  ])('409 %s -> %s', (code, kind) => {
    expect(classifyCartError(fromBody(409, { detail: { code, message: 'x' } })).kind).toBe(kind);
  });

  it('404 PRODUCT_NOT_AVAILABLE', () => {
    const error = fromBody(404, { detail: { code: 'PRODUCT_NOT_AVAILABLE', message: 'x' } });
    expect(classifyCartError(error).kind).toBe('product-unavailable');
  });

  it('503/502 sent by order-service itself: the product could not be checked, the cart did not change', () => {
    expect(classifyCartError(fromBody(503, { detail: 'product-service is not available, try again later' })).kind).toBe('catalog-down');
    expect(classifyCartError(fromBody(502, { detail: 'product-service gave an unexpected answer' })).kind).toBe('catalog-down');
  });

  it('no answer from order-service: the outcome is uncertain', () => {
    expect(classifyCartError(ApiError.timeout()).kind).toBe('uncertain');
    expect(classifyCartError(ApiError.network()).kind).toBe('uncertain');
    expect(classifyCartError(fromBody(504, { detail: { code: 'UPSTREAM_TIMEOUT', message: 'x' } })).kind).toBe('uncertain');
    expect(classifyCartError(fromBody(502, { detail: { code: 'UPSTREAM_UNAVAILABLE', message: 'x' } })).kind).toBe('uncertain');
    expect(classifyCartError(fromBody(500, 'Internal Server Error')).kind).toBe('uncertain');
  });

  it('422, 401 and anything else', () => {
    expect(classifyCartError(fromBody(422, { detail: [{ loc: ['body', 'quantity'], msg: 'x' }] })).kind).toBe('invalid');
    expect(classifyCartError(fromBody(401, { detail: 'Invalid or expired token' })).kind).toBe('session');
    const other = classifyCartError(fromBody(403, { detail: 'Forbidden' }));
    expect(other.kind).toBe('other');
    expect(other.message).toBe('No tienes permiso para realizar esta acción.');
    expect(classifyCartError(new Error('boom')).kind).toBe('other');
  });
});

describe('messages', () => {
  it('uncertain outcomes say whether the cart was read again', () => {
    const found = problem('uncertain');
    expect(describeProblem(found, true)).toContain('volvimos a consultar tu carrito');
    expect(describeProblem(found, false)).toContain('Reintentar');
  });

  it('busy and cancelled show nothing', () => {
    expect(isSilent(problem('busy'))).toBe(true);
    expect(isSilent(problem('cancelled'))).toBe(true);
    expect(isSilent(problem('changed'))).toBe(false);
  });

  it('CART_FULL names the limit of 50 products', () => {
    expect(problem('full').message).toContain('50 productos distintos');
  });
});
