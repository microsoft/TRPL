// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * @jest-environment node
 */
import { sendMessageStream, sendMessage, type StreamCallbacks } from '../chat'
import type { ChatRequest } from '@/schemas/chat'
import { API_ROUTES } from '@/lib/constants'

/** Build a mock Response with a streaming body */
function streamingResponse(events: string[], status = 200): Response {
  const encoder = new TextEncoder()
  const stream = new ReadableStream({
    start(controller) {
      for (const evt of events) {
        controller.enqueue(encoder.encode(evt))
      }
      controller.close()
    },
  })
  return new Response(stream, {
    status,
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

function sse(payload: object | string): string {
  const body = typeof payload === 'string' ? `"${payload}"` : JSON.stringify(payload)
  return `data: ${body}\n\n`
}

/**
 * Calls sendMessageStream and resolves when the stream signals completion
 * (via the onComplete callback). Eliminates arbitrary setTimeout waits.
 */
async function streamToCompletion(
  request: ChatRequest,
  callbacks: StreamCallbacks
): Promise<void> {
  return new Promise<void>((resolve) => {
    sendMessageStream(request, {
      ...callbacks,
      onComplete: () => {
        callbacks.onComplete?.()
        resolve()
      },
    })
  })
}

beforeEach(() => {
  jest.useRealTimers()
  global.fetch = jest.fn() as unknown as typeof fetch
})

describe('sendMessageStream', () => {
  test('invokes onDelta for each delta event', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue(
      streamingResponse([
        sse({ type: 'delta', content: 'Hello ' }),
        sse({ type: 'delta', content: 'world' }),
        sse({ type: 'final', content: 'Hello world' }),
        sse('[DONE]'),
      ])
    )

    const onDelta = jest.fn()
    const onFinal = jest.fn()
    const onComplete = jest.fn()

    await streamToCompletion(
      { message: 'hi', sessionId: 'sess-1' },
      { onDelta, onFinal, onComplete }
    )

    expect(onDelta).toHaveBeenCalledTimes(2)
    expect(onDelta).toHaveBeenNthCalledWith(1, 'Hello ')
    expect(onDelta).toHaveBeenNthCalledWith(2, 'world')
    expect(onFinal).toHaveBeenCalledWith('Hello world', undefined)
    expect(onComplete).toHaveBeenCalled()
  })

  test('invokes onFinal with attachments', async () => {
    const attachments = [{ id: 'a1', type: 'letter', title: 'A', url: 'https://x' }]
    ;(global.fetch as jest.Mock).mockResolvedValue(
      streamingResponse([
        sse({ type: 'final', content: 'done', attachments }),
        sse('[DONE]'),
      ])
    )

    const onFinal = jest.fn()
    await streamToCompletion({ message: 'hi', sessionId: 's' }, { onFinal })

    expect(onFinal).toHaveBeenCalledWith('done', attachments)
  })

  test('invokes onAgentError for agent_error event', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue(
      streamingResponse([
        sse({ type: 'agent_error', error: 'agent blew up' }),
        sse('[DONE]'),
      ])
    )

    const onAgentError = jest.fn()
    const onError = jest.fn()
    await streamToCompletion({ message: 'hi', sessionId: 's' }, { onAgentError, onError })

    expect(onAgentError).toHaveBeenCalledWith('agent blew up')
    expect(onError).not.toHaveBeenCalled()
  })

  test('invokes onError for error event', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue(
      streamingResponse([
        sse({ type: 'error', error: 'connection lost' }),
        sse('[DONE]'),
      ])
    )

    const onError = jest.fn()
    await streamToCompletion({ message: 'hi', sessionId: 's' }, { onError })

    expect(onError).toHaveBeenCalledWith('connection lost')
  })

  test('invokes onProgress for progress events', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue(
      streamingResponse([
        sse({ type: 'progress', progress: 'Searching' }),
        sse('[DONE]'),
      ])
    )

    const onProgress = jest.fn()
    await streamToCompletion({ message: 'hi', sessionId: 's' }, { onProgress })

    expect(onProgress).toHaveBeenCalledWith('Searching')
  })

  test('reports HTTP error when response is not OK', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue(
      new Response('Too many requests', { status: 429 })
    )

    const onError = jest.fn()
    const onComplete = jest.fn()
    await sendMessageStream({ message: 'hi', sessionId: 's' }, { onError, onComplete })

    expect(onError).toHaveBeenCalledWith(expect.stringContaining('429'))
    expect(onError).toHaveBeenCalledWith(expect.stringContaining('Too many requests'))
    expect(onComplete).toHaveBeenCalled()
  })

  test('handles missing response body', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      body: null,
      status: 200,
    })

    const onError = jest.fn()
    const onComplete = jest.fn()
    await sendMessageStream({ message: 'hi', sessionId: 's' }, { onError, onComplete })

    expect(onError).toHaveBeenCalledWith('No response body received')
    expect(onComplete).toHaveBeenCalled()
  })

  test('silently handles AbortError on fetch', async () => {
    const abortController = new AbortController()
    abortController.abort()
    ;(global.fetch as jest.Mock).mockRejectedValue(
      Object.assign(new Error('aborted'), { name: 'AbortError' })
    )

    const onError = jest.fn()
    const onComplete = jest.fn()
    await sendMessageStream(
      { message: 'hi', sessionId: 's' },
      { onError, onComplete },
      abortController
    )

    expect(onError).not.toHaveBeenCalled()
    expect(onComplete).not.toHaveBeenCalled()
  })

  test('reports generic fetch failure via onError', async () => {
    ;(global.fetch as jest.Mock).mockRejectedValue(new Error('network down'))

    const onError = jest.fn()
    await sendMessageStream({ message: 'hi', sessionId: 's' }, { onError })

    expect(onError).toHaveBeenCalledWith('network down')
  })

  test('returns the provided AbortController so callers can cancel', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue(streamingResponse([sse('[DONE]')]))

    const ac = new AbortController()
    const result = await sendMessageStream(
      { message: 'hi', sessionId: 's' },
      {},
      ac
    )
    expect(result).toBe(ac)
  })

  test('validates request before sending', async () => {
    await expect(
      sendMessageStream({ message: '', sessionId: 's' }, {})
    ).rejects.toThrow()
    expect(global.fetch).not.toHaveBeenCalled()
  })

  test('posts to chat route with JSON body', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue(streamingResponse([sse('[DONE]')]))

    await sendMessageStream(
      { message: 'hi', sessionId: 's', agent: 'default' },
      {}
    )

    expect(global.fetch).toHaveBeenCalledWith(
      API_ROUTES.CHAT,
      expect.objectContaining({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: expect.stringContaining('"message":"hi"'),
      })
    )
  })

  test('handles split-line buffering', async () => {
    const encoder = new TextEncoder()
    const stream = new ReadableStream({
      start(controller) {
        // Send a delta split across two chunks
        controller.enqueue(encoder.encode('data: {"type":"delta","cont'))
        controller.enqueue(encoder.encode('ent":"ABC"}\n\n'))
        controller.enqueue(encoder.encode('data: "[DONE]"\n\n'))
        controller.close()
      },
    })
    ;(global.fetch as jest.Mock).mockResolvedValue(
      new Response(stream, { status: 200 })
    )

    const onDelta = jest.fn()
    await streamToCompletion({ message: 'hi', sessionId: 's' }, { onDelta })

    expect(onDelta).toHaveBeenCalledWith('ABC')
  })
})

describe('sendMessage (non-streaming wrapper)', () => {
  test('resolves with accumulated content and final attachments', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue(
      streamingResponse([
        sse({ type: 'delta', content: 'Hello ' }),
        sse({ type: 'delta', content: 'world' }),
        sse({ type: 'final', content: 'Hello world', attachments: [] }),
        sse('[DONE]'),
      ])
    )

    const result = await sendMessage({ message: 'hi', sessionId: 's' })
    expect(result.content).toBe('Hello world')
    expect(result.attachments).toEqual([])
  })

  test('rejects on error event', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue(
      streamingResponse([sse({ type: 'error', error: 'oh no' }), sse('[DONE]')])
    )

    await expect(sendMessage({ message: 'hi', sessionId: 's' })).rejects.toThrow('oh no')
  })
})
