import type { Availability } from '../catalog/availability';
import { StatusBadge } from './StatusBadge';

/** Availability of a product; the figure is informative, units are reserved at checkout */
export function StockLabel({ availability }: { availability: Availability }) {
  switch (availability.state) {
    case 'loading':
      return <span className="stock stock--loading">Consultando disponibilidad…</span>;
    case 'in-stock':
      return (
        <span className="stock">
          <StatusBadge tone="success" label="Disponible" />
          <span className="stock__units">
            {availability.available} {availability.available === 1 ? 'unidad' : 'unidades'}
          </span>
        </span>
      );
    case 'sold-out':
      return (
        <span className="stock">
          <StatusBadge tone="danger" label="Agotado" />
        </span>
      );
    case 'no-record':
      return (
        <span className="stock">
          <StatusBadge tone="neutral" label="Sin existencias registradas" />
        </span>
      );
    case 'unknown':
      return (
        <span className="stock">
          <StatusBadge tone="warning" label="Disponibilidad no verificada" />
        </span>
      );
  }
}
