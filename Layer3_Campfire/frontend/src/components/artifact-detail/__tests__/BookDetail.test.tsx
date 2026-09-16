// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * @jest-environment jsdom
 *
 * Regression tests for REPORT.md #23: XSS via book_description rendered
 * through react-markdown.
 *
 * Previously BookDetail rendered:
 *   <ReactMarkdown rehypePlugins={[rehypeRaw]}>{book_description}</ReactMarkdown>
 * which allowed raw HTML inside book_description to execute as DOM (script
 * tags, img onerror handlers, etc).
 *
 * The fix removed rehype-raw. These tests assert that raw HTML inside
 * book_description is NOT executed.
 */

import { render, screen } from '@testing-library/react'
import { BookDetail } from '../BookDetail'

interface WindowWithXss {
  __xss?: unknown
  __xss2?: unknown
}

describe('BookDetail XSS regression (REPORT.md #23)', () => {
  beforeEach(() => {
    delete (window as unknown as WindowWithXss).__xss
    delete (window as unknown as WindowWithXss).__xss2
  })

  afterEach(() => {
    delete (window as unknown as WindowWithXss).__xss
    delete (window as unknown as WindowWithXss).__xss2
  })

  it('book_description script tag is escaped, not executed', () => {
    const data = {
      book_title: 'Test Book',
      book_description: 'Hello\n\n<script data-test-xss>window.__xss=1</script>',
    }

    const { container } = render(<BookDetail data={data} />)

    // Security property: script must not have executed
    expect((window as unknown as WindowWithXss).__xss).toBeUndefined()

    // No <script> elements should have been injected by react-markdown
    expect(container.querySelector('script[data-test-xss]')).toBeNull()
    expect(container.querySelector('script')).toBeNull()

    // The non-HTML markdown text should still be visible
    expect(screen.getByText(/Hello/)).toBeInTheDocument()
  })

  it('book_description img onerror is escaped, not executed', () => {
    const data = {
      book_title: 'Test Book',
      book_description: '<img src=x onerror="window.__xss2=1">',
    }

    const { container } = render(<BookDetail data={data} />)

    // Security property: the onerror handler must not have fired
    expect((window as unknown as WindowWithXss).__xss2).toBeUndefined()

    const description = container.querySelector('[class*="description"]')
    expect(description).not.toBeNull()
    const img = description?.querySelector('img')
    if (img) {
      expect(img.getAttribute('onerror')).toBeNull()
    }
  })

  it('book_description renders HTML content safely', () => {
    const data = {
      book_title: 'Test Book',
      book_description: '<p>A book with <strong>bold</strong> and <em>italic</em> text.</p>',
    }

    const { container } = render(<BookDetail data={data} />)
    
    const strong = container.querySelector('strong')
    const em = container.querySelector('em')

    expect(strong).not.toBeNull()
    expect(strong?.textContent).toBe('bold')
    expect(em).not.toBeNull()
    expect(em?.textContent).toBe('italic')
  })
})
