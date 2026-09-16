// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { ApiDocumentMetadata } from '@/services/api';

export type FieldStatus = 'metadata' | 'extracted' | 'missing';

export interface FieldStatusMap {
  [fieldName: string]: FieldStatus;
}

export interface MappedFieldValues {
  values: {
    title: string;
    description: string;
    creator: string;
    recipient: string;
    creationDate: string;
    resourceType: string;
    period: string;
    rights: string;
    productionMethod: string;
    language: string;
    citation: string;
  };
  status: FieldStatusMap;
}


const extractString = (value: any): string => {
  if (!value) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'object') {
    if (value.label) return value.label;
    if (value.name) return value.name;
    if (value.title) return value.title;
    if (value.text) return value.text;
    return '';
  }
  return String(value);
};

const parseHtmlDescription = (html: string): string => {
  if (!html) return '';
  
  // Check if description appears to be HTML
  if (html.trim().startsWith('<')) {
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, 'text/html');
    const content = doc.body?.textContent || doc.documentElement?.textContent || '';
    return content.trim().replace(/\s+/g, ' ');
  }
  
  return html;
};

const isEmptyOrUnknown = (value: string): boolean => {
  if (!value) return true;
  const normalized = value.trim().toLowerCase();
  return normalized === '' || 
         normalized === 'unknown' || 
         normalized === 'n/a' || 
         normalized === 'na' ||
         normalized === 'null' ||
         normalized === 'undefined' ||
         normalized === 'none';
};


export function mapDocumentFieldsWithStatus(apiDoc: ApiDocumentMetadata): MappedFieldValues {
  const metadata = apiDoc.metadata || {};
  const extractedMetadata = apiDoc.extracted_metadata || {};
  
  const result: MappedFieldValues = {
    values: {
      title: '',
      description: '',
      creator: '',
      recipient: '',
      creationDate: '',
      resourceType: '',
      period: '',
      rights: '',
      productionMethod: '',
      language: '',
      citation: '',
    },
    status: {},
  };

  // Helper function to map a field with status and fallback logic
  const mapField = (
    fieldName: keyof typeof result.values,
    metadataKeys: string | string[], // Can be single key or array of possible keys
    extractedKeys?: string | string[] // Can be single key or array of possible keys
  ) => {
    let value = '';
    let status: FieldStatus = 'missing';

    // Normalize keys to arrays
    const metaKeyArray = Array.isArray(metadataKeys) ? metadataKeys : [metadataKeys];
    const extractedKeyArray = extractedKeys 
      ? (Array.isArray(extractedKeys) ? extractedKeys : [extractedKeys])
      : [];

    // First check metadata (verified data) - try all possible keys
    for (const metaKey of metaKeyArray) {
      const metadataValue = extractString(metadata[metaKey]);
      if (metadataValue && !isEmptyOrUnknown(metadataValue)) {
        value = metadataValue;
        status = 'metadata';
        break;
      }
    }

    // If metadata is empty or "Unknown", check extracted_metadata (AI extracted)
    if (!value || isEmptyOrUnknown(value)) {
      for (const extractedKey of extractedKeyArray) {
        const extractedValue = extractString(extractedMetadata[extractedKey]);
        if (extractedValue && !isEmptyOrUnknown(extractedValue)) {
          value = extractedValue;
          status = 'extracted';
          break;
        }
      }
    }

    result.values[fieldName] = value;
    result.status[fieldName] = status;
  };

  // Map all fields with their possible keys
  mapField('title', 'Title', 'title');
  
  // Description needs special handling for HTML
  const metadataDescriptionKeys = ['Description', 'Summary', 'Abstract'];
  const extractedDescriptionKeys = ['description', 'summary', 'subject'];
  
  let descriptionValue = '';
  let descriptionStatus: FieldStatus = 'missing';
  
  // Try metadata keys first
  for (const key of metadataDescriptionKeys) {
    const metadataDescription = metadata[key];
    if (metadataDescription) {
      const parsed = parseHtmlDescription(extractString(metadataDescription));
      if (parsed && !isEmptyOrUnknown(parsed)) {
        descriptionValue = parsed;
        descriptionStatus = 'metadata';
        break;
      }
    }
  }
  
  // Fallback to extracted metadata
  if (!descriptionValue || isEmptyOrUnknown(descriptionValue)) {
    for (const key of extractedDescriptionKeys) {
      const extractedDescription = extractedMetadata[key];
      if (extractedDescription) {
        const parsed = parseHtmlDescription(extractString(extractedDescription));
        if (parsed && !isEmptyOrUnknown(parsed)) {
          descriptionValue = parsed;
          descriptionStatus = 'extracted';
          break;
        }
      }
    }
  }
  
  result.values.description = descriptionValue;
  result.status.description = descriptionStatus;
  
  // Map other fields with multiple possible keys
  mapField('creator', ['Creator', 'Author', 'Writer'], ['sender', 'creator', 'author']);
  mapField('recipient', ['Recipient', 'Addressee', 'To'], ['recipient', 'addressee']);
  mapField('creationDate', ['Creation Date', 'Date Created', 'Date', 'Issue Date'], ['date', 'Creation Date', 'created_date']);
  mapField('resourceType', ['Resource Type', 'Type', 'Document Type'], ['resource_type', 'document_type', 'type']);
  mapField('period', ['Period', 'Era', 'Time Period'], ['period', 'era']);
  mapField('rights', ['Copyright Status', 'Image Rights', 'Rights', 'Copyright'], ['rights', 'copyright']);
  mapField('productionMethod', ['Production Method', 'Method', 'Format'], ['production_method', 'method']);
  mapField('language', ['Language', 'Languages'], ['language', 'languages']);
  mapField('citation', ['Citation', 'Source Citation', 'Reference'], ['citation']);

  return result;
}

export function getStatusColorClass(status: FieldStatus): string {
  switch (status) {
    case 'metadata':
      return 'text-blue-700 bg-blue-100 border-blue-200';
    case 'extracted':
      return 'text-green-700 bg-green-100 border-green-200';
    case 'missing':
      return 'text-red-700 bg-red-100 border-red-200';
  }
}

export function getFieldBorderClass(status: FieldStatus): string {
  switch (status) {
    case 'metadata':
      return 'border-l-4 border-blue-400 bg-blue-50/20';
    case 'extracted':
      return 'border-l-4 border-green-400 bg-green-50/30';
    case 'missing':
      return 'border-l-4 border-red-400 bg-red-50/30';
  }
}
