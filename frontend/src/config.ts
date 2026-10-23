// Build-time settings of the web app. Vite replaces import.meta.env.VITE_* values when the
// app is compiled: changing them needs a new build (in Docker, rebuilding the image).
// They are visible to anyone who reads the bundle, so they never hold secrets, and they
// are interface choices only: the backend applies its own rules regardless of them.

/**
 * VITE_PAYMENT_DEMO=true shows the "Opciones de demostración" of the simulated payment
 * (reject, slow approval). Off by default: customers only see "Pagar" (APPROVE).
 * Hiding the options is not a security control; order-service accepts any valid mode.
 */
export function paymentDemoEnabled(): boolean {
  return import.meta.env.VITE_PAYMENT_DEMO === 'true';
}
