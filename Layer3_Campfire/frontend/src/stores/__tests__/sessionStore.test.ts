// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * @jest-environment jsdom
 */

import { useSessionStore, selectComposedPrompt, createUserMessage } from '../sessionStore'
import { act } from '@testing-library/react'
import type { Message } from '@/schemas/chat'

describe('sessionStore', () => {
  beforeEach(() => {
    // Reset store to initial state before each test
    act(() => {
      useSessionStore.getState().resetSession()
    })
  })

  describe('initial state', () => {
    it('starts with null sessionId', () => {
      const state = useSessionStore.getState()
      expect(state.sessionId).toBeNull()
    })

    it('starts with empty messages', () => {
      const state = useSessionStore.getState()
      expect(state.messages).toEqual([])
    })

    it('starts with isLoading false', () => {
      const state = useSessionStore.getState()
      expect(state.isLoading).toBe(false)
    })

    it('starts in discovery chatMode', () => {
      const state = useSessionStore.getState()
      expect(state.chatMode).toBe('discovery')
    })

    it('starts with recentChatHistoryVisibleDesktop false', () => {
      const state = useSessionStore.getState()
      expect(state.recentChatHistoryVisibleDesktop).toBe(false)
    })

    it('starts with recentChatHistoryVisibleMobile false', () => {
      const state = useSessionStore.getState()
      expect(state.recentChatHistoryVisibleMobile).toBe(false)
    })

    it('starts with no selections', () => {
      const state = useSessionStore.getState()
      expect(state.selectedAction).toBeNull()
      expect(state.selectedTopic).toBeNull()
      expect(state.selectedPromptId).toBeNull()
    })
  })

  describe('setSessionId', () => {
    it('sets the session ID', () => {
      act(() => {
        useSessionStore.getState().setSessionId('test-123')
      })

      expect(useSessionStore.getState().sessionId).toBe('test-123')
    })
  })

  describe('addMessage', () => {
    it('adds a message to the array', () => {
      const message: Message = {
        id: '1',
        role: 'user',
        content: 'Hello',
        timestamp: new Date().toISOString(),
      }

      act(() => {
        useSessionStore.getState().addMessage(message)
      })

      const state = useSessionStore.getState()
      expect(state.messages).toHaveLength(1)
      expect(state.messages[0]).toEqual(message)
    })

    it('appends multiple messages', () => {
      const message1: Message = {
        id: '1',
        role: 'user',
        content: 'Hello',
        timestamp: new Date().toISOString(),
      }
      const message2: Message = {
        id: '2',
        role: 'assistant',
        content: 'Hi there!',
        timestamp: new Date().toISOString(),
      }

      act(() => {
        useSessionStore.getState().addMessage(message1)
        useSessionStore.getState().addMessage(message2)
      })

      const state = useSessionStore.getState()
      expect(state.messages).toHaveLength(2)
      expect(state.messages[0].content).toBe('Hello')
      expect(state.messages[1].content).toBe('Hi there!')
    })
  })

  describe('clearMessages', () => {
    it('clears all messages', () => {
      const message: Message = {
        id: '1',
        role: 'user',
        content: 'Hello',
        timestamp: new Date().toISOString(),
      }

      act(() => {
        useSessionStore.getState().addMessage(message)
        useSessionStore.getState().clearMessages()
      })

      expect(useSessionStore.getState().messages).toEqual([])
    })
  })

  describe('setIsLoading', () => {
    it('sets loading to true', () => {
      act(() => {
        useSessionStore.getState().setIsLoading(true)
      })

      expect(useSessionStore.getState().isLoading).toBe(true)
    })

    it('sets loading to false', () => {
      act(() => {
        useSessionStore.getState().setIsLoading(true)
        useSessionStore.getState().setIsLoading(false)
      })

      expect(useSessionStore.getState().isLoading).toBe(false)
    })
  })

  describe('setChatMode', () => {
    it('sets chat mode', () => {
      act(() => {
        useSessionStore.getState().setChatMode('research')
      })

      expect(useSessionStore.getState().chatMode).toBe('research')
    })
  })

  describe('artifacts visibility', () => {
    it('setRecentChatHistoryVisibleDesktop sets the visibility', () => {
      act(() => {
        useSessionStore.getState().setRecentChatHistoryVisibleDesktop(false)
      })

      expect(useSessionStore.getState().recentChatHistoryVisibleDesktop).toBe(false)
    })

    it('toggleRecentChatHistoryDesktop toggles the visibility', () => {
      const initialValue = useSessionStore.getState().recentChatHistoryVisibleDesktop

      act(() => {
        useSessionStore.getState().toggleRecentChatHistoryDesktop()
      })

      expect(useSessionStore.getState().recentChatHistoryVisibleDesktop).toBe(!initialValue)
    })

    it('setRecentChatHistoryVisibleMobile sets the visibility', () => {
      act(() => {
        useSessionStore.getState().setRecentChatHistoryVisibleMobile(false)
      })

      expect(useSessionStore.getState().recentChatHistoryVisibleMobile).toBe(false)
    })

    it('toggleRecentChatHistoryMobile toggles the visibility', () => {
      const initialValue = useSessionStore.getState().recentChatHistoryVisibleMobile

      act(() => {
        useSessionStore.getState().toggleRecentChatHistoryMobile()
      })

      expect(useSessionStore.getState().recentChatHistoryVisibleMobile).toBe(!initialValue)
    })
  })

  describe('selections', () => {
    it('setSelectedAction sets action', () => {
      act(() => {
        useSessionStore.getState().setSelectedAction('research')
      })

      expect(useSessionStore.getState().selectedAction).toBe('research')
    })

    it('setSelectedAction clears with null', () => {
      act(() => {
        useSessionStore.getState().setSelectedAction('research')
        useSessionStore.getState().setSelectedAction(null)
      })

      expect(useSessionStore.getState().selectedAction).toBeNull()
    })

    it('setSelectedTopic sets topic', () => {
      act(() => {
        useSessionStore.getState().setSelectedTopic('world war')
      })

      expect(useSessionStore.getState().selectedTopic).toBe('world war')
    })

    it('setSelectedPromptId sets prompt ID', () => {
      act(() => {
        useSessionStore.getState().setSelectedPromptId('prompt-1')
      })

      expect(useSessionStore.getState().selectedPromptId).toBe('prompt-1')
    })

    // Regression tests: spaces should NOT be stripped during typing
    // Sanitization happens at compose time, not keystroke time
    it('setSelectedAction preserves trailing spaces during typing', () => {
      act(() => {
        useSessionStore.getState().setSelectedAction('research ')
      })

      // Raw value should preserve the space for typing UX
      expect(useSessionStore.getState().selectedAction).toBe('research ')
    })

    it('setSelectedAction preserves leading spaces during typing', () => {
      act(() => {
        useSessionStore.getState().setSelectedAction(' research')
      })

      expect(useSessionStore.getState().selectedAction).toBe(' research')
    })

    it('setSelectedTopic preserves trailing spaces during typing', () => {
      act(() => {
        useSessionStore.getState().setSelectedTopic('world war ')
      })

      expect(useSessionStore.getState().selectedTopic).toBe('world war ')
    })

    it('setSelectedTopic preserves internal spaces', () => {
      act(() => {
        useSessionStore.getState().setSelectedTopic('world war history')
      })

      expect(useSessionStore.getState().selectedTopic).toBe('world war history')
    })
  })

  describe('resetSession', () => {
    it('resets all state to initial values', () => {
      // Set various state values
      act(() => {
        useSessionStore.getState().setSessionId('test')
        useSessionStore.getState().addMessage({
          id: '1',
          role: 'user',
          content: 'test',
          timestamp: new Date().toISOString(),
        })
        useSessionStore.getState().setIsLoading(true)
        useSessionStore.getState().setChatMode('research')
        useSessionStore.getState().setRecentChatHistoryVisibleDesktop(false)
        useSessionStore.getState().setRecentChatHistoryVisibleMobile(false)
        useSessionStore.getState().setSelectedAction('research')
        useSessionStore.getState().setSelectedTopic('topic')
        useSessionStore.getState().setSelectedPromptId('prompt')
      })

      // Reset
      act(() => {
        useSessionStore.getState().resetSession()
      })

      const state = useSessionStore.getState()
      expect(state.sessionId).toBeNull()
      expect(state.messages).toEqual([])
      expect(state.isLoading).toBe(false)
      expect(state.chatMode).toBe('discovery')
      expect(state.recentChatHistoryVisibleDesktop).toBe(false)
      expect(state.recentChatHistoryVisibleMobile).toBe(false)
      expect(state.selectedAction).toBeNull()
      expect(state.selectedTopic).toBeNull()
      expect(state.selectedPromptId).toBeNull()
    })
  })
})

describe('selectComposedPrompt', () => {
  beforeEach(() => {
    act(() => {
      useSessionStore.getState().resetSession()
    })
  })

  it('returns null when nothing selected', () => {
    const state = useSessionStore.getState()
    expect(selectComposedPrompt(state)).toBeNull()
  })

  it('returns action-only prompt', () => {
    act(() => {
      useSessionStore.getState().setSelectedAction('research')
    })

    const state = useSessionStore.getState()
    expect(selectComposedPrompt(state)).toBe('research')
  })

  it('returns topic-only prompt', () => {
    act(() => {
      useSessionStore.getState().setSelectedTopic('world war')
    })

    const state = useSessionStore.getState()
    expect(selectComposedPrompt(state)).toBe('world war')
  })

  it('returns combined prompt with "on" connector when both selected', () => {
    act(() => {
      useSessionStore.getState().setSelectedAction('research')
      useSessionStore.getState().setSelectedTopic('world war')
    })

    const state = useSessionStore.getState()
    // Matches UI: "I want to..." [on] "enter a topic..."
    expect(selectComposedPrompt(state)).toBe('research on world war')
  })

  it('returns exact user input with "on" connector', () => {
    act(() => {
      useSessionStore.getState().setSelectedAction('find me images')
      useSessionStore.getState().setSelectedTopic('the Rough Riders')
    })

    const state = useSessionStore.getState()
    expect(selectComposedPrompt(state)).toBe('find me images on the Rough Riders')
  })

  // Sanitization tests: verify cleanup happens at compose time
  it('trims trailing spaces from action at compose time', () => {
    act(() => {
      useSessionStore.getState().setSelectedAction('research ')
    })

    const state = useSessionStore.getState()
    // Raw value preserves space, but composed prompt is trimmed
    expect(state.selectedAction).toBe('research ')
    expect(selectComposedPrompt(state)).toBe('research')
  })

  it('trims leading spaces from topic at compose time', () => {
    act(() => {
      useSessionStore.getState().setSelectedTopic(' world war')
    })

    const state = useSessionStore.getState()
    expect(state.selectedTopic).toBe(' world war')
    expect(selectComposedPrompt(state)).toBe('world war')
  })

  it('preserves internal spaces in composed prompt', () => {
    act(() => {
      useSessionStore.getState().setSelectedAction('learn about')
      useSessionStore.getState().setSelectedTopic('world war history')
    })

    const state = useSessionStore.getState()
    expect(selectComposedPrompt(state)).toBe('learn about on world war history')
  })

  it('collapses multiple spaces in composed prompt', () => {
    act(() => {
      useSessionStore.getState().setSelectedAction('research  deeply')
    })

    const state = useSessionStore.getState()
    // Multiple spaces should be collapsed to single space
    expect(selectComposedPrompt(state)).toBe('research deeply')
  })

  it('returns null when input is only whitespace', () => {
    act(() => {
      useSessionStore.getState().setSelectedAction('   ')
      useSessionStore.getState().setSelectedTopic('   ')
    })

    const state = useSessionStore.getState()
    // After sanitization, whitespace-only becomes empty, so null
    expect(selectComposedPrompt(state)).toBeNull()
  })
})

describe('createUserMessage', () => {
  it('creates a message with user role', () => {
    const message = createUserMessage('Hello')
    expect(message.role).toBe('user')
  })

  it('includes content', () => {
    const message = createUserMessage('Hello world')
    expect(message.content).toBe('Hello world')
  })

  it('generates a unique ID', () => {
    const message1 = createUserMessage('Hello')
    const message2 = createUserMessage('World')
    expect(message1.id).not.toBe(message2.id)
  })

  it('includes timestamp', () => {
    const before = new Date().toISOString()
    const message = createUserMessage('Hello')
    const after = new Date().toISOString()

    expect(message.timestamp).toBeDefined()
    expect(message.timestamp >= before).toBe(true)
    expect(message.timestamp <= after).toBe(true)
  })

  it('sets isInitialQuery when true', () => {
    const message = createUserMessage('Hello', true)
    expect(message.isInitialQuery).toBe(true)
  })

  it('does not include isInitialQuery when false', () => {
    const message = createUserMessage('Hello', false)
    expect(message.isInitialQuery).toBeUndefined()
  })
})
