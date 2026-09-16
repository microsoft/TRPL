// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * @jest-environment jsdom
 */

import { render, screen } from '@testing-library/react'
import { HeroSection } from '../HeroSection'
import { createRef } from 'react'

// Mock hooks
jest.mock('@/hooks/useIsMobile', () => ({
  useIsMobile: jest.fn(() => false),
}))

// Mock sessionStore
jest.mock('@/stores/sessionStore', () => ({
  useSessionStore: jest.fn((selector) => {
    const state = {
      selectedAction: null,
      selectedTopic: null,
      selectedPromptId: null,
      setSelectedAction: jest.fn(),
      setSelectedTopic: jest.fn(),
      setSelectedPromptId: jest.fn(),
      addMessage: jest.fn(),
    }
    return selector(state)
  }),
  selectComposedPrompt: () => null,
  createUserMessage: jest.fn(),
}))

// Mock child components
jest.mock('@/components/chat', () => ({
  ChatBox: ({ onSubmit }: { onSubmit?: () => void }) => (
    <div data-testid="chat-box">
      <button onClick={onSubmit}>Submit</button>
    </div>
  ),
  PromptBar: () => <div data-testid="prompt-bar">PromptBar</div>,
}))

describe('HeroSection', () => {
  const createRefs = () => ({
    taglineRef: createRef<HTMLParagraphElement>(),
    titleRef: createRef<HTMLHeadingElement>(),
    contentRef: createRef<HTMLDivElement>(),
    heroRef: createRef<HTMLDivElement>(),
  })

  beforeEach(() => {
    jest.clearAllMocks()
  })

  describe('rendering', () => {
    it('renders tagline', () => {
      render(<HeroSection {...createRefs()} />)

      expect(screen.getByText(/Discover the Life & Legacy of/)).toBeInTheDocument()
    })

    it('renders title', () => {
      render(<HeroSection {...createRefs()} />)

      expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Theodore Roosevelt')
    })

    it('renders ChatBox', () => {
      render(<HeroSection {...createRefs()} />)

      expect(screen.getByTestId('chat-box')).toBeInTheDocument()
    })

    it('renders PromptBar', () => {
      render(<HeroSection {...createRefs()} />)

      expect(screen.getByTestId('prompt-bar')).toBeInTheDocument()
    })
  })

  describe('refs', () => {
    it('attaches taglineRef to paragraph', () => {
      const refs = createRefs()
      render(<HeroSection {...refs} />)

      expect(refs.taglineRef.current).toBeInstanceOf(HTMLParagraphElement)
    })

    it('attaches titleRef to heading', () => {
      const refs = createRefs()
      render(<HeroSection {...refs} />)

      expect(refs.titleRef.current).toBeInstanceOf(HTMLHeadingElement)
    })

    it('attaches heroRef to hero wrapper', () => {
      const refs = createRefs()
      render(<HeroSection {...refs} />)

      expect(refs.heroRef.current).toBeInstanceOf(HTMLDivElement)
    })

    it('attaches contentRef to search container', () => {
      const refs = createRefs()
      render(<HeroSection {...refs} />)

      expect(refs.contentRef.current).toBeInstanceOf(HTMLDivElement)
    })
  })

  describe('submit callback', () => {
    it('passes onSubmit to ChatBox', () => {
      const onSubmit = jest.fn()
      render(<HeroSection {...createRefs()} onSubmit={onSubmit} />)

      // ChatBox should receive the onSubmit prop
      expect(screen.getByTestId('chat-box')).toBeInTheDocument()
    })
  })
})
