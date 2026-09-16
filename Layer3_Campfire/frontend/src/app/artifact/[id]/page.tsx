// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { ArtifactDetailView } from '@/components/artifact-detail'
import { ErrorBoundary } from '@/components/ui'

/**
 * Artifact Detail Page
 * Displays detailed information about a specific artifact/source.
 */
export default function ArtifactDetailPage() {
  return (
    <ErrorBoundary>
      <ArtifactDetailView />
    </ErrorBoundary>
  )
}
