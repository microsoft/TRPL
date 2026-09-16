// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { useQuery } from '@tanstack/react-query'
import { API_ROUTES } from '@/lib/constants'

const TWENTY_FOUR_HOURS = 24 * 60 * 60 * 1000

async function fetchArtifact(id: string, source: string): Promise<Record<string, unknown>> {
  const response = await fetch(API_ROUTES.ARTIFACTS, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ index: source, id }),
  })

  if (!response.ok) {
    const errorData = await response.json().catch(() => null)
    throw new Error(errorData?.error || `Failed to fetch artifact (${response.status})`)
  }

  return response.json()
}

export function useArtifactDetail(id: string, source: string) {
  return useQuery({
    queryKey: ['artifact', source, id],
    queryFn: () => fetchArtifact(id, source),
    enabled: !!id,
    staleTime: Infinity,
    gcTime: TWENTY_FOUR_HOURS,
    refetchOnMount: false,
    refetchOnReconnect: false,
    refetchOnWindowFocus: false,
    retry: false,
  })
}
