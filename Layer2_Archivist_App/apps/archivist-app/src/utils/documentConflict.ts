// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Optimistic concurrency (ETag / If-Match) conflicts when saving document edits.
 */

export const DOCUMENT_CONFLICT_TITLE = "Can't save this record";

export const DOCUMENT_CONFLICT_MESSAGE =
  'Two or more people may be working on this record, or you have another browser tab open with an older copy. ' +
  'Refresh this tab to load the latest version. Your changes on this screen cannot be saved until you reload.';

export const DOCUMENT_CONFLICT_TOAST =
  'This record was changed elsewhere. Refresh this tab to continue editing.';

export const DOCUMENT_CONFLICT_BANNER_TITLE =
  "Can't save — someone else may have this record open";

export const DOCUMENT_CONFLICT_BANNER_MESSAGE =
  'This record was updated in another tab or by another archivist. Refresh to view the latest version before editing again.';

export class DocumentConflictError extends Error {
  readonly status = 409;
  readonly isDocumentConflict = true;

  constructor(message: string = DOCUMENT_CONFLICT_MESSAGE) {
    super(message);
    this.name = 'DocumentConflictError';
  }
}

const CONFLICT_PHRASES = [
  'modified by another user',
  'please refresh',
  'etag mismatch',
  'precondition failed',
  'conflict',
];

export function isConflictStatus(status: number): boolean {
  return status === 409 || status === 412;
}

export function isConflictMessage(message: string): boolean {
  const lower = message.toLowerCase();
  return CONFLICT_PHRASES.some((phrase) => lower.includes(phrase));
}

export function isDocumentConflictError(error: unknown): error is DocumentConflictError {
  if (error instanceof DocumentConflictError) {
    return true;
  }
  if (error && typeof error === 'object') {
    if ('isDocumentConflict' in error && (error as { isDocumentConflict?: unknown }).isDocumentConflict === true) {
      return true;
    }
    if ('status' in error) {
      const status = (error as { status?: unknown }).status;
      if (typeof status === 'number' && isConflictStatus(status)) {
        return true;
      }
    }
  }
  if (error instanceof Error) {
    return isConflictMessage(error.message);
  }
  return false;
}

/** Prefer conflict dialog over a generic toast when a save fails due to stale ETag. */
export function handleDocumentSaveError(
  error: unknown,
  options: {
    setConflictDialogOpen: (open: boolean) => void;
    setToast?: (toast: { type: 'success' | 'error' | 'info'; message: string } | null) => void;
    fallbackMessage?: string;
  }
): boolean {
  if (isDocumentConflictError(error)) {
    options.setConflictDialogOpen(true);
    options.setToast?.({
      type: 'info',
      message: DOCUMENT_CONFLICT_TOAST,
    });
    return true;
  }

  if (options.setToast) {
    options.setToast({
      type: 'error',
      message:
        error instanceof Error
          ? error.message
          : options.fallbackMessage ?? 'Failed to save changes. Please try again.',
    });
  }
  return false;
}
