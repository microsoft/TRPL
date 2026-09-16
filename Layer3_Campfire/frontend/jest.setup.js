// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

// Jest setup file
// Add custom matchers and global test configuration

import '@testing-library/jest-dom'

// Use fake timers globally to prevent timer leaks causing worker exit issues
// Individual tests can call jest.useRealTimers() if needed
jest.useFakeTimers()

// Mock Next.js Image to avoid React warnings about non-DOM props in tests
jest.mock('next/image', () => ({
  __esModule: true,
  default: ({ src, alt, unoptimized, ...props }) => {
    const resolvedSrc = typeof src === 'string' ? src : src?.src || ''
    return <img src={resolvedSrc} alt={alt} {...props} />
  },
}))

// Mock ResizeObserver (not available in jsdom)
global.ResizeObserver = class ResizeObserver {
  constructor(callback) {
    this.callback = callback
  }
  observe() {}
  unobserve() {}
  disconnect() {}
}
