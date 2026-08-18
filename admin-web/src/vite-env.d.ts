/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_NOVA_API_URL?: string;
  readonly VITE_NOVA_DEFAULT_ORG?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
