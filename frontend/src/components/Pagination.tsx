import { Link } from 'react-router-dom';

interface PaginationProps {
  page: number;
  totalPages: number;
  /** Address of a page, e.g. "?q=laptop&pagina=2" */
  hrefFor(page: number): string;
}

export function Pagination({ page, totalPages, hrefFor }: PaginationProps) {
  if (totalPages <= 1) return null;
  return (
    <nav className="pagination" aria-label="Paginación">
      {page > 1 ? (
        <Link to={hrefFor(page - 1)} className="btn btn--secondary" rel="prev">
          Anterior
        </Link>
      ) : (
        <span className="btn btn--secondary pagination__off" aria-disabled="true">
          Anterior
        </span>
      )}
      <span className="pagination__status">
        Página {page} de {totalPages}
      </span>
      {page < totalPages ? (
        <Link to={hrefFor(page + 1)} className="btn btn--secondary" rel="next">
          Siguiente
        </Link>
      ) : (
        <span className="btn btn--secondary pagination__off" aria-disabled="true">
          Siguiente
        </span>
      )}
    </nav>
  );
}
