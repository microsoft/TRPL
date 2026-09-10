import { ArchivalRecord, CollectionSummary } from '@/types'
import { equalsIgnoreCase } from '@/utils/textCompare'

/**
 * Generates collection summaries from archival records.
 * Groups records by collection/repository pairs and calculates statistics.
 */
export function generateCollectionSummaries(records: ArchivalRecord[]): CollectionSummary[] {
  // Group records by collection/repository pair
  const groupedRecords = new Map<string, ArchivalRecord[]>()
  
  records.forEach(record => {
    const key = `${record.source.repository}|${record.source.collection}`
    if (!groupedRecords.has(key)) {
      groupedRecords.set(key, [])
    }
    groupedRecords.get(key)!.push(record)
  })
  
  // Calculate statistics for each group
  const summaries: CollectionSummary[] = []
  
  groupedRecords.forEach((groupRecords, key) => {
    const [repository, collection] = key.split('|')
    
    const totalItems = groupRecords.length
    const pending = groupRecords.filter(r => r.status === 'pending').length
    const completed = groupRecords.filter(r => r.status === 'reviewed').length
    const published = groupRecords.filter(r => r.status === 'published').length
    const completionRate = totalItems > 0 
      ? Math.round(((completed + published) / totalItems) * 100 * 10) / 10 // Round to 1 decimal
      : 0
    
    // Get code and color from first record in group (they should all be the same)
    const code = groupRecords[0]?.source.code || ''
    const color = groupRecords[0]?.source.color || '#6b7280'
    
    summaries.push({
      repository,
      collection,
      code,
      color,
      totalItems,
      pending,
      completionRate,
      completed,
      published
    })
  })
  
  // Sort by repository name
  return summaries.sort((a, b) => a.repository.localeCompare(b.repository))
}

/**
 * Gets a single collection summary for a specific repository/collection pair
 */
export function getCollectionSummary(
  records: ArchivalRecord[], 
  repository: string, 
  collection: string
): CollectionSummary | null {
  const summaries = generateCollectionSummaries(records)
  return (
    summaries.find(
      s => equalsIgnoreCase(s.repository, repository) && equalsIgnoreCase(s.collection, collection)
    ) || null
  )
}

