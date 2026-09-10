'use client'

import { useEffect, useState } from 'react'

const DEFAULT_LOAD_TIMEOUT_MS = 5000

/**
 * Wait for explicitly configured critical images before starting the home-page
 * entrance animation. Gallery images are intentionally excluded because their
 * image components already manage eager and lazy loading.
 */
export function useAssetLoader(
  assetPaths: readonly string[],
  timeoutMs: number = DEFAULT_LOAD_TIMEOUT_MS
): { isReady: boolean } {
  const [isReady, setIsReady] = useState(assetPaths.length === 0)

  useEffect(() => {
    const uniquePaths = [...new Set(assetPaths)]
    if (uniquePaths.length === 0) {
      setIsReady(true)
      return
    }

    setIsReady(false)
    let settledCount = 0
    let completed = false

    const markComplete = () => {
      if (!completed) {
        completed = true
        setIsReady(true)
      }
    }

    const handleSettled = () => {
      settledCount += 1
      if (settledCount === uniquePaths.length) {
        markComplete()
      }
    }

    const images = uniquePaths.map((src) => {
      const image = new Image()
      image.onload = handleSettled
      image.onerror = handleSettled
      image.src = src
      return image
    })

    const timeoutId = window.setTimeout(markComplete, timeoutMs)

    return () => {
      completed = true
      window.clearTimeout(timeoutId)
      images.forEach((image) => {
        image.onload = null
        image.onerror = null
      })
    }
  }, [assetPaths, timeoutMs])

  return { isReady }
}
