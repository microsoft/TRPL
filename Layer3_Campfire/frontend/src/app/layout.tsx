import type { Metadata } from 'next'
import { QueryProvider } from '@/providers/QueryProvider'
import { TelemetryProvider } from '@/providers/TelemetryProvider'
import { Toaster } from 'sonner'
import './globals.css'

export const metadata: Metadata = {
  title: 'Campfire | Theodore Roosevelt Presidential Library',
  description:
    'Discover the life and legacy of Theodore Roosevelt with AI-powered research tools.',
  keywords: [
    'Theodore Roosevelt',
    'TR',
    'Presidential Library',
    'History',
    'Research',
  ],
}

export default function RootLayout({children}: Readonly<{children: React.ReactNode}>) {
  return (
    <html lang="en">
      <head>
      </head>
      <body>
        <TelemetryProvider>
          <QueryProvider>{children}</QueryProvider>
        </TelemetryProvider>
        <Toaster
          position="bottom-center"
          toastOptions={{
            style: {
              background: '#000000',
              color: '#FFFFFF',
              border: 'none',
            },
          }}
        />
      </body>
    </html>
  )
}
