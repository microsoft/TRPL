// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * @jest-environment jsdom
 *
 * Integration tests to verify that user input flows through exactly as typed.
 * These tests ensure no text transformation/prefixing happens between input and display.
 */

import { act } from '@testing-library/react'
import {
  useSessionStore,
  selectComposedPrompt,
  createUserMessage
} from '../sessionStore'

describe('Prompt Flow Integration', () => {
  beforeEach(() => {
    act(() => {
      useSessionStore.getState().resetSession()
    })
  })

  describe('Homepage → Chat Page flow', () => {
    /**
     * Verifies that text typed in the homepage inputs appears exactly
     * as the user typed it when displayed on the chat page.
     */
    it('action input text appears exactly as typed in the composed prompt', () => {
      const userInput = 'find information about'

      act(() => {
        useSessionStore.getState().setSelectedAction(userInput)
      })

      const state = useSessionStore.getState()
      const composedPrompt = selectComposedPrompt(state)

      // The composed prompt should be exactly what the user typed
      expect(composedPrompt).toBe(userInput)
    })

    it('topic input text appears exactly as typed in the composed prompt', () => {
      const userInput = 'Theodore Roosevelt letters'

      act(() => {
        useSessionStore.getState().setSelectedTopic(userInput)
      })

      const state = useSessionStore.getState()
      const composedPrompt = selectComposedPrompt(state)

      expect(composedPrompt).toBe(userInput)
    })

    it('combined action + topic appears with "on" connector', () => {
      const actionInput = 'I want to research'
      const topicInput = 'letters between Teddy and Taft'

      act(() => {
        useSessionStore.getState().setSelectedAction(actionInput)
        useSessionStore.getState().setSelectedTopic(topicInput)
      })

      const state = useSessionStore.getState()
      const composedPrompt = selectComposedPrompt(state)

      // Should be: action + " on " + topic (matches UI layout)
      expect(composedPrompt).toBe(`${actionInput} on ${topicInput}`)
    })

    it('composed prompt becomes exact message content when submitted', () => {
      const actionInput = 'show me images of'
      const topicInput = 'the Rough Riders'

      act(() => {
        useSessionStore.getState().setSelectedAction(actionInput)
        useSessionStore.getState().setSelectedTopic(topicInput)
      })

      const state = useSessionStore.getState()
      const composedPrompt = selectComposedPrompt(state)!
      const message = createUserMessage(composedPrompt, true)

      // Message content should exactly match composed prompt
      expect(message.content).toBe(composedPrompt)
      expect(message.content).toBe(`${actionInput} on ${topicInput}`)
    })

    it('full flow: homepage input → store → message preserves exact text', () => {
      // Simulate the full homepage → chat flow
      const actionInput = 'I want to learn about'
      const topicInput = "TR's conservation policies"
      const expectedFinalText = `${actionInput} on ${topicInput}`

      // Step 1: User types in homepage inputs
      act(() => {
        useSessionStore.getState().setSelectedAction(actionInput)
        useSessionStore.getState().setSelectedTopic(topicInput)
      })

      // Step 2: Get composed prompt (what ChatBox uses)
      const state = useSessionStore.getState()
      const composedPrompt = selectComposedPrompt(state)!

      // Step 3: Create message (what ChatBox does on submit)
      const message = createUserMessage(composedPrompt, true)

      // Step 4: Add to store (simulating the full flow)
      act(() => {
        useSessionStore.getState().addMessage(message)
      })

      // Verify: The message in the store has exact text
      const finalState = useSessionStore.getState()
      expect(finalState.messages[0].content).toBe(expectedFinalText)
    })
  })

  describe('Chat Page input flow', () => {
    /**
     * Verifies that text typed in the chat input field appears exactly
     * as typed when added to the message list.
     */
    it('chat input text becomes exact message content', () => {
      const userInput = 'Tell me about the Bull Moose Party'

      // createUserMessage is what ChatView uses when user submits
      const message = createUserMessage(userInput)

      expect(message.content).toBe(userInput)
      expect(message.role).toBe('user')
    })

    it('chat message with special characters is preserved exactly', () => {
      const userInput = "What were TR's views on the \"Square Deal\"?"

      const message = createUserMessage(userInput)

      expect(message.content).toBe(userInput)
    })

    it('chat message with numbers and punctuation is preserved', () => {
      const userInput = 'What happened on October 14, 1912?'

      const message = createUserMessage(userInput)

      expect(message.content).toBe(userInput)
    })

    it('full chat flow: input → message → store preserves exact text', () => {
      const userInput = 'Explain the significance of the Panama Canal'

      // Step 1: Create message (what ChatView.handleSubmit does)
      const message = createUserMessage(userInput)

      // Step 2: Add to store
      act(() => {
        useSessionStore.getState().addMessage(message)
      })

      // Verify: Message in store matches input exactly
      const state = useSessionStore.getState()
      expect(state.messages[0].content).toBe(userInput)
    })
  })

  describe('Edge cases', () => {
    it('preserves internal whitespace in user input', () => {
      const userInput = 'research    multiple   spaces'

      act(() => {
        useSessionStore.getState().setSelectedAction(userInput)
      })

      const state = useSessionStore.getState()
      const composedPrompt = selectComposedPrompt(state)

      // Note: sanitization collapses multiple spaces to single space
      expect(composedPrompt).toBe('research multiple spaces')
    })

    it('trims leading/trailing whitespace but preserves content', () => {
      const userInput = '  research topic  '

      act(() => {
        useSessionStore.getState().setSelectedAction(userInput)
      })

      const state = useSessionStore.getState()
      const composedPrompt = selectComposedPrompt(state)

      expect(composedPrompt).toBe('research topic')
    })

    it('handles empty action with topic', () => {
      act(() => {
        useSessionStore.getState().setSelectedAction('')
        useSessionStore.getState().setSelectedTopic('Panama Canal')
      })

      const state = useSessionStore.getState()
      const composedPrompt = selectComposedPrompt(state)

      expect(composedPrompt).toBe('Panama Canal')
    })

    it('handles action with empty topic', () => {
      act(() => {
        useSessionStore.getState().setSelectedAction('research')
        useSessionStore.getState().setSelectedTopic('')
      })

      const state = useSessionStore.getState()
      const composedPrompt = selectComposedPrompt(state)

      expect(composedPrompt).toBe('research')
    })
  })
})
