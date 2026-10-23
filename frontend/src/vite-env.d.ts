/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "true" shows the demonstration options of the simulated payment */
  readonly VITE_PAYMENT_DEMO?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
