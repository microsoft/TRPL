// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { ArchivalRecord, User, QueueStats } from '@/types'

// Synthetic UI fallback records. Historical subjects and institution names
// provide realistic context, but identifiers, descriptions, and record
// associations are invented and are not collection metadata.
export const mockRecords: ArchivalRecord[] = [
  {
    id: '1',
    title: 'Letter to John Hay regarding Panama Canal negotiations',
    content:
      "This confidential correspondence details Roosevelt's strategic approach to securing canal rights and addresses concerns about...",
    date: '1903-11-15',
    dateType: 'created',
    creator: { name: 'Theodore Roosevelt', role: 'Author' },
    source: {
      repository: 'Library of Congress',
      collection: 'Primary presidential collection',
      code: 'LC',
      color: '#2563eb',
    },
    resourceType: 'correspondence',
    aiConfidence: 42,
    ocrConfidence: 42,
    metadataConfidence: 42,
    ocr_processing_status: 'pending',
    status: 'pending',
    flags: { severeDeviation: true },
    documentId: 'loc-2013646805',
    assetCount: 1,
    querySelector(_arg0: string): unknown {
      throw new Error('Function not implemented.')
    },
  },
  {
    id: '2',
    title: 'Photograph of Roosevelt at Yellowstone National Park',
    content:
      'Black and white photograph showing President Roosevelt during his conservation tour of Yellowstone, standing near...',
    date: '1904-04-08',
    dateType: 'taken',
    creator: { name: 'Unknown', role: 'Photographer' },
    source: {
      repository: 'Harvard University',
      collection: 'Houghton Library collection',
      code: 'H',
      color: '#dc2626',
    },
    resourceType: 'photo',
    aiConfidence: 87,
    ocrConfidence: 87,
    metadataConfidence: 87,
    ocr_processing_status: 'pending',
    status: 'pending',
    flags: {},
    documentId: 'harvard-ph-1904-023',
    assetCount: 1,
    querySelector(_arg0: string): unknown {
      throw new Error('Function not implemented.')
    },
  },
  {
    id: '3',
    title: 'Speech transcript: "The Square Deal" address to Congress',
    content:
      'Complete transcript of Roosevelt\'s landmark speech outlining his domestic policy agenda, including trust-busting and...',
    date: '1903-12-03',
    dateType: 'delivered',
    creator: { name: 'Theodore Roosevelt', role: 'Speaker' },
    source: {
      repository: 'Morris Library',
      collection: 'University of Delaware',
      code: 'M',
      color: '#16a34a',
    },
    resourceType: 'speech',
    aiConfidence: 94,
    ocrConfidence: 94,
    metadataConfidence: 94,
    ocr_processing_status: 'pending',
    status: 'pending',
    lastEdited: { time: '2 hours ago', user: 'Archivist A' },
    flags: {},
    documentId: 'morris-sp-1903-001',
    assetCount: 1,
    querySelector(_arg0: string): unknown {
      throw new Error('Function not implemented.')
    },
  },
  {
    id: '4',
    title: 'Diary entry regarding conservation policies',
    content:
      "Personal reflections on the establishment of national parks and wildlife refuges, written during Roosevelt's second term...",
    date: '1906-09-22',
    dateType: 'written',
    creator: { name: 'Theodore Roosevelt', role: 'Author' },
    source: {
      repository: 'Roosevelt House',
      collection: 'Personal family collection',
      code: 'RA',
      color: '#9333ea',
    },
    resourceType: 'diary',
    aiConfidence: 73,
    ocrConfidence: 73,
    metadataConfidence: 73,
    ocr_processing_status: 'pending',
    status: 'pending',
    flags: {},
    documentId: 'roosevelt-diary-1906-045',
    assetCount: 1,
    querySelector(_arg0: string): unknown {
      throw new Error('Function not implemented.')
    },
  },
  {
    id: '5',
    title: 'Official proclamation establishing Crater Lake National Park',
    content:
      'Presidential proclamation signed by Roosevelt designating Crater Lake as a national park, including boundary descriptions and...',
    date: '1902-05-22',
    dateType: 'signed',
    creator: { name: 'Theodore Roosevelt', role: 'President' },
    source: {
      repository: 'Library of Congress',
      collection: 'Primary presidential collection',
      code: 'LC',
      color: '#2563eb',
    },
    resourceType: 'official_document',
    aiConfidence: 91,
    ocrConfidence: 91,
    metadataConfidence: 91,
    ocr_processing_status: 'pending',
    status: 'pending',
    lastEdited: { time: '45 min ago', user: 'Archivist B' },
    flags: {},
    documentId: 'loc-proc-1902-015',
    assetCount: 1,
    querySelector(_arg0: string): unknown {
      throw new Error('Function not implemented.')
    },
  },
]

export const mockStats: QueueStats = mockRecords.reduce<QueueStats>(
  (acc, record) => {
    if (record.status === 'pending') acc.pending += 1
    if (record.status === 'reviewed') acc.completed += 1 
    if (record.status === 'published') acc.inReview += 1
    if (record.flags.severeDeviation) acc.severeDeviations += 1
    return acc
  },
  { pending: 0, completed: 0, severeDeviations: 0, inReview: 0 }
)
