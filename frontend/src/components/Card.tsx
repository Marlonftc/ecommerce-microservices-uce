import type { ReactNode } from 'react';

interface CardProps {
  title?: string;
  children: ReactNode;
  className?: string;
}

export function Card({ title, children, className = '' }: CardProps) {
  return (
    <section className={`card ${className}`.trim()}>
      {title && <h2 className="card__title">{title}</h2>}
      {children}
    </section>
  );
}
