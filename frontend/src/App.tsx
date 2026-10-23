import { BrowserRouter, Route, Routes } from 'react-router-dom';
import { AuthProvider } from './auth/AuthContext';
import { GuestOnly, RequireAuth, RequireRole } from './auth/guards';
import { CartProvider } from './cart/CartContext';
import { Layout } from './components/Layout';
import { NotificationsProvider } from './notifications/NotificationsContext';
import { AccountPage } from './pages/AccountPage';
import { AdminHomePage } from './pages/AdminHomePage';
import { CartPage } from './pages/CartPage';
import { CatalogPage } from './pages/CatalogPage';
import { CheckoutPage } from './pages/CheckoutPage';
import { HomePage } from './pages/HomePage';
import { LoginPage } from './pages/LoginPage';
import { NotificationsPage } from './pages/NotificationsPage';
import { NotFoundPage } from './pages/NotFoundPage';
import { OrderDetailPage } from './pages/OrderDetailPage';
import { OrdersPage } from './pages/OrdersPage';
import { ProductDetailPage } from './pages/ProductDetailPage';
import { RegisterPage } from './pages/RegisterPage';

/** Route table, shared by the app and the tests. CartProvider and NotificationsProvider need
 * AuthProvider above them (and NotificationsProvider a router, for the current page). */
export function AppRoutes() {
  return (
    <CartProvider>
      <NotificationsProvider>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<HomePage />} />
            <Route path="productos" element={<CatalogPage />} />
            <Route path="productos/:productId" element={<ProductDetailPage />} />
            <Route element={<GuestOnly />}>
              <Route path="login" element={<LoginPage />} />
            </Route>
            <Route element={<GuestOnly fallback="/cuenta" />}>
              <Route path="registro" element={<RegisterPage />} />
            </Route>
            <Route element={<RequireAuth />}>
              <Route path="cuenta" element={<AccountPage />} />
              <Route path="carrito" element={<CartPage />} />
              <Route path="checkout" element={<CheckoutPage />} />
              <Route path="pedidos" element={<OrdersPage />} />
              <Route path="pedidos/:orderId" element={<OrderDetailPage />} />
              {/* Customers only: an admin would receive every customer's notifications */}
              <Route element={<RequireRole role="customer" />}>
                <Route path="notificaciones" element={<NotificationsPage />} />
              </Route>
              <Route element={<RequireRole role="admin" />}>
                <Route path="admin" element={<AdminHomePage />} />
              </Route>
            </Route>
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Routes>
      </NotificationsProvider>
    </CartProvider>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </BrowserRouter>
  );
}
