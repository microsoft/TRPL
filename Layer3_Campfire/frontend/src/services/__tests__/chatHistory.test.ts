// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * @jest-environment jsdom
 */
jest.mock('@/lib/anonymousUser', () => ({
  getOrCreateAnonymousUserId: jest.fn(() => 'anon-user-1'),
}))

import { chatHistoryService } from '../chatHistory'

beforeEach(() => {
  global.fetch = jest.fn() as unknown as typeof fetch
})

afterEach(() => {
  jest.restoreAllMocks()
})

describe('chatHistoryService.getChats', () => {
  test('GETs /api/chat-history/{userId} and returns chat IDs', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      statusText: 'OK',
      json: async () => [{ chat_id: 'chat-a', mode: 'discovery' }, { chat_id: 'chat-b', mode: 'research' }],
    })

    const result = await chatHistoryService.getChats()
    expect(result).toEqual([
      { chatId: 'chat-a', mode: 'discovery' },
      { chatId: 'chat-b', mode: 'research' },
    ])
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/chat-history/anon-user-1',
      expect.objectContaining({ method: 'GET' })
    )
  })

  test('throws on non-OK response', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue({
      ok: false,
      statusText: 'Server Error',
    })

    await expect(chatHistoryService.getChats()).rejects.toThrow(/Server Error/)
  })
})

describe('chatHistoryService.getChatMessages', () => {
  test('transforms backend timestamps and citations into Message format', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      statusText: 'OK',
      json: async () => [
        { role: 'user', text: 'hi', timestamp: 1700000000, citations: null },
        {
          role: 'assistant',
          text: 'hello',
          timestamp: 1700000001000, // already milliseconds
          citations: [
            {
              id: 'c1',
              source: 'letter',
              title: 'L1',
              trpl_file_url: ['https://img/1.jpg'],
            },
          ],
        },
      ],
    })

    const result = await chatHistoryService.getChatMessages('chat-a')

    expect(result).toHaveLength(2)
    expect(result[0].role).toBe('user')
    expect(result[0].content).toBe('hi')
    // Numeric seconds → ISO date
    expect(result[0].timestamp).toBe('2023-11-14T22:13:20.000Z')
    expect(result[0].attachments).toEqual([])

    expect(result[1].role).toBe('assistant')
    // Numeric ms → ISO date
    expect(result[1].timestamp).toBe('2023-11-14T22:13:21.000Z')
    expect(result[1].attachments).toHaveLength(1)
    expect(result[1].attachments![0].url).toBe('https://img/1.jpg')
  })

  test('encodes user and chat IDs in URL', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      statusText: 'OK',
      json: async () => [],
    })

    await chatHistoryService.getChatMessages('chat with space')

    const url = (global.fetch as jest.Mock).mock.calls[0][0]
    expect(url).toContain('anon-user-1')
    expect(url).toContain('chat%20with%20space')
    expect(url).toContain('/messages')
  })

  test('throws on non-OK response', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue({
      ok: false,
      statusText: 'Not Found',
    })

    await expect(chatHistoryService.getChatMessages('chat-x')).rejects.toThrow(/Not Found/)
  })

  test('drops letters without URLs but keeps books without URLs', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      statusText: 'OK',
      json: async () => [
        {
          role: 'assistant',
          text: 'mixed',
          timestamp: 1700000000,
          citations: [
            { id: 'b1', source: 'book', book_title: 'Rough Riders', chapter_id: 'ch-1' },
            { id: 'l1', source: 'letter', title: 'no url letter' },
            {
              id: 'l2',
              source: 'letter',
              title: 'with url letter',
              trpl_file_url: ['https://img/2.jpg'],
            },
          ],
        },
      ],
    })

    const result = await chatHistoryService.getChatMessages('chat-a')
    expect(result[0].attachments).toHaveLength(2)
    expect(result[0].attachments!.map((a) => a.id)).toEqual(['b1', 'l2'])
  })
})

describe('chatHistoryService.deleteChat', () => {
  test('returns true on successful DELETE', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue({ ok: true, statusText: 'OK' })

    const result = await chatHistoryService.deleteChat('chat-a')
    expect(result).toBe(true)
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/chat-history/anon-user-1/chat-a',
      expect.objectContaining({ method: 'DELETE' })
    )
  })

  test('returns false on non-OK response (does not throw)', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValue({ ok: false, statusText: 'Server Error' })
    jest.spyOn(console, 'error').mockImplementation(() => {})

    const result = await chatHistoryService.deleteChat('chat-a')
    expect(result).toBe(false)
  })

  test('returns false on fetch rejection (does not throw)', async () => {
    ;(global.fetch as jest.Mock).mockRejectedValue(new Error('network down'))
    jest.spyOn(console, 'error').mockImplementation(() => {})

    const result = await chatHistoryService.deleteChat('chat-a')
    expect(result).toBe(false)
  })
})
