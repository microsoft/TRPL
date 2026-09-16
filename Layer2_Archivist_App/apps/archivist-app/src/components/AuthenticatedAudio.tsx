// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import React from 'react'
import { useAuthenticatedUrl } from '@/hooks/useAuthenticatedUrl'

type AuthenticatedAudioProps = Omit<React.AudioHTMLAttributes<HTMLAudioElement>, 'src'> & {
  rawUrl: string | undefined
  children?: React.ReactNode
}

const AuthenticatedAudio: React.FC<AuthenticatedAudioProps> = ({ rawUrl, children, ...audioProps }) => {
  const blobUrl = useAuthenticatedUrl(rawUrl)

  if (!blobUrl) return null

  return <audio {...audioProps} src={blobUrl}>{children}</audio>
}

export default AuthenticatedAudio
