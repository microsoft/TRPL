// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Strips HTML tags from a string and decodes HTML entities.
 * Extracts content from the body tag if present.
 */
export function stripHtml(html: string): string {
  if (!html) return '';
  
  // Create a temporary div element to parse HTML
  const temp = document.createElement('div');
  temp.innerHTML = html;
  
  // Try to find body element first
  const bodyElement = temp.querySelector('body');
  const content = bodyElement || temp;
  
  // Get text content (automatically decodes HTML entities)
  const text = content.textContent || content.innerText || '';
  
  // Clean up extra whitespace
  return text.trim().replace(/\s+/g, ' ');
}

