/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** P4-02: read:decisions API token baked into a build that serves the
   * public track-record page (the operator issues it via POST /api/tokens;
   * a ?token= query param overrides it at view time). */
  readonly VITE_TRACK_RECORD_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
