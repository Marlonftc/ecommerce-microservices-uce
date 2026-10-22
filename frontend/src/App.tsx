import { BrowserRouter, Route, Routes } from 'react-router-dom';
import { AuthProvider } from './auth/AuthContext';
import { GuestOnly, RequireAuth, RequireRole } from './auth/guards';
import { Layout } from './components/Layout';
import { AccountPage } from './pages/AccountPage';
import { AdminHomePage } from './pages/AdminHomePage';
import { HomePage } from './pages/HomePage';
import { LoginPage } from './pages/LoginPage';
import { NotFoundPage } from './pages/NotFoundPage';
import { RegisterPage } from './pages/RegisterPage';

/** Route table, shared by the app and the tests */
export function AppRoutes() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<HomePage />} />
        <Route element={<GuestOnly />}>
          <Route path="login" element={<LoginPage />} />
        </Route>
        <Route element={<GuestOnly fallback="/cuenta" />}>
          <Route path="registro" element={<RegisterPage />} />
        </Route>
        <Route element={<RequireAuth />}>
          <Route path="cuenta" element={<AccountPage />} />
          <Route element={<RequireRole role="admin" />}>
            <Route path="admin" element={<AdminHomePage />} />
          </Route>
        </Route>
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
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
