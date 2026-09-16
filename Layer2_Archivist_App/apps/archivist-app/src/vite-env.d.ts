// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/// <reference types="vite/client" />

// Extend ImportMetaEnv with our VITE_ variables used in the app
interface ImportMetaEnv {
  readonly VITE_BACKEND_API_ENDPOINT: string;
  readonly VITE_APPLICATIONINSIGHTS_CONNECTION_STRING?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
