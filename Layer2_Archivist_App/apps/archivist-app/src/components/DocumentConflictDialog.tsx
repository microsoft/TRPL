// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import ConfirmDialog from '@/components/ConfirmDialog';
import {
  DOCUMENT_CONFLICT_MESSAGE,
  DOCUMENT_CONFLICT_TITLE,
} from '@/utils/documentConflict';

type DocumentConflictDialogProps = {
  isOpen: boolean;
  onClose: () => void;
  onReload: () => void;
  isReloading?: boolean;
};

/**
 * Shown when a save fails because the server ETag no longer matches (another tab/user saved first).
 */
export default function DocumentConflictDialog({
  isOpen,
  onClose,
  onReload,
  isReloading = false,
}: DocumentConflictDialogProps) {
  return (
    <ConfirmDialog
      isOpen={isOpen}
      onClose={onClose}
      onConfirm={onReload}
      title={DOCUMENT_CONFLICT_TITLE}
      message={DOCUMENT_CONFLICT_MESSAGE}
      confirmText="Refresh tab"
      cancelText="Cancel"
      variant="warning"
      isLoading={isReloading}
    />
  );
}
