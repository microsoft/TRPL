// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import React from 'react'
import { useAuthenticatedUrl } from '@/hooks/useAuthenticatedUrl'

type AuthenticatedImageProps = Omit<React.ImgHTMLAttributes<HTMLImageElement>, 'src'> & {
  rawUrl: string | undefined
}

const AuthenticatedImage: React.FC<AuthenticatedImageProps> = ({ rawUrl, alt, ...imgProps }) => {
  const blobUrl = useAuthenticatedUrl(rawUrl)

  if (!blobUrl) return null

  return <img {...imgProps} src={blobUrl} alt={alt ?? ''} />
}

export default AuthenticatedImage
