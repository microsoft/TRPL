// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { HomeView } from '@/components/home'
import { ErrorBoundary } from '@/components/ui'

/**
 * Welcome/Landing Page
 */
export default function Home() {
  return (
    <ErrorBoundary>
      <HomeView />
    </ErrorBoundary>
  )
}
