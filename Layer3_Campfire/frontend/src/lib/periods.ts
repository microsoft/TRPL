/**
 * Theodore Roosevelt life periods for the artifact detail timeline.
 *
 * Canonical list derived from the Azure Search index `period` facet
 * (see /docs/Periods-output.txt for raw data).
 *
 * The index contains many variant spellings and formats for the same
 * period. PERIOD_ALIAS_MAP normalizes every known variant to a
 * canonical id so the timeline can highlight the correct dot
 * regardless of which string the backend returns.
 */

export interface Period {
  id: string
  label: string
  startYear: number
  /** null means "present" / ongoing */
  endYear: number | null
}

/**
 * Canonical periods in chronological order.
 *
 * Timeline rendering model:
 * - The timeline has PERIODS.length + 1 dots (18 total).
 * - Period at index `i` spans the segment from dot `i` to dot `i+1`.
 * - The active period highlights its two boundary dots and the
 *   connecting line between them.
 */
export const PERIODS: Period[] = [
  { id: 'ancestry', label: 'Ancestry', startYear: 1600, endYear: 1858 },
  { id: 'youth', label: 'Youth', startYear: 1858, endYear: 1881 },
  { id: 'assemblyman', label: 'New York State Assemblyman', startYear: 1882, endYear: 1884 },
  { id: 'rancher', label: 'Dakota Rancher', startYear: 1884, endYear: 1886 },
  { id: 'author-family', label: 'Author and Family Man', startYear: 1887, endYear: 1888 },
  { id: 'civil-service', label: 'Civil Service Commissioner', startYear: 1889, endYear: 1895 },
  { id: 'police-commissioner', label: 'NYC Police Commissioner', startYear: 1895, endYear: 1897 },
  { id: 'navy-secretary', label: 'Assistant Secretary of the Navy', startYear: 1897, endYear: 1898 },
  { id: 'rough-rider', label: 'Rough Rider', startYear: 1898, endYear: 1898 },
  { id: 'governor', label: 'Governor of New York', startYear: 1898, endYear: 1900 },
  { id: 'vice-president', label: 'Vice President', startYear: 1901, endYear: 1901 },
  { id: 'president-1st', label: 'U.S. President – 1st Term', startYear: 1901, endYear: 1905 },
  { id: 'president-2nd', label: 'U.S. President – 2nd Term', startYear: 1905, endYear: 1909 },
  { id: 'african-safari', label: 'African Safari', startYear: 1909, endYear: 1910 },
  { id: 'progressive', label: 'Progressive Party Candidate', startYear: 1911, endYear: 1912 },
  { id: 'post-presidential', label: 'Post-Presidential Years', startYear: 1913, endYear: 1919 },
  { id: 'public-memory', label: 'Public Memory', startYear: 1919, endYear: null },
]

/**
 * Maps every known index value (lowercased) → canonical period id.
 *
 * When the backend returns a `period` string, lowercase it and look
 * it up here to get the canonical id for timeline highlighting.
 */
export const PERIOD_ALIAS_MAP: Record<string, string> = {
  // Ancestry
  'ancestry (to 1858)': 'ancestry',

  // Youth
  'youth (october 27, 1858-1881)': 'youth',

  // Assemblyman
  'new york state assemblyman (1882-may 1884)': 'assemblyman',

  // Dakota Rancher
  'dakota rancher (june 1884-1886)': 'rancher',

  // Author and Family Man
  'author and family man (1887-1888)': 'author-family',

  // Civil Service Commissioner
  'civil service commissioner (1889-april 1895)': 'civil-service',

  // NYC Police Commissioner
  'nyc police commissioner (may 1895-march 1897)': 'police-commissioner',

  // Assistant Secretary of the Navy
  'assistant secretary of the navy (april 1897-april 1898)': 'navy-secretary',
  '(1897, april-1898, april) assistant secretary of the navy': 'navy-secretary',

  // Rough Rider / Spanish-American War
  'rough rider (may 1898 to september 1898)': 'rough-rider',
  'spanish-american war (1898)': 'rough-rider',

  // Governor of New York
  'governor of new york (october 1898-1900)': 'governor',
  '(1898, october-1900) governor of new york': 'governor',
  'gubernatorial years (1899-1900)': 'governor',
  'gubernatorial years (1899-1901)': 'governor',

  // Vice President
  'vice president of the united states (1901)': 'vice-president',
  'vice-presidential years (1901)': 'vice-president',
  'vice presidential years (1901)': 'vice-president',

  // President – 1st Term
  'u.s. president - 1st term (september 1901-february 1905)': 'president-1st',

  // President – 2nd Term
  'u.s. president - 2nd term (march 1905-february 1909)': 'president-2nd',
  '(1905, march-1909, february) u.s. president - 2nd term': 'president-2nd',

  // Combined presidential (map to 1st term as default)
  'presidential years (1901-1909)': 'president-1st',
  'early twentieth century (1901-1909)': 'president-1st',
  'presidential campaign years (1900)': 'vice-president',
  'pre-presidential years (before 1901)': 'governor',

  // African Safari
  'african safari (march 1909-1910)': 'african-safari',

  // Progressive Party Candidate
  'progressive party candidate (1911-1912)': 'progressive',
  '(1911-1912) progressive party candidate': 'progressive',
  'presidential election of 1912 (1912)': 'progressive',

  // Post-Presidential Years
  'post-presidential years (1913-january 6, 1919)': 'post-presidential',
  '(1913-1919, january 6) post-presidential years': 'post-presidential',

  // Public Memory
  'theodore roosevelt and public memory (1919-present)': 'public-memory',
  'roosevelt family/relatives (1919-present)': 'public-memory',

  // Broader historical eras — map to closest TR period
  'early political career (1881-1898)': 'assemblyman',
  'progressive era (1890s-1920s)': 'progressive',
  'progressive era (1890s-1920)': 'progressive',
  'world war i era (1914-1918)': 'post-presidential',
  'world war i (1914-1918)': 'post-presidential',
  'post-reconstruction era (1877-1915)': 'civil-service',
  'new deal era (1933-1942)': 'public-memory',
  'world war ii (1939-1945)': 'public-memory',
  'world war ii era (1939-1945)': 'public-memory',
  'cold war era (1947-1991)': 'public-memory',
  'columbia university protests (april-may 1968)': 'public-memory',
  'postwar harvard (1945-1970)': 'public-memory',
  'postwar harvard (1945-present)': 'public-memory',
  'dakota territorial centennial (1861-1961)': 'public-memory',
}

/**
 * Resolve a raw period string from the backend to a canonical period id.
 * Returns undefined for empty, "Unknown", or unrecognized values.
 */
export function resolvePeriodId(raw: string | undefined | null): string | undefined {
  if (!raw || raw.trim() === '' || raw.toLowerCase() === 'unknown') return undefined
  return PERIOD_ALIAS_MAP[raw.toLowerCase().trim()]
}

/**
 * Get the canonical Period object from a raw backend string.
 */
export function resolvePeriod(raw: string | undefined | null): Period | undefined {
  const id = resolvePeriodId(raw)
  if (!id) return undefined
  return PERIODS.find((p) => p.id === id)
}

/**
 * Get the index of the active period in the PERIODS array.
 * This is the segment index for the timeline: the active segment
 * spans from dot[index] to dot[index + 1].
 * Returns -1 if the period can't be resolved.
 */
export function resolvePeriodIndex(raw: string | undefined | null): number {
  const id = resolvePeriodId(raw)
  if (!id) return -1
  return PERIODS.findIndex((p) => p.id === id)
}
