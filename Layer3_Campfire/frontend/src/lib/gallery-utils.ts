/**
 * Gallery utility functions shared between animation hooks
 */

/**
 * Measures the true content height of one set of images in a column.
 *
 * Uses getBoundingClientRect() for sub-pixel precision instead of offsetHeight.
 * Includes the gap between the last item of set 1 and first item of set 2
 * to ensure seamless wrapping when content is duplicated.
 *
 * @param column - The column element to measure
 * @param originalCount - Number of items in one set (before duplication)
 * @returns The height of one set including gaps, or 0 if invalid
 */
export const measureSingleSetHeight = (column: HTMLElement, originalCount: number): number => {
  if (!column || originalCount <= 0) return 0

  const children = Array.from(column.children) as HTMLElement[]
  if (children.length === 0) return 0

  const gap = parseFloat(getComputedStyle(column).gap) || 0

  // Sum heights using getBoundingClientRect for sub-pixel precision
  let contentHeight = 0
  const countToMeasure = Math.min(originalCount, children.length)

  for (let i = 0; i < countToMeasure; i++) {
    const rect = children[i]?.getBoundingClientRect()
    if (rect) {
      contentHeight += rect.height
    }
  }

  // Total gaps = gaps within set + gap to next duplicate set
  // This ensures the wrap point aligns with where the duplicate content begins
  const totalGaps = gap * originalCount

  return contentHeight + totalGaps
}

/**
 * Measures the true content width of one set of images in a row.
 *
 * Uses getBoundingClientRect() for sub-pixel precision instead of offsetWidth.
 * Includes the gap between the last item of set 1 and first item of set 2
 * to ensure seamless wrapping when content is duplicated.
 *
 * @param row - The row element to measure
 * @param originalCount - Number of items in one set (before duplication)
 * @returns The width of one set including gaps, or 0 if invalid
 */
export const measureSingleSetWidth = (row: HTMLElement, originalCount: number): number => {
  if (!row || originalCount <= 0) return 0

  const children = Array.from(row.children) as HTMLElement[]
  if (children.length === 0) return 0

  const gap = parseFloat(getComputedStyle(row).gap) || 0

  // Sum widths using getBoundingClientRect for sub-pixel precision
  let contentWidth = 0
  const countToMeasure = Math.min(originalCount, children.length)

  for (let i = 0; i < countToMeasure; i++) {
    const rect = children[i]?.getBoundingClientRect()
    if (rect) {
      contentWidth += rect.width
    }
  }

  // Total gaps = gaps within set + gap to next duplicate set
  // This ensures the wrap point aligns with where the duplicate content begins
  const totalGaps = gap * originalCount

  return contentWidth + totalGaps
}

/**
 * Wraps animation currentTime to stay within valid range [0, duration).
 * Handles both positive overflow and negative values.
 *
 * @param time - The current time value to wrap
 * @param duration - The total duration of the animation
 * @returns The wrapped time value within [0, duration)
 */
export const wrapTime = (time: number, duration: number): number => {
  if (duration === 0) return 0
  let result = time % duration
  if (result < 0) result += duration
  return result
}
