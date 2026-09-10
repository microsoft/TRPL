import { ApiDocumentMetadata } from "@/services/api";
import { ArchivalRecord, ResourceType } from "@/types";

export function mapApiDocumentToRecord(apiDoc: ApiDocumentMetadata): ArchivalRecord {
  // Metadata is at root level in the API response
  const metadata = (apiDoc as any).metadata || {};
  
  // Helper function to extract string from potentially object values
  const extractString = (value: any): string => {
    if (!value) return '';
    if (typeof value === 'string') return value;
    if (typeof value === 'object') {
      // Try common object property names
      if (value.label) return value.label;
      if (value.name) return value.name;
      if (value.title) return value.title;
      if (value.text) return value.text;
      // Fallback: stringify the object
      return '';
    }
    return String(value);
  };

  // Get extracted metadata (AI-extracted)
  const extractedMetadata = (apiDoc as any).extracted_metadata || {};
  
  // Title fallback: metadata.Title -> extracted_metadata.title -> extracted_metadata.subject -> filename -> 'Untitled Document'
  const title = extractString(metadata.Title) 
    || extractString(extractedMetadata.title) 
    || extractString(extractedMetadata.subject) 
    || apiDoc.filename 
    || 'Untitled Document';
  const date = extractString(metadata['Creation Date']) || apiDoc.created_at || apiDoc.processed_at || '';
  const creatorName = extractString(metadata.Creator);
  const recipientName = extractString(metadata.Recipient);
  const repository = extractString(metadata.Repository);
  const collection = extractString(metadata.Collection); // This will extract from object if needed
  const resourceType = extractString(metadata['Resource Type']);
  
  // Calculate OCR confidence from asset_avg_confidence (prioritize this for OCR)
  let ocrConfidence = 0;
  if (apiDoc.asset_avg_confidence !== undefined && apiDoc.asset_avg_confidence !== null) {
    // asset_avg_confidence is a decimal (0.0-1.0), convert to percentage
    ocrConfidence = isNaN(apiDoc.asset_avg_confidence) ? 0 : Math.round(apiDoc.asset_avg_confidence * 100);
  } else if (apiDoc.ocr_result?.confidence !== undefined && apiDoc.ocr_result?.confidence !== null) {
    // Fallback to ocr_result.confidence (older format)
    ocrConfidence = isNaN(apiDoc.ocr_result.confidence) ? 0 : Math.round(apiDoc.ocr_result.confidence * 100);
  }
  
  // Calculate Metadata extraction confidence from metadata_extraction_confidence
  let metadataConfidence = 0;
  if (apiDoc.metadata_extraction_confidence !== undefined && apiDoc.metadata_extraction_confidence !== null) {
    // metadata_extraction_confidence is a decimal (0.0-1.0), convert to percentage
    metadataConfidence = isNaN(apiDoc.metadata_extraction_confidence) ? 0 : Math.round(apiDoc.metadata_extraction_confidence * 100);
  }

  
  // Determine status based on archivist_status, published flag, and document status
  let status: ArchivalRecord['status'] = 'pending';
  const archivistStatus = (apiDoc.archivist_status || '').trim().toLowerCase();
  if (archivistStatus === 'publishing') {
    status = 'publishing';
  } else if (archivistStatus === 'reviewed') {
    status = 'reviewed';
  } else if (archivistStatus === 'published') {
    status = 'published';
  } else if (archivistStatus === 'failed' || archivistStatus === 'error') {
    status = 'failed';
  } else {
    status = 'pending';
  }
  
  // Generate source code from collection name (or repository as fallback)
  const sourceForCode = collection || repository;
  const sourceCode = sourceForCode 
    ? sourceForCode.split(' ').map((word: string) => word[0]).join('').substring(0, 3).toUpperCase()
    : '';
  
  
  // Extract description from metadata, parse HTML if needed
  let content = '';
  const description = extractString(metadata.Description);
  if (description) {
    // Check if description appears to be HTML
    if (description.trim().startsWith('<')) {
      // Parse HTML and extract text content
      const parser = new DOMParser();
      const doc = parser.parseFromString(description, 'text/html');
      // Get text from body, or fallback to entire document text
      content = doc.body?.textContent || doc.documentElement?.textContent || '';
      // Clean up extra whitespace
      content = content.trim().replace(/\s+/g, ' ');
    } else {
      // Plain text description
      content = description;
    }
  }
  
  // Format last edited info from archivist_modified_ts
  let lastEdited: { time: string; user: string } | undefined = undefined;
  if (apiDoc.archivist_modified_ts) {
    const formattedTime = new Intl.DateTimeFormat('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    }).format(new Date(apiDoc.archivist_modified_ts));
    
    lastEdited = {
      time: formattedTime,
      user: apiDoc.published_by || apiDoc.validated_by || 'Archivist'
    };
  }
  
  const assetCount = apiDoc.asset_count || 0;

  return {
    id: apiDoc.id,
    title: title,
    content: content,
    date: date,
    dateType: 'created',
    creator: {
      name: creatorName,
      role: extractString(metadata['Creator Role'])
    },
    recipient: recipientName,
    ocr_processing_status: apiDoc.ocr_processing_status || "pending",
    related_assets_status: apiDoc.related_assets_status,
    asset_details_status: apiDoc.asset_details_status,
    original_file_status: apiDoc.original_file_status,
    ocr_batch_status: apiDoc.ocr_batch_status,
    metadata_extraction_status: apiDoc.metadata_extraction_status,
    visual_description_possible: (apiDoc as any).visual_description_possible || apiDoc.visual_description_possible,
    source: {
      repository: repository,
      collection: collection,
      code: sourceCode,
      color: getColorForSource(collection || repository)
    },
    resourceType: resourceType,
    aiConfidence: ocrConfidence, // Backward compatibility
    ocrConfidence: ocrConfidence,
    metadataConfidence: metadataConfidence,
    status: status,
    lastEdited: lastEdited,
    flags: {
      severeDeviation: ocrConfidence < 50
    },
    documentId: extractString(metadata['Source Record ID']),
    assetCount: assetCount,
    // Review/Publish tracking
    validatedBy: apiDoc.validated_by,
    validatedAt: apiDoc.validated_at,
    publishedBy: apiDoc.published_by,
    publishedAt: apiDoc.published_at,
    // Raw metadata for dynamic field access (used by field mapping feature)
    rawMetadata: metadata,
    querySelector: function(arg0: string): unknown {
      throw new Error('Function not implemented.');
    }
  };
}

/**
 * Assign colors to different sources/collections
 */
function getColorForSource(sourceName: string): string {
  const lowerName = (sourceName || '').toLowerCase();
  
  if (lowerName.includes('library of congress') || lowerName.includes('loc')) {
    return '#2563eb'; // Blue
  } else if (lowerName.includes('harvard')) {
    return '#dc2626'; // Red
  } else if (lowerName.includes('roosevelt')) {
    return '#9333ea'; // Purple
  } else if (lowerName.includes('morris')) {
    return '#16a34a'; // Green
  } else {
    return '#6b7280'; // Gray (default)
  }
}

/**
 * Maps multiple API documents to ArchivalRecord array
 */
export function mapApiDocumentsToRecords(apiDocs: ApiDocumentMetadata[]): ArchivalRecord[] {
  return apiDocs.map(mapApiDocumentToRecord);
}