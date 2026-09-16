// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

export interface ArchivalRecord {
  querySelector(arg0: string): unknown;
  id: string;
  title: string;
  content: string;
  date: string;
  dateType: 'created' | 'taken' | 'delivered' | 'signed' | 'written';
  creator: {
    name: string;
    role: string;
  };
  recipient?: string;
  source: {
    repository: string;
    collection: string;
    code: string;
    color: string;
  };
  resourceType: string;
  aiConfidence: number; // Deprecated: use ocrConfidence and metadataConfidence instead
  ocrConfidence: number;
  metadataConfidence: number;
  ocr_processing_status: string; //"completed" | "pending" | "no_assets_found" | "error";
  related_assets_status?: string;
  visual_description_possible?:string;
  asset_details_status?: string;
  original_file_status?: string;
  ocr_batch_status?: string;
  metadata_extraction_status?: string;
  status: 'pending' | 'reviewed' | 'publishing' | 'published' | 'failed'
  lastEdited?: {
    time: string;
    user: string;
  };
  flags: {
    severeDeviation?: boolean;
    highConfidence?: boolean;
  };
  documentId: string;
  assetCount: number;
  
  // Review/Publish tracking
  validatedBy?: string;
  validatedAt?: string;
  publishedBy?: string;
  publishedAt?: string;
  
  // Raw metadata for dynamic field access (used by field mapping feature)
  rawMetadata?: { [key: string]: any };
}

export interface User {
  name: string;
  role: string;
  avatar: string;
}

export interface QueueStats {
  pending: number;
  completed: number;
  severeDeviations: number;
  inReview: number;
}

export interface FilterState {
  search: string;
  repository: string;
  collection: string;
  resourceType: string;
  status: string;
  dateRange: {
    start: string;
    end: string;
  };
  confidence: string;
}

export interface PaginationState {
  currentPage: number;
  itemsPerPage: number;
  totalItems: number;
  totalPages: number;
}

export interface SortState {
  field: 'created_at' | 'title' | 'confidence' | 'lastEdited';
  direction: 'asc' | 'desc';
}

export interface BulkActionState {
  selectedItems: string[];
  isVisible: boolean;
}

export interface UIState {
  sidebarOpen: boolean;
  shortcutsModalOpen: boolean;
  viewMode: 'table' | 'list' | 'grid';
  bulkActionsVisible: boolean;
}

export type SourceType = 'loc' | 'harvard' | 'morris' | 'roosevelt';
export type ResourceType = string;
export type StatusType = 'pending' | 'reviewed' | 'publishing' | 'published' | 'failed';
export type ConfidenceLevel = 'high' | 'medium' | 'low' | 'very-low';

export interface CollectionSummary {
  repository: string
  collection: string
  code: string
  color: string
  totalItems: number
  pending: number
  completionRate: number
  completed: number
  published: number
  avgOcrConfidence?: number | null
}