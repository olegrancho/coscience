/// <reference types="vite/client" />

// Short git SHA of the bundle, injected at build time by vite.config.ts.
declare const __APP_VERSION__: string;
// The app's numbered version from the repo's VERSION file (P13), e.g. "0.1.1".
declare const __APP_RELEASE__: string;
