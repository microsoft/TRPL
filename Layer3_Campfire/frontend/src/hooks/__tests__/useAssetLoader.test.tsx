/**
 * @jest-environment jsdom
 */

import { act, renderHook } from '@testing-library/react'
import { useAssetLoader } from '../useAssetLoader'

class MockImage {
  static instances: MockImage[] = []

  onload: (() => void) | null = null
  onerror: (() => void) | null = null

  set src(_value: string) {
    MockImage.instances.push(this)
  }
}

describe('useAssetLoader', () => {
  beforeEach(() => {
    MockImage.instances = []
    Object.defineProperty(window, 'Image', {
      configurable: true,
      value: MockImage,
    })
  })

  it('is immediately ready when no critical assets are configured', () => {
    const { result } = renderHook(() => useAssetLoader([]))

    expect(result.current.isReady).toBe(true)
    expect(MockImage.instances).toHaveLength(0)
  })

  it('waits until every configured critical asset settles', () => {
    const paths = ['/assets/background.webp', '/assets/overlay.webp']
    const { result } = renderHook(() => useAssetLoader(paths))

    expect(result.current.isReady).toBe(false)
    expect(MockImage.instances).toHaveLength(2)

    act(() => MockImage.instances[0].onload?.())
    expect(result.current.isReady).toBe(false)

    act(() => MockImage.instances[1].onerror?.())
    expect(result.current.isReady).toBe(true)
  })
})
