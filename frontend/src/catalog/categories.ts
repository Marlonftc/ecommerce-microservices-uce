// Categories are stored normalized by product-service ("ropa-deportiva").
// For display: hyphens become spaces and the first letter is capitalized.
export function categoryLabel(slug: string): string {
  const text = slug.replace(/-/g, ' ').trim();
  return text ? text.charAt(0).toLocaleUpperCase('es') + text.slice(1) : slug;
}
