// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import React from 'react'
import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useChat } from '../useChat'
import { sendMessageStream, type StreamCallbacks } from '@/services/chat'
import { useSessionStore } from '@/stores/sessionStore'
import { toast } from 'sonner'

// Mock the chat service
jest.mock('@/services/chat', () => ({
  sendMessageStream: jest.fn(),
}))

// Mock sonner toast
jest.mock('sonner', () => ({
  toast: {
    error: jest.fn(),
  },
}))

const mockSendMessageStream = sendMessageStream as jest.MockedFunction<typeof sendMessageStream>
const mockToastError = toast.error as jest.MockedFunction<typeof toast.error>

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  })

  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

describe('useChat', () => {
  let warnSpy: jest.SpyInstance

  beforeEach(() => {
    jest.clearAllMocks()
    warnSpy = jest.spyOn(console, 'warn').mockImplementation(() => {})
    // Reset the store state before each test
    useSessionStore.setState({
      sessionId: null,
      selectedAction: null,
      selectedTopic: null,
      messages: [],
      isLoading: false,
      recentChatHistoryVisibleDesktop: false,
      recentChatHistoryVisibleMobile: false,
      chatMode: 'discovery',
      selectedPromptId: null,
    })
  })

  afterEach(() => {
    warnSpy.mockRestore()
  })

  it('returns initial state', () => {
    const { result } = renderHook(() => useChat(), { wrapper: createWrapper() })

    expect(result.current.isLoading).toBe(false)
    expect(typeof result.current.sendMessage).toBe('function')
    expect(typeof result.current.respondToExisting).toBe('function')
    expect(typeof result.current.cancelStream).toBe('function')
  })

  it('generates session ID on first message', async () => {
    mockSendMessageStream.mockImplementation(async (_request, callbacks) => {
      callbacks.onDelta?.('Hello!')
      callbacks.onFinal?.('Hello!', undefined)
      callbacks.onComplete?.()
      return new AbortController()
    })

    const { result } = renderHook(() => useChat(), { wrapper: createWrapper() })

    // Initially no session ID
    expect(useSessionStore.getState().sessionId).toBeNull()

    await act(async () => {
      await result.current.sendMessage('Hi there')
    })

    // Session ID should now be set (UUID format)
    const sessionId = useSessionStore.getState().sessionId
    expect(sessionId).not.toBeNull()
    expect(sessionId).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
    )
  })

  it('reuses existing session ID', async () => {
    useSessionStore.setState({ sessionId: 'existing-session-id' })

    mockSendMessageStream.mockImplementation(async (_request, callbacks) => {
      callbacks.onFinal?.('Response', undefined)
      callbacks.onComplete?.()
      return new AbortController()
    })

    const { result } = renderHook(() => useChat(), { wrapper: createWrapper() })

    await act(async () => {
      await result.current.sendMessage('Hi')
    })

    // Should still have the same session ID
    expect(useSessionStore.getState().sessionId).toBe('existing-session-id')

    // Request should include the session ID
    const callArgs = mockSendMessageStream.mock.calls[0][0]
    expect(callArgs.sessionId).toBe('existing-session-id')
  })

  it('sends message and adds user message optimistically', async () => {
    mockSendMessageStream.mockImplementation(async (_request, callbacks) => {
      callbacks.onDelta?.('Hello!')
      callbacks.onFinal?.('Hello!', undefined)
      callbacks.onComplete?.()
      return new AbortController()
    })

    const { result } = renderHook(() => useChat(), { wrapper: createWrapper() })

    await act(async () => {
      const success = await result.current.sendMessage('Hi there')
      expect(success).toBe(true)
    })

    // Check that user message was added to store
    const state = useSessionStore.getState()
    expect(state.messages).toHaveLength(2) // User message + assistant response
    expect(state.messages[0].role).toBe('user')
    expect(state.messages[0].content).toBe('Hi there')
    expect(state.messages[1].role).toBe('assistant')
    expect(state.messages[1].content).toBe('Hello!')
  })

  it('blocks sending while already streaming', async () => {
    let completeCallback: (() => void) | null = null

    mockSendMessageStream.mockImplementation(async (_request, callbacks) => {
      // Store the complete callback to call later
      completeCallback = () => {
        callbacks.onFinal?.('Done', undefined)
        callbacks.onComplete?.()
      }
      callbacks.onDelta?.('Loading...')
      return new AbortController()
    })

    const { result } = renderHook(() => useChat(), { wrapper: createWrapper() })

    // Start first message (don't await)
    act(() => {
      result.current.sendMessage('First message')
    })

    // Wait for loading state
    await waitFor(() => {
      expect(result.current.isLoading).toBe(true)
    })

    // Try to send second message while streaming
    let secondSuccess: boolean | undefined
    await act(async () => {
      secondSuccess = await result.current.sendMessage('Second message')
    })

    // Second message should be blocked
    expect(secondSuccess).toBe(false)
    expect(warnSpy).toHaveBeenCalledWith(
      'Cannot send message while previous message is still streaming'
    )

    // Only first message should have been sent
    expect(mockSendMessageStream).toHaveBeenCalledTimes(1)

    // Complete the stream
    await act(async () => {
      completeCallback?.()
    })
  })

  it('streams content incrementally via deltas', async () => {
    mockSendMessageStream.mockImplementation(async (_request, callbacks) => {
      callbacks.onDelta?.('Hello')
      callbacks.onDelta?.(' world')
      callbacks.onDelta?.('!')
      callbacks.onFinal?.('Hello world!', undefined)
      callbacks.onComplete?.()
      return new AbortController()
    })

    const { result } = renderHook(() => useChat(), { wrapper: createWrapper() })

    await act(async () => {
      await result.current.sendMessage('Hi')
    })

    const state = useSessionStore.getState()
    expect(state.messages[1].content).toBe('Hello world!')
  })

  it('includes attachments from final event', async () => {
    const mockAttachments = [
      {
        id: 'att-1',
        type: 'letter' as const,
        title: 'Test Letter',
        url: 'https://example.com/letter.jpg',
      },
    ]

    mockSendMessageStream.mockImplementation(async (_request, callbacks) => {
      callbacks.onDelta?.('Here is a letter')
      callbacks.onFinal?.('Here is a letter', mockAttachments)
      callbacks.onComplete?.()
      return new AbortController()
    })

    const { result } = renderHook(() => useChat(), { wrapper: createWrapper() })

    await act(async () => {
      await result.current.sendMessage('Show me a letter')
    })

    const state = useSessionStore.getState()
    expect(state.messages[1].attachments).toEqual(mockAttachments)
  })

  it('includes action and topic context in request', async () => {
    useSessionStore.setState({
      selectedAction: 'analyze',
      selectedTopic: 'conservation',
    })

    mockSendMessageStream.mockImplementation(async (_request, callbacks) => {
      callbacks.onDelta?.('Response')
      callbacks.onFinal?.('Response', undefined)
      callbacks.onComplete?.()
      return new AbortController()
    })

    const { result } = renderHook(() => useChat(), { wrapper: createWrapper() })

    await act(async () => {
      await result.current.sendMessage('Tell me more')
    })

    expect(mockSendMessageStream).toHaveBeenCalled()
    const callArgs = mockSendMessageStream.mock.calls[0][0]
    expect(callArgs.context).toEqual({
      action: 'analyze',
      topic: 'conservation',
    })
  })

  it('handles error from stream and shows toast', async () => {
    mockSendMessageStream.mockImplementation(async (_request, callbacks) => {
      callbacks.onError?.('Network error')
      callbacks.onComplete?.()
      return new AbortController()
    })

    const { result } = renderHook(() => useChat(), { wrapper: createWrapper() })

    await act(async () => {
      await result.current.sendMessage('Hi')
    })

    await waitFor(() => {
      const state = useSessionStore.getState()
      expect(state.messages).toHaveLength(2)
      expect(state.messages[1].content).toContain('Sorry, something went wrong')
    })

    expect(mockToastError).toHaveBeenCalledWith('Connection Failed', {
      description: 'Please try again in a few moments.',
    })
  })

  it('handles connection error and shows toast', async () => {
    mockSendMessageStream.mockRejectedValueOnce(new Error('Connection refused'))

    const { result } = renderHook(() => useChat(), { wrapper: createWrapper() })

    await act(async () => {
      const success = await result.current.sendMessage('Hi')
      expect(success).toBe(false)
    })

    expect(mockToastError).toHaveBeenCalledWith('Connection Failed', {
      description: 'Please try again in a few moments.',
    })
  })

  it('handles agent error without showing toast', async () => {
    mockSendMessageStream.mockImplementation(async (_request, callbacks) => {
      callbacks.onAgentError?.('Agent cannot process this query')
      callbacks.onComplete?.()
      return new AbortController()
    })

    const { result } = renderHook(() => useChat(), { wrapper: createWrapper() })

    await act(async () => {
      await result.current.sendMessage('What questions have I asked?')
    })

    await waitFor(() => {
      const state = useSessionStore.getState()
      expect(state.messages).toHaveLength(2)
      expect(state.messages[1].content).toContain('not able to help with that request')
    })

    // Agent errors should NOT show a toast
    expect(mockToastError).not.toHaveBeenCalled()
  })

  it('sets isLoading during streaming', async () => {
    let completeCallback: (() => void) | null = null

    mockSendMessageStream.mockImplementation(async (_request, callbacks) => {
      completeCallback = () => {
        callbacks.onFinal?.('Done', undefined)
        callbacks.onComplete?.()
      }
      callbacks.onDelta?.('Loading...')
      return new AbortController()
    })

    const { result } = renderHook(() => useChat(), { wrapper: createWrapper() })

    expect(result.current.isLoading).toBe(false)

    act(() => {
      result.current.sendMessage('Hi')
    })

    await waitFor(() => {
      expect(result.current.isLoading).toBe(true)
    })

    await act(async () => {
      completeCallback?.()
    })

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false)
    })
  })

  it('cancelStream aborts the request', async () => {
    let passedController: AbortController | undefined
    mockSendMessageStream.mockImplementation(async (_request, _callbacks, controller) => {
      // Don't call callbacks — leave stream open for cancellation
      passedController = controller
      return controller!
    })

    const { result } = renderHook(() => useChat(), { wrapper: createWrapper() })

    await act(async () => {
      await result.current.sendMessage('Hi')
    })

    expect(passedController).toBeDefined()
    const abortSpy = jest.spyOn(passedController!, 'abort')

    act(() => {
      result.current.cancelStream()
    })

    expect(abortSpy).toHaveBeenCalled()
    expect(result.current.isLoading).toBe(false)
  })

  describe('respondToExisting', () => {
    it('does not add user message, only assistant response', async () => {
      mockSendMessageStream.mockImplementation(async (_request, callbacks) => {
        callbacks.onDelta?.('Hello!')
        callbacks.onFinal?.('Hello!', undefined)
        callbacks.onComplete?.()
        return new AbortController()
      })

      const { result } = renderHook(() => useChat(), { wrapper: createWrapper() })

      await act(async () => {
        await result.current.respondToExisting('User query')
      })

      // Should only have assistant message, not user message
      const state = useSessionStore.getState()
      expect(state.messages).toHaveLength(1)
      expect(state.messages[0].role).toBe('assistant')
      expect(state.messages[0].content).toBe('Hello!')
    })

    it('blocks while already streaming', async () => {
      let completeCallback: (() => void) | null = null

      mockSendMessageStream.mockImplementation(async (_request, callbacks) => {
        completeCallback = () => {
          callbacks.onFinal?.('Done', undefined)
          callbacks.onComplete?.()
        }
        callbacks.onDelta?.('Loading...')
        return new AbortController()
      })

      const { result } = renderHook(() => useChat(), { wrapper: createWrapper() })

      // Start first request
      act(() => {
        result.current.respondToExisting('First')
      })

      await waitFor(() => {
        expect(result.current.isLoading).toBe(true)
      })

      // Try second request while streaming
      let secondSuccess: boolean | undefined
      await act(async () => {
        secondSuccess = await result.current.respondToExisting('Second')
      })

      expect(secondSuccess).toBe(false)
      expect(warnSpy).toHaveBeenCalledWith(
        'Cannot send message while previous message is still streaming'
      )
      expect(mockSendMessageStream).toHaveBeenCalledTimes(1)

      // Cleanup
      await act(async () => {
        completeCallback?.()
      })
    })

    it('shows toast on error', async () => {
      mockSendMessageStream.mockImplementation(async (_request, callbacks) => {
        callbacks.onError?.('Backend error')
        callbacks.onComplete?.()
        return new AbortController()
      })

      const { result } = renderHook(() => useChat(), { wrapper: createWrapper() })

      await act(async () => {
        await result.current.respondToExisting('Query')
      })

      expect(mockToastError).toHaveBeenCalledWith('Connection Failed', {
        description: 'Please try again in a few moments.',
      })
    })
  })
})
