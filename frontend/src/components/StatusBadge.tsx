export type BadgeTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger';

interface StatusBadgeProps {
  label: string;
  tone?: BadgeTone;
}

/** Generic badge; the mapping of order and payment states to tones comes with those pages */
export function StatusBadge({ label, tone = 'neutral' }: StatusBadgeProps) {
  return <span className={`badge badge--${tone}`}>{label}</span>;
}
