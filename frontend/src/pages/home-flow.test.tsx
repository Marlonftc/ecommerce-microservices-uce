// @vitest-environment jsdom
// Welcome page and its single catalogue entry point, with real routes and providers.
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppRoutes } from '../App';
import { AuthProvider } from '../auth/AuthContext';
import { createFakeApi, json, type FakeApi } from '../test/fakeApi';
import { page, product, stock } from '../test/shop';

let api: FakeApi;

function Where() {
  const location = useLocation();
  return <output data-testid="where">{location.pathname}{location.search}</output>;
}

function renderHome() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <AuthProvider><AppRoutes /><Where /></AuthProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  api = createFakeApi();
  vi.stubGlobal('fetch', api.fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('public home', () => {
  it('welcomes without catalogue queries, duplicated shopping controls or the academic strip', () => {
    renderHome();
    const main = screen.getByRole('main');
    expect(within(main).getByRole('link', { name: 'Explorar catálogo' }).getAttribute('href')).toBe('/productos');
    expect(within(main).getAllByRole('link')).toHaveLength(1);
    expect(screen.getByAltText('Universidad Central del Ecuador').getAttribute('src')).toBe('/images/uce-crest.png');
    expect(screen.getByAltText('Instalaciones de la Universidad Central del Ecuador').getAttribute('src')).toBe('/images/uce-campus.webp');
    expect(within(main).queryByRole('search')).toBeNull();
    expect(within(main).queryByRole('article')).toBeNull();
    expect(screen.queryByText('Productos del catálogo')).toBeNull();
    expect(screen.queryByText(/Desarrollado para el complexivo UCE/)).toBeNull();
    expect(screen.queryByText(/no una tienda comercial oficial/)).toBeNull();
    expect(api.calls).toHaveLength(0);
  });

  it('opens the existing catalogue with its search, categories, prices and availability', async () => {
    api.on('GET', '/api/products', () => json(200, page([product(1, { name: 'Equipo de prueba', price: '749.99' })])));
    api.on('GET', '/api/inventory', () => json(200, stock({ 1: 12 })));
    renderHome();
    fireEvent.click(screen.getByRole('link', { name: 'Explorar catálogo' }));
    expect(await screen.findByRole('heading', { level: 1, name: 'Catálogo' })).toBeTruthy();
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/productos'));
    expect(screen.getByLabelText('Buscar por nombre')).toBeTruthy();
    expect(screen.getByLabelText('Categoría')).toBeTruthy();
    expect(await screen.findByRole('link', { name: 'Equipo de prueba' })).toBeTruthy();
    expect(await screen.findByText('12 unidades')).toBeTruthy();
    expect(screen.getByText('$749,99')).toBeTruthy();
    expect(api.calls.every((call) => call.key.startsWith('GET '))).toBe(true);
  });
});
