// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * @jest-environment jsdom
 */

import { applyHomeLeaveTransitionFallback, applyChatLeaveTransitionFallback } from '../animationFallback'

const createDivRef = () => {
  const el = document.createElement('div')
  return { current: el }
}

describe('applyHomeLeaveTransitionFallback', () => {
  it('sets end-state styles when home→chat animations fail or timeout', () => {
    const heroRef = createDivRef()
    const contentRef = createDivRef()
    const footerRef = createDivRef()
    const galleryRef = createDivRef()
    const backgroundOverlayRef = createDivRef()

    applyHomeLeaveTransitionFallback({
      heroRef,
      contentRef,
      galleryRef,
      footerRef,
      backgroundOverlayRef,
    })

    expect(backgroundOverlayRef.current.style.opacity).toBe('1')
    expect(heroRef.current.style.opacity).toBe('0')
    expect(heroRef.current.style.transform).toContain('-150')
    expect(contentRef.current.style.opacity).toBe('0')
    expect(contentRef.current.style.transform).toContain('-100')
    expect(footerRef.current.style.opacity).toBe('0')
    expect(footerRef.current.style.transform).toContain('-80')
    expect(galleryRef.current.style.opacity).toBe('0')
  })
})

describe('applyChatLeaveTransitionFallback', () => {
  it('sets end-state styles when chat→home animations fail or timeout', () => {
    const headerRef = createDivRef()
    const chatAreaRef = createDivRef()
    const chatbarRef = createDivRef()
    const artifactsSidebarRef = createDivRef()
    const backgroundOverlayRef = createDivRef()

    applyChatLeaveTransitionFallback({
      headerRef,
      chatAreaRef,
      chatbarRef,
      artifactsSidebarRef,
      backgroundOverlayRef,
    })

    expect(backgroundOverlayRef.current.style.opacity).toBe('1')
    expect(headerRef.current.style.opacity).toBe('0')
    expect(headerRef.current.style.transform).toContain('-150')
    expect(chatAreaRef.current.style.opacity).toBe('0')
    expect(chatAreaRef.current.style.transform).toContain('-100')
    expect(chatbarRef.current.style.opacity).toBe('0')
    expect(chatbarRef.current.style.transform).toContain('-80')
    expect(artifactsSidebarRef.current.style.transform).toContain('100%')
  })
})
