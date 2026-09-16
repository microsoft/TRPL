// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { AboutView } from '@/components/about'
import { ErrorBoundary } from '@/components/ui'

/**
 * About/Responsible AI Page
 */
export default function AboutPage() {
  return (
    <ErrorBoundary>
      <AboutView />
    </ErrorBoundary>
  )
}
