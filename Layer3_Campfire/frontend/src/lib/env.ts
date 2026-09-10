import { z } from 'zod'

/**
 * Environment variable schema
 * Validates at runtime - app will fail fast if misconfigured
 */
const envSchema = z.object({
  // Server-side only (not exposed to client)
  PYTHON_RAG_API_URL: z.string().url({
    message: 'PYTHON_RAG_API_URL must be a valid URL',
  }),
  RAG_API_KEY: z.string({
    message: 'RAG_API_KEY is required for backend authentication',
  }),
  // Azure storage account hosting the artifact PDFs. The PDF proxy refuses
  // any URL whose host is not `<this>.blob.core.windows.net`. Required when
  // the PDF proxy is in use; absent ⇒ proxy returns 503.
  AZURE_STORAGE_ACCOUNT: z
    .string()
    .regex(/^[a-z0-9]{3,24}$/, {
      message:
        'AZURE_STORAGE_ACCOUNT must be a valid Azure storage account name (3-24 lowercase alphanumeric)',
    })
    .optional(),

  // Public variables (exposed to client via NEXT_PUBLIC_ prefix)
  NEXT_PUBLIC_APP_NAME: z.string().default('Reading Room'),
  NEXT_PUBLIC_APP_URL: z.string().url().default('http://localhost:3000'),
  NEXT_PUBLIC_PYTHON_RAG_API_URL: z.string().url().optional(),

  // Telemetry (optional - app works without it)
  NEXT_PUBLIC_APPINSIGHTS_CONNECTION_STRING: z.string().optional(),
})

/**
 * Validated environment variables
 * Access via: import { env } from '@/lib/env'
 */
export const env = envSchema.parse({
  PYTHON_RAG_API_URL: process.env.PYTHON_RAG_API_URL,
  RAG_API_KEY: process.env.RAG_API_KEY,
  AZURE_STORAGE_ACCOUNT: process.env.AZURE_STORAGE_ACCOUNT,
  NEXT_PUBLIC_APP_NAME: process.env.NEXT_PUBLIC_APP_NAME,
  NEXT_PUBLIC_APP_URL: process.env.NEXT_PUBLIC_APP_URL,
  NEXT_PUBLIC_PYTHON_RAG_API_URL: process.env.NEXT_PUBLIC_PYTHON_RAG_API_URL,
  NEXT_PUBLIC_APPINSIGHTS_CONNECTION_STRING: process.env.NEXT_PUBLIC_APPINSIGHTS_CONNECTION_STRING,
})

export type Env = z.infer<typeof envSchema>
