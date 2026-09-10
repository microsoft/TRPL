// API Service for Archivist App
import { getAccessToken, getIdToken } from './auth';
import {
  DocumentConflictError,
  DOCUMENT_CONFLICT_MESSAGE,
  isConflictMessage,
  isConflictStatus,
} from '@/utils/documentConflict';

/**
 * Error thrown when trying to upload an EPUB that already exists
 */
export class EpubDuplicateError extends Error {
  existingDocument?: {
    id: string;
    filename: string;
    status: string;
    created_at: string;
  };

  constructor(message: string, existingDocument?: {
    id: string;
    filename: string;
    status: string;
    created_at: string;
  }) {
    super(message);
    this.name = 'EpubDuplicateError';
    this.existingDocument = existingDocument;
  }
}

/**
 * Extract error message from API error response.
 * Handles both string and object detail formats:
 * - { detail: "error message" }
 * - { detail: { error: "...", message: "...", ... } }
 */
function extractErrorMessage(
  errorData: Record<string, unknown>,
  fallback: string,
  status?: number
): string {
  if (
    (status === 409 || status === 412) &&
    typeof errorData?.detail === 'string' &&
    errorData.detail.trim()
  ) {
    return errorData.detail;
  }

  if (typeof errorData?.error === 'string' && errorData.error.trim()) {
    return errorData.error;
  }

  if (typeof errorData?.detail === 'string' && errorData.detail.trim()) {
    return errorData.detail;
  }

  const detail = errorData?.detail;

  if (detail && typeof detail === 'object') {
    const detailObj = detail as Record<string, unknown>;
    // Prefer 'message' over 'error' for user-friendly messages
    if (typeof detailObj.message === 'string') {
      return detailObj.message;
    }
    if (typeof detailObj.error === 'string') {
      return detailObj.error;
    }
  }
  
  return fallback;
}

function throwDocumentSaveError(
  response: Response,
  errorData: Record<string, unknown>,
  fallback: string
): never {
  const message = extractErrorMessage(errorData, fallback, response.status);
  if (isConflictStatus(response.status) || (response.status === 400 && isConflictMessage(message))) {
    throw new DocumentConflictError(DOCUMENT_CONFLICT_MESSAGE);
  }
  throw new Error(message);
}

export interface AssetDetail {
  asset_id?: string;
  id?: string;
  blob_url: string;
  blob_thumbnail_url?: string;
  filename?: string;
  asset_type?: string;
  ocr_result?: {
    ocr_text?: string;
    ocr_text_original?: string;
    entities?: any[];
    confidence_scores?: {
      ocr_confidence?: number;
      entity_extraction_confidence?: number;
      overall_confidence?: number;
    };
    processing_time?: number;
    processed_at?: string;
   
    [key: string]: any;
  };

   metadata?: {
      [key: string]: any;
    };
}

export interface ApiDocumentMetadata {
  id: string;
  status?: string;
  record_id?: string;
  title?: string;
  type?: string;
  type_label?: string;
  published?: boolean;
  asset_avg_confidence?: number;
  metadata_extraction_confidence?: number;
  ocr_result?: {
    confidence?: number
  };
  
  // Verified metadata from database
  metadata?: {
    Title?: string;
    Collection?: string;
    Description?: string;
    "Creation Date"?: string;
    Creator?: string;
    Identifier?: string;
    "Resource Type"?: string;
    Period?: string;
    Repository?: string;
    "Copyright Status"?: string;
    "Image Rights"?: string;
    "Production Method"?: string;
    Language?: string;
    "Page Count"?: string;
    Recipient?: string;
    Citation?: string;
    [key: string]: any;
  };
  
  // AI-extracted metadata (new field)
  extracted_metadata?: {
    document_type?: string;
    date?: string;
    sender?: string;
    recipient?: string;
    subject?: string;
    key_entities?: string[];
    summary?: string;
    description?: string;
    language?: string;
    topics?: string[];
    creator?: string;
    resource_type?: string;
    period?: string;
    production_method?: string;
    rights?: string;
    [key: string]: any;
  };
  related_assets?: string[];
  asset_details?: AssetDetail[];
  asset_count?: number;
  image_url?: string;
  storage_path?: string;
  filename?: string;
  content_type?: string;
  file_size?: number;
  created_at?: string;
  error_message?: string;
  processed_at?: string;
  last_processed?: string;
  ocr_processing_status?: string;
  related_assets_status?: string;
  asset_details_status?: string;
  original_file_status?: string;
  ocr_batch_status?: string;
  metadata_extraction_status?: string;
  archivist_notes?: string;
  archivist_status?: string; // Lowercase: pending | reviewed | publishing | published | failed
  archivist_modified_ts?: string;  // Timestamp of last archivist edit
  
  // Review/Publish tracking
  validated_by?: string;  // Display name of validator
  validated_at?: string;  // ISO timestamp when validated
  published_by?: string;  // Display name of publisher
  published_at?: string;  // ISO timestamp when published
  publish_status?: string;  // "published" | "unpublished"
  
  // Visual description fields
  visual_description_possible?: string;  // "Y" or "N"
  visual_detailed_description_original_blob_url?: string;
  visual_detailed_description_flexible_blob_url?: string;
  visual_detailed_description_original?: string;  // Hydrated text content
  visual_detailed_description_flexible?: string;  // Hydrated text content
  _etag?: string;
}

export interface ApiDocumentList {
  documents: ApiDocumentMetadata[];
  count: number;
}

export interface ApiDocumentStatistics {
  total: number;
  pending: number;
  processing: number;
  completed: number;
  failed: number;
}

export interface ApiCollectionSummary {
  repository: string;
  collection: string;
  totalItems: number;
  pending: number;
  completed: number;
  published: number;
  archivistCompleted: number;
  completionRate: number;
  /** Precomputed mean of asset_avg_confidence (0–100) from statistics container, or null */
  avgOcrConfidence?: number | null;
}

export interface ResourceTypeOcrStats {
  resourceType: string
  documentCount: number
  errorCount: number
  avgOcrAccuracy: number | null
  avgMetadataAccuracy: number | null
}

export interface CollectionOcrReport {
  id: string
  type: string
  repository: string
  collection: string
  totalDocuments: number
  errorCount: number
  overallOcrAccuracy: number | null
  overallMetadataAccuracy: number | null
  byResourceType: ResourceTypeOcrStats[]
  lastUpdated: string
}

export interface ApiCollectionSummariesResponse {
  summaries: ApiCollectionSummary[];
  count: number;
}

export interface ApiRepositoriesResponse {
  repositories: string[];
  count: number;
}

export interface ApiCollectionsResponse {
  collections: string[];
  count: number;
}

export interface ApiResourceTypesResponse {
  resource_types: string[];
  count: number;
}

export interface ApiSourcesResponse {
  sources: string[];
  count: number;
}

export interface ApiCreatorEntry {
  creator: string;
  repository: string;
  collection: string;
}

export interface ApiCreatorsResponse {
  creators: ApiCreatorEntry[];
  count: number;
}

export interface ApiRecipientEntry {
  recipient: string;
  repository: string;
  collection: string;
}

export interface ApiRecipientsResponse {
  recipients: ApiRecipientEntry[];
  count: number;
}

export interface ApiRepositoryStatistics {
  repository: string;
  totalItems: number;
  collections: number;
  pending: number;
  reviewed: number;
  publishing: number;
  published: number;
  errors: number;
  completionRate: number;
}

export interface ApiRepositoryStatisticsResponse {
  repositories: ApiRepositoryStatistics[];
  count: number;
}

export interface ApiCollectionDetails {
  collection: string;
  repository: string;
  totalItems: number;
  pending: number;
  reviewed: number;
  publishing: number;
  published: number;
  errors: number;
  completionRate: number;
  /** Mean of document asset_avg_confidence (0–100); null if no documents have that field */
  avgOcrConfidence?: number | null;
  resourceTypes: string[];
  resourceTypesCount: number;
}

export interface ApiCollectionDetailsResponse {
  collection: ApiCollectionDetails;
}

export interface AuditEntry {
  id: string;
  record_id: string;
  ts: string;
  by: {
    userId: string;
    display: string;
  };
  op: string;
  version: number;
  fields: Array<{
    path: string;
    from: string;
    to: string;
  }>;
  correlationId?: string;
}

export interface AuditHistoryResponse {
  entity_id: string;
  entries: AuditEntry[];
  count: number;
}

export interface DocumentUpdateRequest {
  metadata?: {
    [key: string]: any;
  };
  archivist_notes?: string;
  archivist_status?: string;
  visual_detailed_description_flexible?: string;
  correlation_id?: string;
  // user_id and user_display removed - now come from authentication
}

export interface OcrUpdateRequest {
  asset_index: number;
  ocr_text: string;
  correlation_id?: string;
  // user_id and user_display removed - now come from authentication
}

export interface ActiveJob {
  id: string;
  instance_id: string;
  name: string;
  status_url: string;
  runtime_status: string;
  started_at: string;
  custom_status?: any;
  created_at: string;
  updated_at: string;
}

/** Public shape from GET/PATCH `/correction-requests` (Archivist API). */
export interface CorrectionRequestItem {
  id: string;
  record_id: string;
  note: string;
  source: string;
  status: 'unread' | 'reviewed' | 'dismissed';
  timestamp: string;
  received_at: string;
  is_deleted?: boolean;
  source_app_id: string;
  source_display_name: string;
  record_available?: boolean;
  record_title?: string | null;
  record_repository?: string | null;
  record_collection?: string | null;
  caller_app_id?: string | null;
  dismissed_at?: string | null;
  reviewed_at?: string | null;
  dismissed_by?: Record<string, unknown> | null;
  reviewed_by?: Record<string, unknown> | null;
  dismissal_note?: string | null;
}

export interface DigitalItemFile {
  filename?: string;
  url?: string;
  bytes?: number;
}

export interface DigitalItem {
  id: string;
  source: string;
  item_type?: string;
  title?: string;
  hub_label?: string;
  description?: string;
  date_range?: string;
  source_url?: string;
  source_id?: string;
  module_id?: string;
  parent_module_id?: string;
  parent_id?: string;
  pattern?: string;
  entry_count?: number;
  section_count?: number;
  toc_entry_count?: number;
  status?: string;
  primary_pdf_url?: string;
  thumbnail_url?: string;
  page_count?: number;
  metadata?: Record<string, string>;
  provenance?: string;
  files?: DigitalItemFile[];
  document_sections?: { heading?: string; level?: number; body?: string }[];
  cyclopedia_entries?: { letter?: string; heading?: string; body?: string }[];
  speech_sections?: { title?: string; body?: string; links?: unknown[] }[];
  toc_entries?: unknown[];
  extraction_summary?: Record<string, unknown>;
  ingested_at?: string;
  audio_url?: string;
  audio_files?: { url: string; label?: string }[];
  image_url?: string;
  image_alt?: string;
  images?: { src: string; alt?: string }[];
  has_audio?: boolean;
  order?: number;
  plain_text?: string;
  html_content?: string;
  content_url?: string;
  ocr_accuracy?: number;
  ocr_status?: string;
  ocr_page_count?: number;
  publish_status?: string;
  published_at?: string;
  linked_topics?: Array<{
    title: string;
    display: string;
    summary: string;
    thumbnail_url?: string;
    thumbnail_width?: number;
    thumbnail_height?: number;
  }>;
}

export interface DigitalItemsListResponse {
  source: string;
  count: number;
  items: DigitalItem[];
}

export interface DigitalItemPage {
  parent_id: string;
  source: string;
  page_number: number;
  page_count?: number;
  pdf_url?: string;
  files?: string[];
  ocr_text_original?: string;
  ocr_text_flexible?: string;
  ocr_version?: number;
  title?: string;
  ocr_available?: boolean;
  has_edits?: boolean;
}

export interface DigitalItemOcrCompare {
  item_id: string;
  source: string;
  page_number?: number;
  ocr_text_original: string;
  ocr_text_flexible: string;
  has_edits: boolean;
  ocr_version: number;
}

export interface DigitalItemOcrHistory {
  item_id: string;
  source: string;
  total_entries: number;
  entries: Array<{
    id: string;
    record_id: string;
    ts: string;
    by: { userId: string; display: string };
    op: string;
    version: number;
    fields: Array<{ path: string; from: string; to: string }>;
  }>;
}

export interface DigitalIngestionJob {
  source: string;
  label: string;
  status: 'Running' | 'Completed' | 'Failed' | 'Stopped' | 'never_run';
  started_at: string | null;
  completed_at: string | null;
  output_lines: string[];
  error_message: string | null;
  items_ingested: number;
  // Which individual step is running (null = full pipeline)
  current_step?: 'fetch' | 'ocr' | 'publish' | null;
  // OCR substep tracking
  ocr_status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped';
  ocr_items_processed: number;
  ocr_items_failed: number;
  ocr_items_total: number;
  ocr_error: string | null;
}

export interface OcrJob {
  source: string;
  label: string;
  status: 'Running' | 'Completed' | 'Failed' | 'never_run';
  started_at: string | null;
  completed_at: string | null;
  items_processed: number;
  items_failed: number;
  items_skipped: number;
  total_items: number;
  error_message: string | null;
  output_lines: string[];
}

class ApiService {
  private baseUrl = `${import.meta.env.VITE_BACKEND_API_ENDPOINT || ''}/api/v1`;

  /**
   * Get common headers for all requests
   * In production: Let Azure App Service handle auth via cookies
   * In development: Include Bearer token for mock authentication
   */
  private async getHeaders(): Promise<HeadersInit> {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    };

    const accessToken = await getAccessToken();
    const idToken = await getIdToken();
    if (accessToken) {
      headers['Authorization'] = `Bearer ${accessToken}`;
    }
    if (idToken) {
      headers['X-ID-Token'] = idToken;
    }

    return headers;
  }

  /** Auth headers for binary blob GETs (no JSON Content-Type). */
  async getBlobAuthHeaders(): Promise<HeadersInit> {
    const headers: Record<string, string> = {};
    const accessToken = await getAccessToken();
    const idToken = await getIdToken();
    if (accessToken) {
      headers['Authorization'] = `Bearer ${accessToken}`;
    }
    if (idToken) {
      headers['X-ID-Token'] = idToken;
    }
    return headers;
  }

  private isLocalAzuriteUrl(url: string): boolean {
    if (typeof url !== 'string') {
      return false;
    }
    try {
      const parsed = new URL(url);
      const isLocalHost = ['azurite', '127.0.0.1', 'localhost'].includes(
        parsed.hostname,
      );
      return (
        parsed.protocol === 'http:' &&
        isLocalHost &&
        parsed.port === '10000' &&
        parsed.pathname.startsWith('/devstoreaccount1/')
      );
    } catch {
      return false;
    }
  }

  isAzureBlobStorageUrl(url: string): boolean {
    if (typeof url !== 'string') {
      return false;
    }
    try {
      return (
        new URL(url).hostname.endsWith('.blob.core.windows.net') ||
        this.isLocalAzuriteUrl(url)
      );
    } catch {
      return false;
    }
  }

  private cleanAzureBlobUrl(blobUrl: string): string {
    return blobUrl.split('?')[0]?.trim() ?? '';
  }

  private encodeUrlForAssetProxy(rawUrl: string): string {
    const utf8 = unescape(encodeURIComponent(rawUrl));
    return btoa(utf8)
      .replace(/\+/g, '-')
      .replace(/\//g, '_')
      .replace(/=+$/g, '');
  }

  private buildAssetProxyUrl(rawUrl: string): string {
    const encoded = this.encodeUrlForAssetProxy(rawUrl);
    return `${this.baseUrl}/digital-items/asset-proxy?u=${encoded}`;
  }

  async fetchAssetProxyContent(rawUrl: string): Promise<Response> {
    const trimmed = rawUrl?.trim() ?? '';
    if (!trimmed) {
      throw new Error('Invalid asset URL');
    }
    return fetch(this.buildAssetProxyUrl(trimmed), {
      method: 'GET',
      headers: await this.getBlobAuthHeaders(),
      credentials: 'include',
    });
  }

  /** POST blob proxy for private storage blobs. */
  async fetchBlobContent(blobUrl: string): Promise<Response> {
    const cleanUrl = this.cleanAzureBlobUrl(blobUrl);
    if (!cleanUrl) {
      throw new Error('Invalid Azure blob URL');
    }
    return fetch(`${this.baseUrl}/blob-content`, {
      method: 'POST',
      headers: {
        ...(await this.getBlobAuthHeaders()),
        'Content-Type': 'application/json',
      },
      credentials: 'include',
      body: JSON.stringify({ blob_url: cleanUrl }),
    });
  }

  /** Fetch blob bytes as UTF-8 text via the API proxy (private storage). */
  async fetchBlobText(blobUrl: string): Promise<string> {
    if (!this.isAzureBlobStorageUrl(blobUrl)) {
      const response = await fetch(blobUrl);
      if (!response.ok) {
        throw new Error(`Failed to fetch content (${response.status})`);
      }
      return response.text();
    }
    const response = await this.fetchBlobContent(blobUrl);
    if (!response.ok) {
      throw new Error(`Failed to load blob content (${response.status})`);
    }
    return response.text();
  }

  /** Fetch a private-storage blob via the API and return a display object URL. */
  async fetchBlobObjectUrl(blobUrl: string): Promise<string> {
    const response = this.isLocalAzuriteUrl(blobUrl)
      ? await this.fetchBlobContent(blobUrl)
      : await this.fetchAssetProxyContent(blobUrl);
    if (!response.ok) {
      throw new Error(`Failed to load asset content (${response.status})`);
    }
    const blob = await response.blob();
    return URL.createObjectURL(blob);
  }

  /** react-pdf file prop for blobs that require authenticated API proxy. */
  async getBlobProxyFileOptions(blobUrl: string): Promise<string> {
    return this.fetchBlobObjectUrl(blobUrl);
  }

  async getDocuments(params?: {
    max_items?: number;
    order_by?: string;
    descending?: boolean;
    page_number?: number;
    page_size?: number;
    status?: string;
    repository?: string;
    collection?: string;
    resource_type?: string;
    source?: string;
    creator?: string;
    recipient?: string;
    title_contains?: string;
    min_confidence?: number;
    max_confidence?: number;
    exclude_errors?: boolean;
    exclude_zero_assets?: boolean;
  }): Promise<ApiDocumentList> {
    const queryParams = new URLSearchParams();
    if (params?.max_items) queryParams.append('max_items', params.max_items.toString());
    if (params?.order_by) queryParams.append('order_by', params.order_by);
    if (params?.descending !== undefined) queryParams.append('descending', params.descending.toString());
    if (params?.page_number) queryParams.append('page_number', params.page_number.toString());
    if (params?.page_size) queryParams.append('page_size', params.page_size.toString());
    if (params?.status) queryParams.append('status', params.status);
    if (params?.repository) queryParams.append('repository', params.repository);
    if (params?.collection) queryParams.append('collection', params.collection);
    if (params?.resource_type) queryParams.append('resource_type', params.resource_type);
    if (params?.source) queryParams.append('source', params.source);
    if (params?.creator) queryParams.append('creator', params.creator);
    if (params?.recipient) queryParams.append('recipient', params.recipient);
    if (params?.title_contains) queryParams.append('title_contains', params.title_contains);
    if (params?.min_confidence !== undefined) queryParams.append('min_confidence', params.min_confidence.toString());
    if (params?.max_confidence !== undefined) queryParams.append('max_confidence', params.max_confidence.toString());
    if (params?.exclude_errors !== undefined) queryParams.append('exclude_errors', params.exclude_errors.toString());
    if (params?.exclude_zero_assets !== undefined) queryParams.append('exclude_zero_assets', params.exclude_zero_assets.toString());

    const url = `${this.baseUrl}/documents${queryParams.toString() ? '?' + queryParams.toString() : ''}`;

    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include', // Include cookies for authentication
    });

    if (!response.ok) {
      const errorText = await response.text();
      console.error('API Error (%d): %s', response.status, errorText);
      throw new Error(`Failed to fetch documents: ${response.statusText} - ${errorText}`);
    }

    return response.json();
  }

  async getDocumentById(documentId: string, partitionKey?: string): Promise<ApiDocumentMetadata> {
    const queryParams = new URLSearchParams();
    if (partitionKey) queryParams.append('partition_key', partitionKey);

    const url = `${this.baseUrl}/documents/${documentId}${queryParams.toString() ? '?' + queryParams.toString() : ''}`;

    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      throw new Error(`Failed to fetch document: ${response.statusText}`);
    }

    return response.json();
  }

  async getAssetById(documentId: string, assetId: string, partitionKey?: string): Promise<AssetDetail> {
    const queryParams = new URLSearchParams();
    if (partitionKey) queryParams.append('partition_key', partitionKey);

    const url = `${this.baseUrl}/documents/${documentId}/assets/${assetId}${queryParams.toString() ? '?' + queryParams.toString() : ''}`;

    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`Failed to fetch asset: ${response.statusText} - ${errorText}`);
    }

    return response.json();
  }

  async getCollectionSummaries(): Promise<ApiCollectionSummariesResponse> {
    const url = `${this.baseUrl}/collections/summaries`;

    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      throw new Error(`Failed to fetch collection summaries: ${response.statusText}`);
    }

    return response.json();
  }

  async getRepositoryNames(): Promise<ApiRepositoriesResponse> {
    const url = `${this.baseUrl}/filters/repositories`;
    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      throw new Error(`Failed to fetch repository names: ${response.statusText}`);
    }
    return response.json();
  }

  async getCollections(repository?: string): Promise<ApiCollectionsResponse> {
    const queryParams = new URLSearchParams();
    if (repository) queryParams.append('repository', repository);

    const url = `${this.baseUrl}/filters/collections${queryParams.toString() ? '?' + queryParams.toString() : ''}`;
    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      throw new Error(`Failed to fetch collections: ${response.statusText}`);
    }
    return response.json();
  }

  async getRepositories(params?: {
    search?: string
    page_number?: number
    page_size?: number
  }): Promise<ApiRepositoryStatisticsResponse> {
    const queryParams = new URLSearchParams()
    if (params?.search) queryParams.append('title_contains', params.search)
    if (params?.page_number) queryParams.append('page_number', params.page_number.toString())
    if (params?.page_size) queryParams.append('page_size', params.page_size.toString())

    const url = `${this.baseUrl}/repositories${queryParams.toString() ? '?' + queryParams.toString() : ''}`
    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    })
    if (!response.ok) {
      throw new Error(`Failed to fetch repositories: ${response.statusText}`)
    }
    return response.json()
  }

  async getRepositoryStatisticsSummary(): Promise<{
    totalRepositories: number
    totalItems: number
    totalCollections: number
    totalPending: number
    totalReviewed: number
    totalPublishing: number
    totalPublished: number
    totalErrors: number
    overallCompletionRate: number
  }> {
    const url = `${this.baseUrl}/repositories/statistics`
    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    })
    if (!response.ok) {
      throw new Error(`Failed to fetch repository statistics: ${response.statusText}`)
    }
    return response.json()
  }

  async getRepositoryCollectionStatisticsSummary(repository: string): Promise<{
    totalCollections: number
    totalItems: number
    totalPending: number
    totalReviewed: number
    totalPublishing: number
    totalPublished: number
    totalErrors: number
    overallCompletionRate: number
  }> {
    const url = `${this.baseUrl}/repositories/${encodeURIComponent(repository)}/statistics`
    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    })
    if (!response.ok) {
      throw new Error(`Failed to fetch repository collection statistics: ${response.statusText}`)
    }
    return response.json()
  }

  async getCollectionsByRepository(
    repository: string,
    params?: {
      title_contains?: string
      page_number?: number
      page_size?: number
    }
  ): Promise<{ collections: ApiCollectionDetails[]; count: number }> {
    const queryParams = new URLSearchParams()
    if (params?.title_contains) queryParams.append('title_contains', params.title_contains)
    if (params?.page_number) queryParams.append('page_number', params.page_number.toString())
    if (params?.page_size) queryParams.append('page_size', params.page_size.toString())

    const url = `${this.baseUrl}/repositories/${encodeURIComponent(repository)}/collections${queryParams.toString() ? '?' + queryParams.toString() : ''}`
    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    })
    if (!response.ok) {
      throw new Error(`Failed to fetch collections by repository: ${response.statusText}`)
    }
    return response.json()
  }

  async getRecordIds(params?: {
    repository?: string
    collection?: string
    status?: string
    resource_type?: string
    min_confidence?: number
    max_confidence?: number
    title_contains?: string
    order_by?: string
    descending?: boolean
  }): Promise<{ record_ids: string[]; count: number }> {
    const queryParams = new URLSearchParams()
    if (params?.repository) queryParams.append('repository', params.repository)
    if (params?.collection) queryParams.append('collection', params.collection)
    if (params?.status) queryParams.append('status', params.status)
    if (params?.resource_type) queryParams.append('resource_type', params.resource_type)
    if (params?.min_confidence !== undefined) queryParams.append('min_confidence', params.min_confidence.toString())
    if (params?.max_confidence !== undefined) queryParams.append('max_confidence', params.max_confidence.toString())
    if (params?.title_contains) queryParams.append('title_contains', params.title_contains)
    if (params?.order_by) queryParams.append('order_by', params.order_by)
    if (params?.descending !== undefined) queryParams.append('descending', params.descending.toString())

    const url = `${this.baseUrl}/collections/record-ids${queryParams.toString() ? '?' + queryParams.toString() : ''}`
    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    })
    if (!response.ok) {
      throw new Error(`Failed to fetch record IDs: ${response.statusText}`)
    }
    return response.json()
  }

  async getDashboardStatistics(): Promise<{
    totalItems: number
    totalCollections: number
    totalPending: number
    totalPublishing: number
    totalPublished: number
    totalRepositories: number
    totalReviewed: number
    totalErrors: number
    overallCompletionRate: number
  }> {
    const url = `${this.baseUrl}/dashboard/statistics`
    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    })
    if (!response.ok) {
      throw new Error(`Failed to fetch dashboard statistics: ${response.statusText}`)
    }
    return response.json()
  }

  async getCollectionDetails(repository: string, collection: string): Promise<ApiCollectionDetailsResponse> {
    const queryParams = new URLSearchParams();
    queryParams.append('repository', repository);
    queryParams.append('collection', collection);

    const url = `${this.baseUrl}/collections/details${queryParams.toString() ? '?' + queryParams.toString() : ''}`;
    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      throw new Error(`Failed to fetch collection details: ${response.statusText}`);
    }
    return response.json();
  }

  async getCollectionsDetailsByRepository(repository: string): Promise<{ collections: ApiCollectionDetails[]; count: number }> {
    const queryParams = new URLSearchParams();
    queryParams.append('repository', repository);

    const url = `${this.baseUrl}/collections/details${queryParams.toString() ? '?' + queryParams.toString() : ''}`;
    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      throw new Error(`Failed to fetch collections details by repository: ${response.statusText}`);
    }
    return response.json();
  }

  async getCollectionOcrReport(repository: string, collection: string): Promise<CollectionOcrReport> {
    const url = `${this.baseUrl}/repositories/${encodeURIComponent(repository)}/collections/${encodeURIComponent(collection)}/ocr-report`;
    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      throw new Error(`Failed to fetch OCR report: ${response.statusText}`);
    }
    return response.json();
  }

  async rebuildCollectionOcrReport(repository: string, collection: string): Promise<void> {
    const url = `${this.baseUrl}/statistics/rebuild/ocr-reports/${encodeURIComponent(repository)}/${encodeURIComponent(collection)}`;
    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      throw new Error(`Failed to rebuild OCR report: ${response.statusText}`);
    }
  }

  async getResourceTypes(): Promise<ApiResourceTypesResponse> {
    const url = `${this.baseUrl}/filters/resource-types`;
    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      throw new Error(`Failed to fetch resource types: ${response.statusText}`);
    }
    return response.json();
  }

  async getSources(): Promise<ApiSourcesResponse> {
    const url = `${this.baseUrl}/filters/sources`;
    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      throw new Error(`Failed to fetch sources: ${response.statusText}`);
    }
    return response.json();
  }

  async getCreators(repository?: string, collection?: string): Promise<ApiCreatorsResponse> {
    const queryParams = new URLSearchParams();
    if (repository) queryParams.append('repository', repository);
    if (collection) queryParams.append('collection', collection);
    const url = `${this.baseUrl}/filters/creators${queryParams.toString() ? '?' + queryParams.toString() : ''}`;
    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      throw new Error(`Failed to fetch creators: ${response.statusText}`);
    }
    return response.json();
  }

  async getRecipients(repository?: string, collection?: string): Promise<ApiRecipientsResponse> {
    const queryParams = new URLSearchParams();
    if (repository) queryParams.append('repository', repository);
    if (collection) queryParams.append('collection', collection);
    const url = `${this.baseUrl}/filters/recipients${queryParams.toString() ? '?' + queryParams.toString() : ''}`;
    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      throw new Error(`Failed to fetch recipients: ${response.statusText}`);
    }
    return response.json();
  }

  async updateDocumentMetadata(
    documentId: string,
    updateRequest: DocumentUpdateRequest,
    etag?: string
  ): Promise<ApiDocumentMetadata> {
    const url = `${this.baseUrl}/documents/${documentId}`;

    const headers: Record<string, string> = {
      ...await this.getHeaders() as Record<string, string>,
    };

    if (etag) {
      headers['If-Match'] = etag;
    }

    const response = await fetch(url, {
      method: 'PATCH',
      headers,
      credentials: 'include',
      body: JSON.stringify(updateRequest),
    });

    if (!response.ok) {
      const errorData = (await response.json().catch(() => ({}))) as Record<string, unknown>;
      throwDocumentSaveError(
        response,
        errorData,
        `Failed to update document: ${response.statusText}`
      );
    }

    return response.json();
  }

  async updateOcrText(
    documentId: string,
    assetId:string,
    ocrRequest: OcrUpdateRequest,
    etag?: string
  ): Promise<ApiDocumentMetadata> {
    const url = `${this.baseUrl}/documents/${documentId}/assets/${assetId}/ocr`;

    const headers: Record<string, string> = {
      ...await this.getHeaders() as Record<string, string>,
    };

    if (etag) {
      headers['If-Match'] = etag;
    }

    const response = await fetch(url, {
      method: 'PATCH',
      headers,
      credentials: 'include',
      body: JSON.stringify(ocrRequest),
    });

    if (!response.ok) {
      const errorData = (await response.json().catch(() => ({}))) as Record<string, unknown>;
      throwDocumentSaveError(
        response,
        errorData,
        `Failed to update OCR text: ${response.statusText}`
      );
    }

    return response.json();
  }

  async getAuditHistory(
    documentId: string,
    maxItems?: number
  ): Promise<AuditHistoryResponse> {
    const queryParams = new URLSearchParams();
    if (maxItems) queryParams.append('max_items', maxItems.toString());

    const url = `${this.baseUrl}/documents/${documentId}/audit${queryParams.toString() ? '?' + queryParams.toString() : ''}`;

    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      throw new Error(`Failed to fetch audit history: ${response.statusText}`);
    }

    return response.json();
  }

  async ingestDocuments(documentIds: string[], correlationId?: string): Promise<{ success: boolean; message: string; document_ids: string[] }> {
    const url = `${this.baseUrl}/documents/ingest/by-ids`;

    const requestBody: { document_ids: string[]; correlation_id?: string } = {
      document_ids: documentIds
    };

    if (correlationId) {
      requestBody.correlation_id = correlationId;
    }

    const response = await fetch(url, {
      method: 'POST',
      headers: {
        ...await this.getHeaders(),
        'Content-Type': 'application/json',
      },
      credentials: 'include',
      body: JSON.stringify(requestBody),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to ingest documents: ${response.statusText}`));
    }

    return response.json();
  }

  async ingestDocumentsByQuery(params: {
    status?: string;
    repository?: string;
    collection?: string;
    resource_type?: string;
    min_confidence?: number;
    max_confidence?: number;
    title_contains?: string;
  }): Promise<{
    success: boolean;
    message: string;
    query?: string;
    parameters?: any[];
    excluded_missing_trc_record_ids?: string[];
    excluded_missing_trc_total?: number;
    excluded_missing_trc_ids_truncated?: boolean;
  }> {
    const queryParams = new URLSearchParams();
    
    if (params.status) queryParams.append('status', params.status);
    if (params.repository) queryParams.append('repository', params.repository);
    if (params.collection) queryParams.append('collection', params.collection);
    if (params.resource_type) queryParams.append('resource_type', params.resource_type);
    if (params.min_confidence !== undefined) queryParams.append('min_confidence', params.min_confidence.toString());
    if (params.max_confidence !== undefined) queryParams.append('max_confidence', params.max_confidence.toString());
    if (params.title_contains) queryParams.append('title_contains', params.title_contains);

    const url = `${this.baseUrl}/documents/ingest/query${queryParams.toString() ? '?' + queryParams.toString() : ''}`;

    const response = await fetch(url, {
      method: 'POST',
      headers: {
        ...await this.getHeaders(),
        'Content-Type': 'application/json',
      },
      credentials: 'include',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to ingest documents by query: ${response.statusText}`));
    }

    return response.json();
  }

  // Statistics Admin Operations
  async getStatisticsStatus(): Promise<{
    statisticsExist: boolean;
    lastUpdated: string | null;
    message: string;
  }> {
    const url = `${this.baseUrl}/statistics/status`;
    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      throw new Error(`Failed to fetch statistics status: ${response.statusText}`);
    }
    return response.json();
  }

  async clearAllStatistics(): Promise<{ status: string; message: string }> {
    const url = `${this.baseUrl}/statistics/clear`;
    const response = await fetch(url, {
      method: 'DELETE',
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to clear statistics: ${response.statusText}`));
    }
    return response.json();
  }

  async rebuildAllStatistics(): Promise<{ status: string; message: string }> {
    const url = `${this.baseUrl}/statistics/rebuild`;
    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to rebuild statistics: ${response.statusText}`));
    }
    return response.json();
  }

  async rebuildDashboardStatistics(): Promise<{ status: string; message: string }> {
    const url = `${this.baseUrl}/statistics/rebuild/dashboard`;
    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to rebuild dashboard statistics: ${response.statusText}`));
    }
    return response.json();
  }

  async rebuildRepositoryStatistics(): Promise<{ status: string; message: string }> {
    const url = `${this.baseUrl}/statistics/rebuild/repositories`;
    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to rebuild repository statistics: ${response.statusText}`));
    }
    return response.json();
  }

  async rebuildCollectionStatistics(): Promise<{ status: string; message: string }> {
    const url = `${this.baseUrl}/statistics/rebuild/collections`;
    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to rebuild collection statistics: ${response.statusText}`));
    }
    return response.json();
  }

  async rebuildCollectionStatisticsByRepository(repository: string): Promise<{ status: string; message: string }> {
    const url = `${this.baseUrl}/statistics/rebuild/collections/${encodeURIComponent(repository)}`;
    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to rebuild collection statistics: ${response.statusText}`));
    }
    return response.json();
  }

  async rebuildAllOcrReports(): Promise<{ status: string; message: string }> {
    const url = `${this.baseUrl}/statistics/rebuild/ocr-reports`;
    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to rebuild OCR reports: ${response.statusText}`));
    }
    return response.json();
  }

  // EPUB Workflow - Upload, Parse (via Function), Select, Ingest
  
  /**
   * Response when a document with the same filename already exists
   */
  async checkEpubExists(epubFile: File, opfFile?: File): Promise<{
    exists: boolean;
    existingDocument?: {
      id: string;
      filename: string;
      status: string;
      created_at: string;
    };
  }> {
    // Try uploading without overwrite to check if it exists
    try {
      await this.uploadEpub(epubFile, opfFile, false);
      return { exists: false };
    } catch (error) {
      if (error instanceof EpubDuplicateError) {
        return { exists: true, existingDocument: error.existingDocument };
      }
      throw error;
    }
  }

  async uploadEpub(
    epubFile: File, 
    opfFile?: File, 
    overwrite: boolean = false
  ): Promise<{ id: string; filename: string; status: string; message: string }> {
    const url = `${this.baseUrl}/epub/upload?overwrite=${overwrite}`;
    const formData = new FormData();
    formData.append('file', epubFile);
    if (opfFile) {
      formData.append('opf_file', opfFile);
    }

    const token = await getAccessToken();
    const idToken = await getIdToken();
    const headers: Record<string, string> = {};
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }
    if (idToken) {
      headers['X-ID-Token'] = idToken;
    }

    const response = await fetch(url, {
      method: 'POST',
      headers,
      body: formData,
      credentials: 'include',
    });

    if (response.status === 409) {
      // API returns { error, existing_document? } (allowlisted extras on 409)
      const errorData = (await response.json().catch(() => ({}))) as Record<string, unknown>;
      const legacy = (errorData.detail || {}) as Record<string, unknown>;
      const existing =
        (errorData.existing_document as EpubDuplicateError['existingDocument']) ||
        (legacy.existing_document as EpubDuplicateError['existingDocument']);
      const msg =
        (typeof errorData.error === 'string' && errorData.error) ||
        (typeof legacy.message === 'string' && legacy.message) ||
        (typeof legacy.error === 'string' && legacy.error) ||
        'Document with same filename already exists';
      throw new EpubDuplicateError(msg, existing);
    }

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to upload EPUB: ${response.statusText}`));
    }
    return response.json();
  }

  async getEpubDocuments(page: number = 1, pageSize: number = 50, status?: string): Promise<{
    documents: any[];
    count: number;
    page: number;
    page_size: number;
    total_pages: number;
  }> {
    let url = `${this.baseUrl}/epub/documents?page=${page}&page_size=${pageSize}`;
    if (status) {
      url += `&status=${encodeURIComponent(status)}`;
    }

    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to get EPUB documents: ${response.statusText}`));
    }
    return response.json();
  }

  async getEpubDocument(documentId: string): Promise<any> {
    const url = `${this.baseUrl}/epub/documents/${encodeURIComponent(documentId)}`;

    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to get EPUB document: ${response.statusText}`));
    }
    return response.json();
  }

  async updateEpubSections(documentId: string, sections: { order: number; selected: boolean }[]): Promise<any> {
    const url = `${this.baseUrl}/epub/documents/${encodeURIComponent(documentId)}/sections`;

    const response = await fetch(url, {
      method: 'PATCH',
      headers: await this.getHeaders(),
      credentials: 'include',
      body: JSON.stringify({ sections }),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to update sections: ${response.statusText}`));
    }
    return response.json();
  }

  async startEpubExtraction(documentId: string, enableOcr: boolean = false): Promise<any> {
    const url = `${this.baseUrl}/epub/documents/${encodeURIComponent(documentId)}/extract?enable_ocr=${enableOcr}`;

    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to start extraction: ${response.statusText}`));
    }
    return response.json();
  }

  async approveEpubDocument(documentId: string): Promise<any> {
    const url = `${this.baseUrl}/epub/documents/${encodeURIComponent(documentId)}/approve`;

    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to approve document: ${response.statusText}`));
    }
    return response.json();
  }

  async getEpubExtractedText(documentId: string, textType: 'original' | 'filtered'): Promise<any[]> {
    const url = `${this.baseUrl}/epub/documents/${encodeURIComponent(documentId)}/text/${textType}`;

    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to get ${textType} text: ${response.statusText}`));
    }
    return response.json();
  }

  async getEpubChunks(documentId: string): Promise<any[]> {
    const url = `${this.baseUrl}/epub/documents/${encodeURIComponent(documentId)}/chunks`;

    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to get chunks: ${response.statusText}`));
    }
    return response.json();
  }

  async retryEpubDocument(documentId: string): Promise<any> {
    const url = `${this.baseUrl}/epub/documents/${encodeURIComponent(documentId)}/retry`;

    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to retry document: ${response.statusText}`));
    }
    return response.json();
  }

  async getEpubFilterDefaults(): Promise<{
    exclude_tags: string[];
    exclude_classes: string[];
    exclude_ids: string[];
    class_patterns: string[];
  }> {
    const url = `${this.baseUrl}/epub/filter-defaults`;

    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to get filter defaults: ${response.statusText}`));
    }
    return response.json();
  }

  async getEpubFilterConfig(documentId: string): Promise<{
    filter_config: any | null;
    has_custom_config: boolean;
  }> {
    const url = `${this.baseUrl}/epub/documents/${encodeURIComponent(documentId)}/filter-config`;

    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to get filter config: ${response.statusText}`));
    }
    return response.json();
  }

  async updateEpubFilterConfig(documentId: string, config: {
    exclude_tags: string[];
    exclude_classes: string[];
    exclude_ids: string[];
    class_patterns: string[];
  }): Promise<any> {
    const url = `${this.baseUrl}/epub/documents/${encodeURIComponent(documentId)}/filter-config`;

    const response = await fetch(url, {
      method: 'PATCH',
      headers: await this.getHeaders(),
      credentials: 'include',
      body: JSON.stringify(config),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to update filter config: ${response.statusText}`));
    }
    return response.json();
  }

  async reExtractEpubDocument(documentId: string): Promise<any> {
    const url = `${this.baseUrl}/epub/documents/${encodeURIComponent(documentId)}/re-extract`;

    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to re-extract: ${response.statusText}`));
    }
    return response.json();
  }

  async deleteEpubDocument(documentId: string): Promise<any> {
    const url = `${this.baseUrl}/epub/documents/${encodeURIComponent(documentId)}`;

    const response = await fetch(url, {
      method: 'DELETE',
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to delete document: ${response.statusText}`));
    }
    return response.json();
  }

  // ============================================================
  // Data Pipeline Functions API (via Backend Proxy)
  // ============================================================

  /**
   * Generic method to trigger any pipeline stage by endpoint name
   * @param endpoint - The trigger endpoint from stage configuration
   * @param params - Optional parameters for the stage
   */
  async triggerPipelineStage(
    endpoint: string,
    params?: Record<string, any>
  ): Promise<{ 
    instance_id: string; 
    status_url: string;
    terminate_url?: string;
    suspend_url?: string;
    resume_url?: string;
  }> {
    const url = `${this.baseUrl}/pipeline/trigger/${endpoint}`;

    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
      body: JSON.stringify(params || {}),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to trigger ${endpoint}: ${response.statusText}`));
    }
    return response.json();
  }

  /**
   * List stored periodic sync runs (Cosmos periodic_run_history; content-source-periodic-sync history).
   */
  async listPipelinePeriodicRuns(
    limit?: number,
    opts?: { excludeAdhoc?: boolean; runWorkflow?: string }
  ): Promise<{
    success: boolean;
    runs: Record<string, unknown>[];
    count: number;
  }> {
    const p = new URLSearchParams();
    if (limit != null) p.set('limit', String(limit));
    if (opts?.excludeAdhoc) p.set('exclude_adhoc', 'true');
    if (opts?.runWorkflow) p.set('run_workflow', opts.runWorkflow);
    const qs = p.toString();
    const url = `${this.baseUrl}/pipeline/periodic-runs${qs ? `?${qs}` : ''}`;
    const response = await fetch(url, {
      method: 'GET',
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to list periodic runs: ${response.statusText}`));
    }
    return response.json();
  }

  async listPipelinePeriodicSchedules(limit?: number): Promise<{
    success: boolean;
    schedules: Record<string, unknown>[];
    count: number;
  }> {
    const p = new URLSearchParams();
    if (limit != null) p.set('limit', String(limit));
    const qs = p.toString();
    const url = `${this.baseUrl}/pipeline/periodic-schedules${qs ? `?${qs}` : ''}`;
    const response = await fetch(url, {
      method: 'GET',
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to list schedules: ${response.statusText}`));
    }
    return response.json();
  }

  async upsertPipelinePeriodicSchedule(body: Record<string, unknown>): Promise<{
    success: boolean;
    schedule: Record<string, unknown>;
  }> {
    const url = `${this.baseUrl}/pipeline/periodic-schedules`;
    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to save schedule: ${response.statusText}`));
    }
    return response.json();
  }

  async deletePipelinePeriodicSchedule(scheduleId: string): Promise<{ success: boolean }> {
    const url = `${this.baseUrl}/pipeline/periodic-schedules/${encodeURIComponent(scheduleId)}`;
    const response = await fetch(url, {
      method: 'DELETE',
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to delete schedule: ${response.statusText}`));
    }
    return response.json();
  }

  async runNowPipelinePeriodicSchedule(scheduleId: string): Promise<{
    success: boolean;
    result: Record<string, unknown>;
  }> {
    const url = `${this.baseUrl}/pipeline/periodic-schedules/${encodeURIComponent(scheduleId)}/run-now`;
    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to run schedule: ${response.statusText}`));
    }
    return response.json();
  }

  async processDuePipelinePeriodicSchedules(): Promise<{
    success: boolean;
    processed: Record<string, unknown>[];
  }> {
    const url = `${this.baseUrl}/pipeline/periodic-schedules/process-due`;
    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to process due schedules: ${response.statusText}`));
    }
    return response.json();
  }

  /**
   * Get orchestrator instance status
   */
  async getOrchestratorStatus(statusUrl: string): Promise<{
    name: string;
    instanceId: string;
    runtimeStatus: 'Pending' | 'Running' | 'Completed' | 'Failed' | 'Terminated' | 'Canceled';
    input: any;
    customStatus: any;
    output: any;
    createdTime: string;
    lastUpdatedTime: string;
  }> {
    const url = `${this.baseUrl}/pipeline/orchestration/status`;

    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
      body: JSON.stringify({ status_url: statusUrl }),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to get orchestrator status: ${response.statusText}`));
    }
    return response.json();
  }

  /**
   * Get pipeline configuration from backend (stages, actions, runtime config)
   */
  async getPipelineConfig(): Promise<{
    stages: Array<{
      id: string;
      name: string;
      description: string;
      icon: string;
      status_field: string;
      has_config: boolean;
      trigger_endpoint: string | null;
      is_automated: boolean;
      can_retry?: boolean;
    }>;
    actions: Record<string, {
      name: string;
      description: string;
      trigger_endpoint: string;
    }>;
    runtime_config: {
      collection_ids: string[];
      batch_size: number;
      parallel_batches: number;
      updated_at?: string | null;
    };
  }> {
    const url = `${this.baseUrl}/pipeline/stages`;

    const response = await fetch(url, {
      method: 'GET',
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to get pipeline stages: ${response.statusText}`));
    }
    return response.json();
  }

  /**
   * Get pipeline statistics from Cosmos DB (pre-computed)
   */
  async getPipelineStatistics(): Promise<{
    total_records: number;
    by_stage: {
      related_assets: { pending: number; completed: number; error: number };
      asset_details: { pending: number; completed: number; error: number };
      original_file: { pending: number; completed: number; error: number };
      ocr_batch: { pending: number; completed: number; error: number };
      ocr_processing: { pending: number; completed: number; error: number };
      metadata_extraction: { pending: number; completed: number; error: number };
    };
    overall: { pending: number; completed: number; error: number };
    active_batches: number;
    last_updated?: string;
  }> {
    const url = `${this.baseUrl}/pipeline/statistics`;

    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to get pipeline statistics: ${response.statusText}`));
    }
    return response.json();
  }

  /**
   * Rebuild pipeline statistics (triggers full recalculation)
   */
  async rebuildPipelineStatistics(): Promise<{
    total_records: number;
    by_stage: {
      related_assets: { pending: number; completed: number; error: number };
      asset_details: { pending: number; completed: number; error: number };
      original_file: { pending: number; completed: number; error: number };
      ocr_batch: { pending: number; completed: number; error: number };
      ocr_processing: { pending: number; completed: number; error: number };
      metadata_extraction: { pending: number; completed: number; error: number };
    };
    overall: { pending: number; completed: number; error: number };
    active_batches: number;
    last_updated?: string;
  }> {
    const url = `${this.baseUrl}/pipeline/statistics/rebuild`;

    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to rebuild pipeline statistics: ${response.statusText}`));
    }
    return response.json();
  }

  /**
   * Get errors for a specific pipeline stage, grouped by error message
   */
  async getStageErrors(
    stageId: string,
    pageNumber: number = 1,
    pageSize: number = 20
  ): Promise<{
    stage_id: string;
    stage_name: string;
    errors: Array<{ error_text: string; count: number }>;
    total_unique_errors: number;
    total_error_count: number;
    page_number: number;
    page_size: number;
    total_pages: number;
  }> {
    const url = `${this.baseUrl}/pipeline/stages/${encodeURIComponent(stageId)}/errors?page_number=${pageNumber}&page_size=${pageSize}`;

    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to get stage errors: ${response.statusText}`));
    }
    return response.json();
  }

  /**
   * Get records that have a specific error message for a pipeline stage
   */
  async getRecordsByError(
    stageId: string, 
    errorText: string, 
    pageNumber: number = 1, 
    pageSize: number = 20
  ): Promise<{
    stage_id: string;
    stage_name: string;
    records: Array<{
      id: string;
      title?: string;
      source_record_id?: string;
      repository?: string;
      collection_name?: string;
      status: string;
      error_message?: string;
      error_detail?: string;
      created_at?: string;
      updated_at?: string;
    }>;
    total_count: number;
    page_number: number;
    page_size: number;
    total_pages: number;
  }> {
    const url = `${this.baseUrl}/pipeline/stages/${encodeURIComponent(stageId)}/errors/records`;

    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
      body: JSON.stringify({
        error_text: errorText,
        page_number: pageNumber,
        page_size: pageSize
      }),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to get records by error: ${response.statusText}`));
    }
    return response.json();
  }

  // =============================================================================
  // Orchestration Management
  // =============================================================================

  /**
   * Terminate a running orchestration
   */
  async terminateOrchestration(
    actionUrl: string,
    reason?: string,
  ): Promise<{ status: string; message: string }> {
    const url = `${this.baseUrl}/pipeline/orchestration/action`;
    const body: Record<string, string> = { action_url: actionUrl };
    if (reason) body.reason = reason;

    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to terminate orchestration: ${response.statusText}`));
    }
    return response.json();
  }

  /**
   * Suspend (pause) a running orchestration
   */
  async suspendOrchestration(
    actionUrl: string,
    reason?: string,
  ): Promise<{ status: string; message: string }> {
    const url = `${this.baseUrl}/pipeline/orchestration/action`;
    const body: Record<string, string> = { action_url: actionUrl };
    if (reason) body.reason = reason;

    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to suspend orchestration: ${response.statusText}`));
    }
    return response.json();
  }

  /**
   * Resume a suspended orchestration
   */
  async resumeOrchestration(
    actionUrl: string,
    reason?: string,
  ): Promise<{ status: string; message: string }> {
    const url = `${this.baseUrl}/pipeline/orchestration/action`;
    const body: Record<string, string> = { action_url: actionUrl };
    if (reason) body.reason = reason;

    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to resume orchestration: ${response.statusText}`));
    }
    return response.json();
  }

  // ==================== Active Pipeline Jobs ====================

  /**
   * Get all active pipeline jobs
   */
  async getActiveJobs(): Promise<{ jobs: ActiveJob[] }> {
    const url = `${this.baseUrl}/pipeline/jobs`;

    const response = await fetch(url, {
      method: 'GET',
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to get active jobs: ${response.statusText}`));
    }
    return response.json();
  }

  /**
   * Update an active job's status (by trigger_endpoint)
   * 
   * The backend saves current_stats based on runtime_status:
   * - Completed/Failed/Terminated/Canceled: saves output as current_stats
   * - Running/Pending: saves input as current_stats
   */
  async updateActiveJob(triggerEndpoint: string, update: {
    runtime_status: string;
    custom_status?: any;
    input?: any;
    output?: any;
    last_updated_time?: string;
  }): Promise<ActiveJob> {
    const url = `${this.baseUrl}/pipeline/jobs/${encodeURIComponent(triggerEndpoint)}`;

    const response = await fetch(url, {
      method: 'PATCH',
      headers: await this.getHeaders(),
      credentials: 'include',
      body: JSON.stringify(update),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to update job: ${response.statusText}`));
    }
    return response.json();
  }

  /**
   * Reset all pipeline jobs by deleting all job records from Cosmos DB.
   * Deletes all documents with ID prefix 'pipeline_step_'.
   */
  async resetAllJobs(): Promise<{
    status: string;
    deleted_count: number;
    error_count: number;
  }> {
    const url = `${this.baseUrl}/pipeline/jobs/reset-all`;

    const response = await fetch(url, {
      method: 'DELETE',
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to reset jobs: ${response.statusText}`));
    }
    return response.json();
  }

  // ==================== Pipeline Configuration ====================

  /**
   * Save pipeline runtime configuration
   */
  async savePipelineConfig(config: {
    collection_ids?: string[];
    batch_size?: number;
    parallel_batches?: number;
  }): Promise<{
    collection_ids: string[];
    batch_size: number;
    parallel_batches: number;
    updated_at: string;
  }> {
    const url = `${this.baseUrl}/pipeline/config`;

    const response = await fetch(url, {
      method: 'PUT',
      headers: await this.getHeaders(),
      credentials: 'include',
      body: JSON.stringify(config),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to save pipeline config: ${response.statusText}`));
    }
    return response.json();
  }

  /**
   * Reset pipeline configuration to defaults
   */
  async resetPipelineConfig(): Promise<{
    message: string;
    config: {
      collection_ids: string[];
      batch_size: number;
      parallel_batches: number;
      updated_at: string | null;
    };
  }> {
    const url = `${this.baseUrl}/pipeline/config`;

    const response = await fetch(url, {
      method: 'DELETE',
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to reset pipeline config: ${response.statusText}`));
    }
    return response.json();
  }

  // ==================== Search Index Management ====================

  /**
   * Restore the AI Search index by re-indexing all published documents
   */
  async restoreSearchIndex(): Promise<{
    success: boolean;
    message: string;
    instance_id?: string;
    status_url?: string;
    terminate_url?: string;
    suspend_url?: string;
    resume_url?: string;
  }> {
    const url = `${this.baseUrl}/search-index/restore`;

    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to restore search index: ${response.statusText}`));
    }
    return response.json();
  }

  /**
   * Manage search index restore job - single endpoint with actions.
   * Actions: "status", "update", "cancel"
   */
  async manageSearchIndexRestoreJob(request: {
    action: 'status' | 'update' | 'cancel';
    status?: string;  // For "update" - Durable Functions status
    error_message?: string;
  }): Promise<{
    has_job?: boolean;
    job_id?: string;
    status?: string;
    total_documents?: number;
    processed_documents?: number;
    started_at?: string;
    started_by?: string;
    updated_at?: string;
    completed_at?: string;
    error_message?: string;
    message?: string;
    instance_id?: string;
    status_url?: string;
    terminate_url?: string;
    suspend_url?: string;
    resume_url?: string;
    success?: boolean;
    cancelled_by?: string;
    job?: Record<string, unknown>;
  }> {
    const url = `${this.baseUrl}/search-index/restore/job`;

    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
      body: JSON.stringify(request),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to manage restore job: ${response.statusText}`));
    }
    return response.json();
  }

  // Convenience methods for search index restore
  async getSearchIndexRestoreStatus() {
    return this.manageSearchIndexRestoreJob({ action: 'status' });
  }

  async updateSearchIndexRestoreStatus(update: { status: string; error_message?: string }) {
    return this.manageSearchIndexRestoreJob({ action: 'update', ...update });
  }

  async cancelSearchIndexRestore() {
    return this.manageSearchIndexRestoreJob({ action: 'cancel' });
  }

  // ==================== Bulk Ingestion Management ====================

  /**
   * Manage bulk ingestion job - single endpoint with actions.
   * Actions: "status", "update", "cancel"
   */
  async manageBulkIngestionJob(request: {
    action: 'status' | 'update' | 'cancel';
    status?: string;  // For "update" - Durable Functions status
    processed_documents?: number;
    total_documents?: number;
    error_message?: string;
  }): Promise<{
    has_job?: boolean;
    job_id?: string;
    job_type?: 'query' | 'ids';
    operation_type?: 'bulk' | 'retry_failed' | 'reindex';
    status?: string;
    total_documents?: number;
    processed_documents?: number;
    success_documents?: number;
    failed_documents?: number;
    started_at?: string;
    started_by?: string;
    updated_at?: string;
    completed_at?: string;
    error_message?: string;
    instance_id?: string;
    status_url?: string;
    terminate_url?: string;
    suspend_url?: string;
    resume_url?: string;
    query_filters?: Record<string, unknown>;
    document_ids?: string[];
    success?: boolean;
    cancelled_by?: string;
    job?: Record<string, unknown>;
  }> {
    const url = `${this.baseUrl}/ingestion/job`;

    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
      body: JSON.stringify(request),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to manage ingestion job: ${response.statusText}`));
    }
    return response.json();
  }

  // Convenience methods for bulk ingestion
  async getBulkIngestionStatus() {
    return this.manageBulkIngestionJob({ action: 'status' });
  }

  /** Bulk/retry publish batch audit (Cosmos). */
  async listPublishBatches(limit?: number): Promise<{
    success: boolean;
    batches: Record<string, unknown>[];
    count: number;
  }> {
    const q = limit != null ? `?limit=${encodeURIComponent(String(limit))}` : '';
    const url = `${this.baseUrl}/ingestion/publish-batches${q}`;
    const response = await fetch(url, { method: 'GET', headers: await this.getHeaders(), credentials: 'include' });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(extractErrorMessage(error, `Request failed: ${response.status}`));
    }
    return response.json();
  }

  async searchPublishBatchesByRecordId(
    recordId: string,
    limit?: number
  ): Promise<{
    success: boolean;
    record_id: string;
    batches: Record<string, unknown>[];
    count: number;
  }> {
    const p = new URLSearchParams({ record_id: recordId });
    if (limit != null) p.set('limit', String(limit));
    const url = `${this.baseUrl}/ingestion/publish-batches/search?${p.toString()}`;
    const response = await fetch(url, { method: 'GET', headers: await this.getHeaders(), credentials: 'include' });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(extractErrorMessage(error, `Request failed: ${response.status}`));
    }
    return response.json();
  }

  async getPublishBatch(batchId: string): Promise<{ success: boolean; batch: Record<string, unknown> }> {
    const url = `${this.baseUrl}/ingestion/publish-batches/${encodeURIComponent(batchId)}`;
    const response = await fetch(url, { method: 'GET', headers: await this.getHeaders(), credentials: 'include' });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(extractErrorMessage(error, `Request failed: ${response.status}`));
    }
    return response.json();
  }

  async unpublishPublishBatch(batchId: string): Promise<Record<string, unknown>> {
    const url = `${this.baseUrl}/ingestion/publish-batches/${encodeURIComponent(batchId)}/unpublish`;
    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
      body: JSON.stringify({}),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(extractErrorMessage(error, `Request failed: ${response.status}`));
    }
    return response.json();
  }

  async updateBulkIngestionStatus(update: { status: string; error_message?: string }) {
    return this.manageBulkIngestionJob({ action: 'update', ...update });
  }

  async cancelBulkIngestion() {
    return this.manageBulkIngestionJob({ action: 'cancel' });
  }

  // ==================== Retry Failed Documents ====================

  /**
   * Trigger retry of all failed documents.
   * Uses the bulk ingestion flow with a filter for archivist_status = 'failed' (case-insensitive in DB).
   */
  async retryFailedDocuments(): Promise<{
    success: boolean;
    message: string;
    instance_id?: string;
    status_url?: string;
    terminate_url?: string;
    suspend_url?: string;
    resume_url?: string;
    total_documents?: number;
  }> {
    const url = `${this.baseUrl}/ingestion/retry-failed`;

    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || `Failed to start retry: ${response.status}`);
    }

    return response.json();
  }

  /**
   * Get count of failed documents
   */
  async getFailedDocumentsCount(): Promise<{ failed_count: number }> {
    const url = `${this.baseUrl}/ingestion/failed-count`;

    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || `Request failed: ${response.status}`);
    }

    return response.json();
  }

  /**
   * Get unique error messages from failed documents with counts
   */
  async getFailedErrorsSummary(
    page: number = 1, 
    pageSize: number = 20
  ): Promise<{
    errors: Array<{ error_text: string; count: number }>;
    total_unique_errors: number;
    total_error_count: number;
    page_number: number;
    page_size: number;
    total_pages: number;
  }> {
    const url = `${this.baseUrl}/ingestion/failed-errors?page=${page}&page_size=${pageSize}`;

    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || `Request failed: ${response.status}`);
    }

    return response.json();
  }

  /**
   * Get paginated list of failed documents, optionally filtered by error message
   */
  async getFailedDocuments(
    page: number = 1,
    pageSize: number = 20,
    errorFilter?: string
  ): Promise<{
    documents: Array<{
      id: string;
      record_id?: string;
      title?: string;
      label?: string;
      repository?: string;
      collection_name?: string;
      archivist_status: string;
      archivist_error_message?: string;
      _ts?: number;
    }>;
    error_text?: string;
    total_count: number;
    page_number: number;
    page_size: number;
    total_pages: number;
  }> {
    let url = `${this.baseUrl}/ingestion/failed-documents?page=${page}&page_size=${pageSize}`;
    if (errorFilter) {
      url += `&error=${encodeURIComponent(errorFilter)}`;
    }

    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || `Request failed: ${response.status}`);
    }

    return response.json();
  }

  /**
   * Admin: documents stuck in publishing longer than threshold (default 30 minutes).
   */
  async getStuckPublishingDocuments(thresholdMinutes = 20): Promise<{
    threshold_minutes: number;
    count: number;
    documents: Array<{
      id?: string;
      record_id?: string;
      title?: string;
      archivist_publish_started_at?: string;
      updated_at?: string;
      archivist_error_message?: string;
    }>;
  }> {
    const url = `${this.baseUrl}/documents/publishing/stuck?threshold_minutes=${thresholdMinutes}`;
    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || `Request failed: ${response.status}`);
    }
    return response.json();
  }

  /**
   * Admin: mark stuck publishing documents as failed or reviewed.
   */
  async reconcileStuckPublishing(params: {
    threshold_minutes?: number;
    action?: 'failed' | 'reviewed';
  }): Promise<{
    threshold_minutes: number;
    action: string;
    matched: number;
    updated: number;
    document_ids: string[];
    errors?: Array<{ id: string; error: string }>;
  }> {
    const url = `${this.baseUrl}/documents/publishing/reconcile`;
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        ...(await this.getHeaders()),
        'Content-Type': 'application/json',
      },
      credentials: 'include',
      body: JSON.stringify({
        threshold_minutes: params.threshold_minutes ?? 20,
        action: params.action ?? 'reviewed',
      }),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || `Request failed: ${response.status}`);
    }
    return response.json();
  }

  // ==================== Unpublish Documents ====================

  /**
   * Get list of publishers (users who published documents) with their publication dates
   */
  async getPublishersList(): Promise<{
    publishers: Array<{
      user: string;
      dates: string[];
    }>;
  }> {
    const url = `${this.baseUrl}/ingestion/publishers`;

    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || `Request failed: ${response.status}`);
    }

    return response.json();
  }

  /**
   * Unpublish documents published by a specific user on a specific date
   */
  async unpublishDocuments(publishedBy: string, publishedDate: string): Promise<{
    success: boolean;
    message: string;
    instance_id?: string;
    status_url?: string;
    terminate_url?: string;
    suspend_url?: string;
    resume_url?: string;
    total_documents?: number;
  }> {
    const url = `${this.baseUrl}/ingestion/unpublish`;

    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
      body: JSON.stringify({
        published_by: publishedBy,
        published_date: publishedDate,
      }),
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || `Failed to start unpublish: ${response.status}`);
    }

    return response.json();
  }

  /**
   * Unpublish published documents in a collection matching optional filters (always published status).
   */
  async unpublishDocumentsByFilters(params: {
    repository: string;
    collection: string;
    resource_type?: string;
    min_confidence?: number;
    max_confidence?: number;
    title_contains?: string;
  }): Promise<{
    success: boolean;
    message: string;
    instance_id?: string;
    status_url?: string;
    terminate_url?: string;
    suspend_url?: string;
    resume_url?: string;
    total_documents?: number;
  }> {
    const url = `${this.baseUrl}/ingestion/unpublish/by-filters`;

    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
      body: JSON.stringify(params),
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(
        typeof error.detail === 'string'
          ? error.detail
          : error.detail?.message || `Failed to start unpublish: ${response.status}`
      );
    }

    return response.json();
  }

  /**
   * Get unpublish job status
   */
  async getUnpublishStatus(): Promise<{
    has_job?: boolean;
    job_id?: string;
    status?: string;
    total_documents?: number;
    processed_documents?: number;
    success_documents?: number;
    failed_documents?: number;
    started_at?: string;
    started_by?: string;
    published_by?: string;
    published_date?: string;
    updated_at?: string;
    completed_at?: string;
    error_message?: string;
    instance_id?: string;
    status_url?: string;
    terminate_url?: string;
    suspend_url?: string;
    resume_url?: string;
  }> {
    const url = `${this.baseUrl}/ingestion/unpublish/job`;

    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
      body: JSON.stringify({ action: 'status' }),
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || `Request failed: ${response.status}`);
    }

    return response.json();
  }

  /**
   * Update unpublish job status
   */
  async updateUnpublishStatus(updates: {
    status?: string;
    error_message?: string;
  }): Promise<{ success: boolean; job?: unknown }> {
    const url = `${this.baseUrl}/ingestion/unpublish/job`;

    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
      body: JSON.stringify({
        action: 'update',
        ...updates,
      }),
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || `Request failed: ${response.status}`);
    }

    return response.json();
  }

  /**
   * Cancel unpublish job
   */
  async cancelUnpublishJob(): Promise<{ success: boolean; message: string }> {
    const url = `${this.baseUrl}/ingestion/unpublish/job`;

    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
      body: JSON.stringify({ action: 'cancel' }),
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || `Request failed: ${response.status}`);
    }

    return response.json();
  }

  /**
   * Unpublish a single document - sends message to Service Bus
   */
  async unpublishSingleDocument(documentId: string): Promise<{
    success: boolean;
    message: string;
    document_id: string;
  }> {
    const url = `${this.baseUrl}/documents/${documentId}/unpublish`;

    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || `Failed to unpublish: ${response.status}`);
    }

    return response.json();
  }

  // ==================== Field Mapping Configuration ====================

  /**
   * Get all field mappings, optionally filtered by repository and/or collection
   */
  async getFieldMappings(params?: {
    repository?: string;
    collection?: string;
  }): Promise<{
    mappings: FieldMapping[];
    count: number;
  }> {
    const queryParams = new URLSearchParams();
    if (params?.repository) queryParams.append('repository', params.repository);
    if (params?.collection) queryParams.append('collection', params.collection);

    const url = `${this.baseUrl}/field-mappings${queryParams.toString() ? '?' + queryParams.toString() : ''}`;

    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to get field mappings: ${response.statusText}`));
    }
    return response.json();
  }

  /**
   * Resolve the effective field mapping for a repository/collection
   * Falls back to repository-level mapping if collection-specific one doesn't exist
   */
  async resolveFieldMapping(
    repository: string,
    collection?: string
  ): Promise<{
    mapping: {
      identifier_field: string;
      display_name?: string;
      repository?: string;
      collection?: string;
    };
    is_default: boolean;
  }> {
    const queryParams = new URLSearchParams();
    queryParams.append('repository', repository);
    if (collection) queryParams.append('collection', collection);

    const url = `${this.baseUrl}/field-mappings/resolve?${queryParams.toString()}`;

    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to resolve field mapping: ${response.statusText}`));
    }
    return response.json();
  }

  /**
   * Create a new field mapping
   */
  async createFieldMapping(data: {
    repository: string;
    collection?: string;
    identifier_field: string;
    display_name?: string;
  }): Promise<{ mapping: FieldMapping }> {
    const url = `${this.baseUrl}/field-mappings`;

    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
      body: JSON.stringify(data),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to create field mapping: ${response.statusText}`));
    }
    return response.json();
  }

  /**
   * Update an existing field mapping
   */
  async updateFieldMapping(
    mappingId: string,
    data: {
      identifier_field?: string;
      display_name?: string;
    }
  ): Promise<{ mapping: FieldMapping }> {
    const url = `${this.baseUrl}/field-mappings/${encodeURIComponent(mappingId)}`;

    const response = await fetch(url, {
      method: 'PUT',
      headers: await this.getHeaders(),
      credentials: 'include',
      body: JSON.stringify(data),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to update field mapping: ${response.statusText}`));
    }
    return response.json();
  }

  /**
   * Delete a field mapping
   */
  async deleteFieldMapping(mappingId: string): Promise<{ message: string }> {
    const url = `${this.baseUrl}/field-mappings/${encodeURIComponent(mappingId)}`;

    const response = await fetch(url, {
      method: 'DELETE',
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to delete field mapping: ${response.statusText}`));
    }
    return response.json();
  }

  /**
   * Get available metadata fields for field mapping
   */
  async getAvailableMetadataFields(): Promise<{
    fields: string[];
    count: number;
  }> {
    const url = `${this.baseUrl}/metadata-fields`;

    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(errorData, `Failed to get metadata fields: ${response.statusText}`));
    }
    return response.json();
  }

  async listCorrectionRequests(params: {
    status?: string;
    limit?: number;
    offset?: number;
    sort?: 'asc' | 'desc';
  }): Promise<{
    items: CorrectionRequestItem[];
    next_cursor: string | null;
    has_more?: boolean;
    total?: number;
    offset?: number;
    limit?: number;
    sort?: string;
  }> {
    const query = new URLSearchParams();
    if (params.status) query.set('status', params.status);
    if (params.limit != null) query.set('limit', String(params.limit));
    if (params.offset != null) query.set('offset', String(params.offset));
    if (params.sort) query.set('sort', params.sort);
    const qs = query.toString();
    const url = `${this.baseUrl}/correction-requests${qs ? `?${qs}` : ''}`;

    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(
        extractErrorMessage(errorData, `Failed to list correction requests: ${response.statusText}`)
      );
    }
    return response.json();
  }

  async patchCorrectionRequest(
    requestId: string,
    body: { status: 'reviewed' | 'dismissed' | 'addressed'; dismissal_note?: string }
  ): Promise<CorrectionRequestItem> {
    const url = `${this.baseUrl}/correction-requests/${encodeURIComponent(requestId)}`;

    const response = await fetch(url, {
      method: 'PATCH',
      headers: await this.getHeaders(),
      credentials: 'include',
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(
        extractErrorMessage(errorData, `Failed to update correction request: ${response.statusText}`)
      );
    }
    return response.json();
  }

  async getDigitalItemsBySource(
    source: string,
    itemType?: string,
    maxItems?: number,
  ): Promise<DigitalItemsListResponse> {
    const params = new URLSearchParams();
    if (itemType) params.set('item_type', itemType);
    if (maxItems) params.set('max_items', String(maxItems));
    const query = params.toString() ? `?${params.toString()}` : '';
    const url = `${this.baseUrl}/digital-items/sources/${encodeURIComponent(source)}${query}`;

    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(
        extractErrorMessage(errorData, `Failed to list digital items: ${response.statusText}`),
      );
    }
    return response.json();
  }

  async getDigitalItem(itemId: string, source: string): Promise<DigitalItem> {
    const url = `${this.baseUrl}/digital-items/items/${encodeURIComponent(itemId)}?source=${encodeURIComponent(source)}`;

    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(
        extractErrorMessage(errorData, `Failed to get digital item: ${response.statusText}`),
      );
    }
    return response.json();
  }

  async getDigitalItemPage(
    itemId: string,
    source: string,
    pageNumber: number,
  ): Promise<DigitalItemPage> {
    const url = `${this.baseUrl}/digital-items/items/${encodeURIComponent(itemId)}/pages/${pageNumber}?source=${encodeURIComponent(source)}`;

    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(
        extractErrorMessage(errorData, `Failed to get digital item page: ${response.statusText}`),
      );
    }
    return response.json();
  }

  // ==================== Digital Items Ingestion ====================

  async startDigitalIngestion(source: string, step?: string): Promise<{ success: boolean; message: string; sources: string[] }> {
    let url = `${this.baseUrl}/digital-items/ingestion/start?source=${encodeURIComponent(source)}`;
    if (step) {
      url += `&step=${encodeURIComponent(step)}`;
    }
    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || `Request failed: ${response.status}`);
    }
    return response.json();
  }

  async getDigitalIngestionStatus(): Promise<{ jobs: Record<string, DigitalIngestionJob> }> {
    const url = `${this.baseUrl}/digital-items/ingestion/status`;
    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || `Request failed: ${response.status}`);
    }
    return response.json();
  }

  async getDigitalItemsSummary(): Promise<Record<string, number>> {
    const url = `${this.baseUrl}/digital-items/summary`;
    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || `Request failed: ${response.status}`);
    }
    return response.json();
  }

  async clearDigitalIngestionJob(source: string): Promise<{ success: boolean }> {
    const url = `${this.baseUrl}/digital-items/ingestion/clear/${encodeURIComponent(source)}`;
    const response = await fetch(url, {
      method: 'DELETE',
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || `Request failed: ${response.status}`);
    }
    return response.json();
  }

  async clearAllDigitalIngestionJobs(): Promise<{ success: boolean }> {
    const url = `${this.baseUrl}/digital-items/ingestion/clear-all`;
    const response = await fetch(url, {
      method: 'DELETE',
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || `Request failed: ${response.status}`);
    }
    return response.json();
  }

  async stopDigitalIngestion(source: string): Promise<{ success: boolean; source: string; message: string }> {
    const url = `${this.baseUrl}/digital-items/ingestion/stop/${encodeURIComponent(source)}`;
    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || `Request failed: ${response.status}`);
    }
    return response.json();
  }

  // ─── OCR Processing (GPT-4 Vision) ───

  async startOcrProcessing(source: string): Promise<{ success: boolean; message: string; sources: string[] }> {
    const url = `${this.baseUrl}/digital-items/ocr/start?source=${encodeURIComponent(source)}`;
    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || `Request failed: ${response.status}`);
    }
    return response.json();
  }

  async getOcrStatus(): Promise<{ jobs: Record<string, OcrJob> }> {
    const url = `${this.baseUrl}/digital-items/ocr/status`;
    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || `Request failed: ${response.status}`);
    }
    return response.json();
  }

  // ─── Publish / Unpublish Digital Items to Search Index ───

  async publishDigitalItems(source: string): Promise<{ success: boolean; message: string; source: string }> {
    const url = `${this.baseUrl}/digital-items/publish/${encodeURIComponent(source)}`;
    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || `Request failed: ${response.status}`);
    }
    return response.json();
  }

  async unpublishDigitalItems(source: string): Promise<{ success: boolean; message: string; source: string }> {
    const url = `${this.baseUrl}/digital-items/unpublish/${encodeURIComponent(source)}`;
    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || `Request failed: ${response.status}`);
    }
    return response.json();
  }

  async reviewDigitalItem(itemId: string, source: string): Promise<{ success: boolean; message: string; publish_status: string }> {
    const url = `${this.baseUrl}/digital-items/items/${encodeURIComponent(itemId)}/review?source=${encodeURIComponent(source)}`;
    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || `Request failed: ${response.status}`);
    }
    return response.json();
  }

  async publishDigitalItem(itemId: string, source: string): Promise<{ success: boolean; message: string; published_at: string }> {
    const url = `${this.baseUrl}/digital-items/items/${encodeURIComponent(itemId)}/publish?source=${encodeURIComponent(source)}`;
    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || `Request failed: ${response.status}`);
    }
    return response.json();
  }

  async unpublishDigitalItem(itemId: string, source: string): Promise<{ success: boolean; message: string }> {
    const url = `${this.baseUrl}/digital-items/items/${encodeURIComponent(itemId)}/unpublish?source=${encodeURIComponent(source)}`;
    const response = await fetch(url, {
      method: 'POST',
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || `Request failed: ${response.status}`);
    }
    return response.json();
  }

  async getDigitalItemsPublishStatus(): Promise<{ jobs: Record<string, any> }> {
    const url = `${this.baseUrl}/digital-items/publish/status`;
    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || `Request failed: ${response.status}`);
    }
    return response.json();
  }

  // ==================== Digital Items OCR Editing ====================

  async updateDigitalItemOcr(
    itemId: string,
    source: string,
    ocrText: string,
    correlationId?: string,
  ): Promise<{ success: boolean; message: string; version: number }> {
    const url = `${this.baseUrl}/digital-items/items/${encodeURIComponent(itemId)}/ocr?source=${encodeURIComponent(source)}`;
    const response = await fetch(url, {
      method: 'PATCH',
      headers: await this.getHeaders(),
      credentials: 'include',
      body: JSON.stringify({ ocr_text: ocrText, correlation_id: correlationId }),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || `Request failed: ${response.status}`);
    }
    return response.json();
  }

  async compareDigitalItemOcr(
    itemId: string,
    source: string,
    pageNumber?: number,
  ): Promise<DigitalItemOcrCompare> {
    let url = `${this.baseUrl}/digital-items/items/${encodeURIComponent(itemId)}/ocr/compare?source=${encodeURIComponent(source)}`;
    if (pageNumber != null) {
      url += `&page_number=${pageNumber}`;
    }
    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || `Request failed: ${response.status}`);
    }
    return response.json();
  }

  async getDigitalItemOcrHistory(
    itemId: string,
    source: string,
    maxItems?: number,
  ): Promise<DigitalItemOcrHistory> {
    let url = `${this.baseUrl}/digital-items/items/${encodeURIComponent(itemId)}/ocr/history?source=${encodeURIComponent(source)}`;
    if (maxItems != null) {
      url += `&max_items=${maxItems}`;
    }
    const response = await fetch(url, {
      headers: await this.getHeaders(),
      credentials: 'include',
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || `Request failed: ${response.status}`);
    }
    return response.json();
  }

}

// Field mapping type definition
export interface FieldMapping {
  id: string;
  type: string;
  repository: string;
  collection?: string;
  identifier_field: string;
  display_name?: string;
  created_at: string;
  updated_at: string;
  created_by?: string;
}

export const apiService = new ApiService();
