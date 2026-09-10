// Shared types for EPUB processing components

export interface Section {
  type: string
  title: string
  content: string
  word_count: number
  order: number
  html_content?: string
  selected: boolean
  level?: number
  parent_order?: number | null
  children?: Section[]
  href?: string
}

export interface FlattenedSection extends Section {
  displayLevel: number
}

export type DiffViewMode = 'text' | 'html'

export interface CreatorInfo {
  name: string
  fileAs?: string
  role?: string
}

export interface EpubMetadata {
  // Core metadata
  title: string
  titleSort?: string
  author: string
  creator?: string
  creatorInfo?: CreatorInfo
  contributors?: CreatorInfo[]
  publisher?: string
  language?: string
  description?: string
  subjects?: string[]
  rights?: string
  date?: string
  
  // Identifiers
  identifier?: string
  isbn?: string
  uuid?: string
  asin?: string
  googleId?: string
  calibreId?: string
  identifiers?: Record<string, string>
  
  // Series info
  series?: string
  seriesIndex?: string
  
  // Calibre metadata
  calibreTimestamp?: string
  
  // Computed fields
  total_word_count?: number
  chapter_count?: number
}

export interface EpubDocument {
  id: string
  filename: string
  blob_url: string
  status: string
  error_message?: string
  created_at: string
  updated_at: string
  parsed_at?: string
  extracted_at?: string
  validated_at?: string
  completed_at?: string
  metadata?: EpubMetadata
  sections?: Section[]
  total_sections: number
  selected_sections: number
  selected_word_count: number
  original_text_url?: string
  filtered_text_url?: string
  original_word_count?: number
  filtered_word_count?: number
  ingested_chunks?: number
  ingested_word_count?: number
}

export interface FilterConfig {
  exclude_tags: string[]
  exclude_classes: string[]
  exclude_ids: string[]
  class_patterns: string[]
}

export interface StatusConfig {
  color: string
  bgColor: string
  icon: React.ReactNode
  label: string
}

export interface SectionGroup {
  key: string
  label: string
  icon: React.ReactNode
  bgColor: string
  sections: FlattenedSection[]
}

export interface Chunk {
  id: string
  text: string
  chapter_title: string
  chunk_index: number
  token_count: number
}

