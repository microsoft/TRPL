import {
  cn,
  formatDate,
  generateId,
  delay,
  sanitizeInput,
  sanitizeAction,
  sanitizeTopic,
  mergeRefs,
  formatTranscript,
  downloadTextFile,
  copyTextToClipboard,
  toProxiedImageUrl,
  INPUT_MAX_LENGTHS,
} from '../utils'

describe('cn (className merge)', () => {
  it('merges multiple class names', () => {
    expect(cn('foo', 'bar')).toBe('foo bar')
  })

  it('handles conditional classes', () => {
    expect(cn('base', true && 'active', false && 'disabled')).toBe('base active')
  })

  it('handles undefined and null values', () => {
    expect(cn('base', undefined, null, 'end')).toBe('base end')
  })

  it('returns empty string for no inputs', () => {
    expect(cn()).toBe('')
  })
})

describe('formatDate', () => {
  it('formats Date object correctly', () => {
    // Use a specific time to avoid timezone issues
    const date = new Date(2024, 2, 15) // March 15, 2024 (month is 0-indexed)
    const result = formatDate(date)
    expect(result).toContain('March')
    expect(result).toContain('15')
    expect(result).toContain('2024')
  })

  it('formats date string correctly', () => {
    // Use ISO format with time to avoid timezone ambiguity
    const result = formatDate('2024-12-25T12:00:00')
    expect(result).toContain('December')
    expect(result).toContain('25')
    expect(result).toContain('2024')
  })
})

describe('generateId', () => {
  it('returns a string', () => {
    expect(typeof generateId()).toBe('string')
  })

  it('returns unique values', () => {
    const ids = new Set(Array.from({ length: 100 }, () => generateId()))
    expect(ids.size).toBe(100)
  })

  it('returns string of expected length', () => {
    const id = generateId()
    expect(id.length).toBeGreaterThan(0)
    expect(id.length).toBeLessThanOrEqual(13)
  })
})

describe('delay', () => {
  beforeEach(() => {
    jest.useFakeTimers()
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it('resolves after specified time', async () => {
    const promise = delay(1000)
    jest.advanceTimersByTime(1000)
    await expect(promise).resolves.toBeUndefined()
  })
})

describe('sanitizeInput', () => {
  describe('basic functionality', () => {
    it('returns empty string for null/undefined/non-string', () => {
      expect(sanitizeInput(null as unknown as string)).toBe('')
      expect(sanitizeInput(undefined as unknown as string)).toBe('')
      expect(sanitizeInput(123 as unknown as string)).toBe('')
      expect(sanitizeInput({} as unknown as string)).toBe('')
    })

    it('trims whitespace', () => {
      expect(sanitizeInput('  hello  ')).toBe('hello')
      expect(sanitizeInput('\n\nhello\n\n')).toBe('hello')
      expect(sanitizeInput('\t\thello\t\t')).toBe('hello')
    })

    it('collapses multiple spaces', () => {
      expect(sanitizeInput('hello    world')).toBe('hello world')
      expect(sanitizeInput('a     b     c')).toBe('a b c')
    })

    it('preserves newlines and tabs in content', () => {
      expect(sanitizeInput('hello\nworld')).toBe('hello\nworld')
      expect(sanitizeInput('hello\tworld')).toBe('hello\tworld')
    })
  })

  describe('control character removal', () => {
    it('removes null bytes', () => {
      expect(sanitizeInput('hello\x00world')).toBe('helloworld')
    })

    it('removes bell character', () => {
      expect(sanitizeInput('hello\x07world')).toBe('helloworld')
    })

    it('removes backspace', () => {
      expect(sanitizeInput('hello\x08world')).toBe('helloworld')
    })

    it('removes vertical tab', () => {
      expect(sanitizeInput('hello\x0Bworld')).toBe('helloworld')
    })

    it('removes form feed', () => {
      expect(sanitizeInput('hello\x0Cworld')).toBe('helloworld')
    })

    it('removes escape character', () => {
      expect(sanitizeInput('hello\x1Bworld')).toBe('helloworld')
    })

    it('removes DEL character', () => {
      expect(sanitizeInput('hello\x7Fworld')).toBe('helloworld')
    })
  })

  describe('unicode whitespace normalization', () => {
    it('normalizes non-breaking space', () => {
      expect(sanitizeInput('hello\u00A0world')).toBe('hello world')
    })

    it('normalizes em space', () => {
      expect(sanitizeInput('hello\u2003world')).toBe('hello world')
    })

    it('normalizes thin space', () => {
      expect(sanitizeInput('hello\u2009world')).toBe('hello world')
    })

    it('normalizes ideographic space', () => {
      expect(sanitizeInput('hello\u3000world')).toBe('hello world')
    })

    it('normalizes multiple unicode spaces', () => {
      expect(sanitizeInput('hello\u00A0\u2003\u3000world')).toBe('hello world')
    })
  })

  describe('length limiting', () => {
    it('uses default max length for chat messages', () => {
      const longInput = 'a'.repeat(5000)
      expect(sanitizeInput(longInput).length).toBe(INPUT_MAX_LENGTHS.chatMessage)
    })

    it('respects custom max length', () => {
      const input = 'hello world'
      expect(sanitizeInput(input, 5)).toBe('hello')
    })

    it('handles input shorter than max length', () => {
      expect(sanitizeInput('short', 1000)).toBe('short')
    })
  })

  describe('XSS prevention', () => {
    it('preserves HTML tags as text (no execution)', () => {
      const input = '<script>alert("xss")</script>'
      expect(sanitizeInput(input)).toBe('<script>alert("xss")</script>')
    })

    it('preserves event handlers as text', () => {
      const input = '<img onerror="alert(1)">'
      expect(sanitizeInput(input)).toBe('<img onerror="alert(1)">')
    })

    it('handles javascript: protocol', () => {
      const input = 'javascript:alert(1)'
      expect(sanitizeInput(input)).toBe('javascript:alert(1)')
    })
  })
})

describe('sanitizeAction', () => {
  it('converts newlines to spaces', () => {
    expect(sanitizeAction('hello\nworld')).toBe('hello world')
    expect(sanitizeAction('hello\rworld')).toBe('hello world')
  })

  it('handles CRLF (produces double space due to processing order)', () => {
    // Note: \r\n each become a space after sanitizeInput runs,
    // resulting in double space. This is current behavior.
    expect(sanitizeAction('hello\r\nworld')).toBe('hello  world')
  })

  it('uses action max length', () => {
    const longInput = 'a'.repeat(500)
    expect(sanitizeAction(longInput).length).toBe(INPUT_MAX_LENGTHS.action)
  })

  it('applies all sanitization rules', () => {
    const input = '  hello\x00\u00A0world\n  '
    expect(sanitizeAction(input)).toBe('hello world')
  })
})

describe('sanitizeTopic', () => {
  it('removes newlines', () => {
    expect(sanitizeTopic('hello\nworld')).toBe('hello world')
    expect(sanitizeTopic('hello\rworld')).toBe('hello world')
  })

  it('uses topic max length', () => {
    const longInput = 'a'.repeat(500)
    expect(sanitizeTopic(longInput).length).toBe(INPUT_MAX_LENGTHS.topic)
  })

  it('applies all sanitization rules', () => {
    const input = '  hello\x00\u00A0world\n  '
    expect(sanitizeTopic(input)).toBe('hello world')
  })
})

describe('mergeRefs', () => {
  it('calls callback refs with node', () => {
    const callbackRef = jest.fn()
    const merged = mergeRefs(callbackRef)
    const node = document.createElement('div')

    merged(node)

    expect(callbackRef).toHaveBeenCalledWith(node)
  })

  it('sets object ref current value', () => {
    const objectRef = { current: null as HTMLDivElement | null }
    const merged = mergeRefs(objectRef)
    const node = document.createElement('div')

    merged(node)

    expect(objectRef.current).toBe(node)
  })

  it('handles multiple refs', () => {
    const callbackRef = jest.fn()
    const objectRef = { current: null as HTMLDivElement | null }
    const merged = mergeRefs(callbackRef, objectRef)
    const node = document.createElement('div')

    merged(node)

    expect(callbackRef).toHaveBeenCalledWith(node)
    expect(objectRef.current).toBe(node)
  })

  it('handles null refs', () => {
    const callbackRef = jest.fn()
    const merged = mergeRefs(null, undefined, callbackRef)
    const node = document.createElement('div')

    merged(node)

    expect(callbackRef).toHaveBeenCalledWith(node)
  })

  it('handles null node (unmount)', () => {
    const callbackRef = jest.fn()
    const objectRef = { current: document.createElement('div') }
    const merged = mergeRefs(callbackRef, objectRef)

    merged(null)

    expect(callbackRef).toHaveBeenCalledWith(null)
    expect(objectRef.current).toBeNull()
  })
})

describe('formatTranscript', () => {
  it('formats messages with header', () => {
    const messages = [
      { role: 'user', content: 'Hello' },
      { role: 'assistant', content: 'Hi there!' },
    ]

    const result = formatTranscript(messages)

    expect(result).toContain('Theodore Roosevelt Reading Room')
    expect(result).toContain('Conversation Transcript')
    expect(result).toContain('Exported:')
    expect(result).toContain('You:\nHello')
    expect(result).toContain('Theodore:\nHi there!')
  })

  it('handles empty messages array', () => {
    const result = formatTranscript([])

    expect(result).toContain('Theodore Roosevelt Reading Room')
    expect(result).not.toContain('You:')
    expect(result).not.toContain('Theodore:')
  })

  it('labels user messages as "You"', () => {
    const messages = [{ role: 'user', content: 'Test message' }]

    const result = formatTranscript(messages)

    expect(result).toContain('You:\nTest message')
  })

  it('labels assistant messages as "Theodore"', () => {
    const messages = [{ role: 'assistant', content: 'Test response' }]

    const result = formatTranscript(messages)

    expect(result).toContain('Theodore:\nTest response')
  })

  it('preserves message content with newlines', () => {
    const messages = [
      { role: 'assistant', content: 'Line 1\nLine 2\nLine 3' },
    ]

    const result = formatTranscript(messages)

    expect(result).toContain('Line 1\nLine 2\nLine 3')
  })
})

describe('copyTextToClipboard', () => {
  let originalClipboard: Clipboard | undefined
  let originalExecCommand: ((commandId: string) => boolean) | undefined
  let execCommandMock: jest.Mock<boolean, [string]>

  beforeEach(() => {
    originalClipboard = navigator.clipboard
    originalExecCommand = (document as Document & { execCommand?: (commandId: string) => boolean }).execCommand
    execCommandMock = jest.fn<boolean, [string]>()
    Object.defineProperty(document, 'execCommand', {
      configurable: true,
      value: execCommandMock,
    })
  })

  afterEach(() => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: originalClipboard,
    })
    Object.defineProperty(document, 'execCommand', {
      configurable: true,
      value: originalExecCommand,
    })
  })

  it('uses navigator.clipboard when available', async () => {
    const writeText = jest.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })

    const result = await copyTextToClipboard('hello')

    expect(result).toBe(true)
    expect(writeText).toHaveBeenCalledWith('hello')
    expect(execCommandMock).not.toHaveBeenCalled()
  })

  it('falls back to execCommand when clipboard API fails', async () => {
    const writeText = jest.fn().mockRejectedValue(new Error('blocked'))
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })
    execCommandMock.mockReturnValue(true)

    const result = await copyTextToClipboard('fallback text')

    expect(result).toBe(true)
    expect(writeText).toHaveBeenCalledWith('fallback text')
    expect(execCommandMock).toHaveBeenCalledWith('copy')
  })

  it('returns false when both clipboard API and fallback fail', async () => {
    const writeText = jest.fn().mockRejectedValue(new Error('blocked'))
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })
    execCommandMock.mockReturnValue(false)

    const result = await copyTextToClipboard('nope')

    expect(result).toBe(false)
    expect(execCommandMock).toHaveBeenCalledWith('copy')
  })
})

describe('downloadTextFile', () => {
  let createObjectURLMock: jest.Mock
  let revokeObjectURLMock: jest.Mock
  let clickMock: jest.Mock
  let appendChildMock: jest.Mock
  let removeChildMock: jest.Mock

  beforeEach(() => {
    createObjectURLMock = jest.fn(() => 'blob:test-url')
    revokeObjectURLMock = jest.fn()
    clickMock = jest.fn()
    appendChildMock = jest.fn()
    removeChildMock = jest.fn()

    global.URL.createObjectURL = createObjectURLMock
    global.URL.revokeObjectURL = revokeObjectURLMock

    jest.spyOn(document, 'createElement').mockImplementation((tag) => {
      if (tag === 'a') {
        return {
          href: '',
          download: '',
          click: clickMock,
          style: {},
        } as unknown as HTMLAnchorElement
      }
      return document.createElement(tag)
    })

    document.body.appendChild = appendChildMock
    document.body.removeChild = removeChildMock
  })

  afterEach(() => {
    jest.restoreAllMocks()
  })

  it('creates a blob with the content', () => {
    downloadTextFile('test content', 'test.txt')

    expect(createObjectURLMock).toHaveBeenCalledWith(expect.any(Blob))
  })

  it('creates an anchor element with correct attributes', () => {
    downloadTextFile('test content', 'test.txt')

    expect(document.createElement).toHaveBeenCalledWith('a')
  })

  it('triggers download by clicking the anchor', () => {
    downloadTextFile('test content', 'test.txt')

    expect(appendChildMock).toHaveBeenCalled()
    expect(clickMock).toHaveBeenCalled()
    expect(removeChildMock).toHaveBeenCalled()
  })

  it('revokes the object URL after download', () => {
    jest.useFakeTimers()

    downloadTextFile('test content', 'test.txt')

    expect(revokeObjectURLMock).not.toHaveBeenCalled()
    jest.runOnlyPendingTimers()
    expect(revokeObjectURLMock).toHaveBeenCalledWith('blob:test-url')

    jest.useRealTimers()
  })

  it('uses provided filename', () => {
    const mockAnchor = {
      href: '',
      download: '',
      click: clickMock,
      style: {},
    }
    jest.spyOn(document, 'createElement').mockReturnValue(mockAnchor as unknown as HTMLElement)

    downloadTextFile('content', 'my-file.txt')

    expect(mockAnchor.download).toBe('my-file.txt')
  })
})

describe('toProxiedImageUrl', () => {
  it('rewrites a blob storage URL to the same-origin image proxy', () => {
    const blobUrl = 'https://examplestorage.blob.core.windows.net/content-assets/x.jpg?sig=abc'
    expect(toProxiedImageUrl(blobUrl)).toBe(
      `/api/artifacts/image?url=${encodeURIComponent(blobUrl)}`,
    )
  })

  it('rewrites any *.blob.core.windows.net host', () => {
    const blobUrl = 'https://other.blob.core.windows.net/c/y.png'
    expect(toProxiedImageUrl(blobUrl)).toBe(
      `/api/artifacts/image?url=${encodeURIComponent(blobUrl)}`,
    )
  })

  it('leaves non-blob hosts unchanged', () => {
    const externalUrl = 'https://example.org/some/image.jpg'
    expect(toProxiedImageUrl(externalUrl)).toBe(externalUrl)
  })

  it('leaves relative/local paths unchanged', () => {
    expect(toProxiedImageUrl('/api/example')).toBe(
      '/api/example',
    )
  })

  it('leaves malformed URLs unchanged', () => {
    expect(toProxiedImageUrl('not a url')).toBe('not a url')
  })
})
