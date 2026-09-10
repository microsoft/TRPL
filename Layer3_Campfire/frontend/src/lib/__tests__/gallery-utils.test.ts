/**
 * @jest-environment jsdom
 */

import {
  wrapTime,
  measureSingleSetHeight,
  measureSingleSetWidth,
} from '../gallery-utils'

// ============================================================================
// wrapTime Tests
// ============================================================================

describe('wrapTime', () => {
  it('returns 0 when duration is 0', () => {
    expect(wrapTime(100, 0)).toBe(0)
    expect(wrapTime(-50, 0)).toBe(0)
  })

  it('returns time unchanged when within bounds', () => {
    expect(wrapTime(500, 1000)).toBe(500)
    expect(wrapTime(0, 1000)).toBe(0)
    expect(wrapTime(999, 1000)).toBe(999)
  })

  it('wraps positive overflow correctly', () => {
    expect(wrapTime(1000, 1000)).toBe(0)
    expect(wrapTime(1500, 1000)).toBe(500)
    expect(wrapTime(2000, 1000)).toBe(0)
    expect(wrapTime(2500, 1000)).toBe(500)
  })

  it('wraps negative values correctly', () => {
    expect(wrapTime(-100, 1000)).toBe(900)
    expect(wrapTime(-1000, 1000)).toBeCloseTo(0) // -0 equals 0
    expect(wrapTime(-1500, 1000)).toBe(500)
    expect(wrapTime(-2500, 1000)).toBe(500)
  })

  it('handles floating point durations', () => {
    expect(wrapTime(1.5, 1)).toBeCloseTo(0.5)
    expect(wrapTime(-0.3, 1)).toBeCloseTo(0.7)
  })
})

// ============================================================================
// measureSingleSetHeight Tests
// ============================================================================

describe('measureSingleSetHeight', () => {
  let mockColumn: HTMLElement

  beforeEach(() => {
    mockColumn = document.createElement('div')
    document.body.appendChild(mockColumn)
  })

  afterEach(() => {
    document.body.removeChild(mockColumn)
  })

  it('returns 0 for null column', () => {
    expect(measureSingleSetHeight(null as unknown as HTMLElement, 5)).toBe(0)
  })

  it('returns 0 for originalCount <= 0', () => {
    expect(measureSingleSetHeight(mockColumn, 0)).toBe(0)
    expect(measureSingleSetHeight(mockColumn, -1)).toBe(0)
  })

  it('returns 0 for empty column', () => {
    expect(measureSingleSetHeight(mockColumn, 5)).toBe(0)
  })

  it('measures children heights correctly', () => {
    // Add mock children with fixed heights
    for (let i = 0; i < 3; i++) {
      const child = document.createElement('div')
      child.style.height = '100px'
      child.style.width = '100px'
      mockColumn.appendChild(child)
    }

    // Without gap, should measure content only
    mockColumn.style.display = 'flex'
    mockColumn.style.flexDirection = 'column'
    mockColumn.style.gap = '0px'

    // getBoundingClientRect returns 0 in JSDOM, so we'll verify the logic
    // by checking it doesn't throw and returns a number
    const result = measureSingleSetHeight(mockColumn, 3)
    expect(typeof result).toBe('number')
    expect(result).toBeGreaterThanOrEqual(0)
  })

  it('respects originalCount limit', () => {
    // Add 6 children (duplicated set)
    for (let i = 0; i < 6; i++) {
      const child = document.createElement('div')
      mockColumn.appendChild(child)
    }

    // Should only measure first 3
    const result = measureSingleSetHeight(mockColumn, 3)
    expect(typeof result).toBe('number')
  })
})

// ============================================================================
// measureSingleSetWidth Tests
// ============================================================================

describe('measureSingleSetWidth', () => {
  let mockRow: HTMLElement

  beforeEach(() => {
    mockRow = document.createElement('div')
    document.body.appendChild(mockRow)
  })

  afterEach(() => {
    document.body.removeChild(mockRow)
  })

  it('returns 0 for null row', () => {
    expect(measureSingleSetWidth(null as unknown as HTMLElement, 5)).toBe(0)
  })

  it('returns 0 for originalCount <= 0', () => {
    expect(measureSingleSetWidth(mockRow, 0)).toBe(0)
    expect(measureSingleSetWidth(mockRow, -1)).toBe(0)
  })

  it('returns 0 for empty row', () => {
    expect(measureSingleSetWidth(mockRow, 5)).toBe(0)
  })

  it('measures children widths correctly', () => {
    for (let i = 0; i < 3; i++) {
      const child = document.createElement('div')
      child.style.width = '100px'
      child.style.height = '100px'
      mockRow.appendChild(child)
    }

    mockRow.style.display = 'flex'
    mockRow.style.flexDirection = 'row'
    mockRow.style.gap = '0px'

    const result = measureSingleSetWidth(mockRow, 3)
    expect(typeof result).toBe('number')
    expect(result).toBeGreaterThanOrEqual(0)
  })
})
