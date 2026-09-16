// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

"use client";

import React, { useState, useEffect, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import DocumentHeader from "@/components/DocumentHeader";
import CorrectionsPanel, {
  CorrectionsPanelRef,
} from "@/components/CorrectionsPanel";
import ActionFooter from "@/components/ActionFooter";
import AuditSidebar from "@/components/AuditSidebar";
import KeyboardShortcutsModal from "@/components/KeyboardShortcutsModal";
import Toast from "@/components/Toast";
import ConfirmDialog from "@/components/ConfirmDialog";
import DocumentConflictDialog from "@/components/DocumentConflictDialog";
import ChangeHistory, {
  ChangeHistoryRef,
  ChangeDetail,
} from "@/components/ChangeHistory";
import { apiService, ApiDocumentMetadata, AssetDetail } from "@/services/api";
import {
  ChevronLeft,
  ChevronRight,
  ZoomIn,
  ZoomOut,
  RotateCw,
  Maximize,
  ImageIcon,
  X,
  ArrowLeft,
  ArrowRight,
  ChevronUp,
  Clock,
  FileSearch,
} from "lucide-react";
import { stripHtml } from "@/utils/stripHtml";
import { useAuth } from "@/contexts/AuthContext";
import { Lock } from "lucide-react";
import DocumentViewer from "@/components/DocumentViewer";
import AuthenticatedBlobImage from "@/components/AuthenticatedBlobImage";
import { mapApiDocumentsToRecords } from "@/utils/mapApiToRecord";
import { useFilters, FiltersProvider } from "@/contexts/FiltersContext";
import { hasPortalPublishDateMetadata } from "@/utils/portalPublishDate";
import { handleDocumentSaveError, DOCUMENT_CONFLICT_BANNER_MESSAGE, DOCUMENT_CONFLICT_BANNER_TITLE } from "@/utils/documentConflict";
import { useDocumentSaveQueue } from "@/utils/documentSaveQueue";

const CollectionReviewPageContent: React.FC = () => {
  const { repository, collectionName, recordid } = useParams<{
    repository: string;
    collectionName: string;
    recordid: string;
  }>();
  const navigate = useNavigate();
  const { user, permissions } = useAuth();
  const canEdit = permissions.documents.canEdit;
  const decodedRepository = repository ? decodeURIComponent(repository) : "";
  const decodedCollectionName = collectionName
    ? decodeURIComponent(collectionName)
    : "";
  const id = recordid;

  // Get filters and navigation state from context
  const { sortBy, currentPage: contextCurrentPage, currentRecordIndex: contextCurrentRecordIndex, setCurrentPage: setContextCurrentPage, setCurrentRecordIndex: setContextCurrentRecordIndex, ...filters } = useFilters();

  // API Data State
  const [documentData, setDocumentData] = useState<ApiDocumentMetadata | null>(
    null
  );
  const [isLoading, setIsLoading] = useState(true);
  const [apiError, setApiError] = useState<string | null>(null);

  const [currentAssetIndex, setCurrentAssetIndex] = useState(0);
  const [zoomLevel, setZoomLevel] = useState(100);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [imagePosition, setImagePosition] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
  const imageContainerRef = useRef<HTMLDivElement>(null);

  // Get current asset
  const currentAsset = documentData?.asset_details?.[currentAssetIndex];
  const totalAssets = documentData?.asset_details?.length || 0;

  // Get OCR text from current asset
  // ocr_text_original is the pristine original that never changes
  // ocr_text is the working copy that gets modified
  const currentOcrTextOriginal =
    currentAsset?.ocr_result?.ocr_text_original || "";
  const currentOcrText = currentAsset?.ocr_result?.ocr_text || "";

  // Store initial transcription in a ref for change detection
  const initialTranscriptionRef = useRef(currentOcrText);

  // State
  const [transcription, setTranscription] = useState(currentOcrTextOriginal); // Read-only original
  const [modifiedText, setModifiedText] = useState(currentOcrText); // Editable working copy
  const [visualDescriptionModified, setVisualDescriptionModified] = useState(""); // Editable visual description
  const [isModified, setIsModified] = useState(false);
  const [lastSaved, setLastSaved] = useState<Date>(new Date());
  const [showAuditSidebar, setShowAuditSidebar] = useState(false);
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [showChangeHistory, setShowChangeHistory] = useState(false);
  const [selectedChangeDetail, setSelectedChangeDetail] =
    useState<ChangeDetail | null>(null);
  const [toast, setToast] = useState<{
    type: "success" | "error" | "info";
    message: string;
  } | null>(null);
  const previousShowChangeHistoryRef = useRef<boolean>(false);
  const [oldValueContent, setOldValueContent] = useState<string | null>(null);
  const [newValueContent, setNewValueContent] = useState<string | null>(null);
  const [isLoadingContent, setIsLoadingContent] = useState(false);

  // Metadata state
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [creationDate, setCreationDate] = useState("");
  const [creator, setCreator] = useState("");
  const [recipient, setRecipient] = useState("");
  const [citation, setCitation] = useState("");
  const [collection, setCollection] = useState("");
  const [identifier, setIdentifier] = useState("");
  const [surrogateReel, setSurrogateReel] = useState("");
  const [resourceType, setResourceType] = useState("");
  const [period, setPeriod] = useState("");
  const [metadataRepository, setMetadataRepository] = useState("");
  const [rights, setRights] = useState("");
  const [productionMethod, setProductionMethod] = useState("");
  const [language, setLanguage] = useState("");
  const [severeDeviation, setSevereDeviation] = useState(false);
  const [deviationNotes, setDeviationNotes] = useState("");
  const [archivistNotes, setArchivistNotes] = useState("");
  const [markComplete, setMarkComplete] = useState(false);

  // Track original values for change detection
  const originalValuesRef = useRef<{ [key: string]: string }>({});
  const etagRef = useRef<string | null>(null);
  const runDocumentSave = useDocumentSaveQueue();
  const changeHistoryRef = useRef<ChangeHistoryRef>(null);
  const correctionsPanelRef = useRef<CorrectionsPanelRef>(null);
  const lastFetchedAssetRef = useRef<{
    documentId: string;
    assetId: string;
    index: number;
  } | null>(null);
  const currentDocumentIdRef = useRef<string | null>(null);
  const [isPublishing, setIsPublishing] = useState(false); //new state for publishing
  const [showPublishConfirm, setShowPublishConfirm] = useState(false); // confirm dialog state
  const [isPublishingInProgress, setIsPublishingInProgress] = useState(false); // loading state for publishing

  // Dirty fields tracking for improved save UX
  const [dirtyFields, setDirtyFields] = useState<Set<string>>(new Set());
  const [isSaving, setIsSaving] = useState(false);
  const [showConflictDialog, setShowConflictDialog] = useState(false);
  const [documentStale, setDocumentStale] = useState(false);
  const [isReloadingAfterConflict, setIsReloadingAfterConflict] = useState(false);

  const onSaveError = (error: unknown, fallbackMessage?: string) => {
    console.error("Save failed:", error);
    handleDocumentSaveError(error, {
      setConflictDialogOpen: (open) => {
        setShowConflictDialog(open);
        if (open) setDocumentStale(true);
      },
      setToast,
      fallbackMessage,
    });
  };

  const handleReloadAfterConflict = () => {
    setIsReloadingAfterConflict(true);
    window.location.reload();
  };

  // Field mapping for dynamic identifier display
  const [fieldMapping, setFieldMapping] = useState<{
    identifier_field: string;
    display_name?: string;
  } | null>(null);

  // Collection navigation state - using pagination instead of fetching all records
  const [currentPageRecords, setCurrentPageRecords] = useState<string[]>([]); // Record IDs for current page only
  const [recordAssetCounts, setRecordAssetCounts] = useState<Map<string, number>>(new Map()); // Map of record ID to asset_count
  const [currentRecordIndex, setCurrentRecordIndex] = useState<number>(contextCurrentRecordIndex); // Index within current page
  const [currentPage, setCurrentPage] = useState<number>(contextCurrentPage); // Use context value
  const [pageSize] = useState<number>(20); // Records per page
  const [totalRecords, setTotalRecords] = useState<number>(0); // Total records count
  const [isLoadingNavigation, setIsLoadingNavigation] = useState(false);
  
  // Sync local state with context when context changes
  useEffect(() => {
    if (contextCurrentPage !== currentPage) {
      setCurrentPage(contextCurrentPage);
    }
  }, [contextCurrentPage]);

  useEffect(() => {
    if (contextCurrentRecordIndex !== currentRecordIndex) {
      setCurrentRecordIndex(contextCurrentRecordIndex);
    }
  }, [contextCurrentRecordIndex]);

  // Cache key for current page records
  const pageCacheKeyRef = useRef<string>("");
  const lastFetchParamsRef = useRef<string>("");

  // Fetch field mapping for dynamic identifier display
  useEffect(() => {
    const fetchFieldMapping = async () => {
      if (!decodedRepository) {
        setFieldMapping(null);
        return;
      }
      
      try {
        const response = await apiService.resolveFieldMapping(decodedRepository, decodedCollectionName);
        setFieldMapping(response.mapping);
      } catch (error) {
        console.error('Failed to fetch field mapping:', error);
        // Default to Source Record ID if fetch fails
        setFieldMapping({ identifier_field: 'Source Record ID', display_name: 'Source Record ID' });
      }
    };
    fetchFieldMapping();
  }, [decodedRepository, decodedCollectionName]);

  // Fetch ONLY current page records for navigation
  useEffect(() => {
    const fetchCurrentPageRecords = async () => {
      if (!decodedRepository || !decodedCollectionName || !id) return;

      // Build sort params matching CollectionsItems.tsx logic
      const orderBy = sortBy === 'Newest first' ? 'created_at' : sortBy === 'Oldest first' ? 'created_at' : 'title';
      const descending = sortBy === 'Newest first' ? true : sortBy === 'Title Z-A' ? true : false;
      
      const params: any = {
        repository: decodedRepository,
        collection: decodedCollectionName,
        order_by: orderBy,
        descending: descending,
        page_number: currentPage,
        page_size: pageSize,
      };

      // Apply filters from context
      if (filters.searchQuery) {
        params.title_contains = filters.searchQuery;
      }
      if (filters.statusFilter) {
        params.status = filters.statusFilter;
      }
      if (filters.typeFilter) {
        params.resource_type = filters.typeFilter;
      }
      if (filters.confidenceFilter) {
        switch (filters.confidenceFilter) {
          case "high":
            params.min_confidence = 0.9;
            params.max_confidence = 1.0;
            break;
          case "medium":
            params.min_confidence = 0.7;
            params.max_confidence = 0.89;
            break;
          case "low":
            params.min_confidence = 0.5;
            params.max_confidence = 0.69;
            break;
          case "very-low":
            params.min_confidence = 0.0;
            params.max_confidence = 0.49;
            break;
        }
      }

      // Check if we've already fetched this exact page
      const paramsKey = JSON.stringify(params);
      if (lastFetchParamsRef.current === paramsKey && currentPageRecords.length > 0) {
        // Already have this page, just find the index
        const index = currentPageRecords.indexOf(id);
        if (index >= 0) {
          setCurrentRecordIndex(index);
        }
        return;
      }

      setIsLoadingNavigation(true);
      try {
        // Use getDocuments with pagination 
        const response = await apiService.getDocuments(params);
        
        // Extract record IDs from the paginated response
        const recordIds = response.documents.map(doc => doc.id);
        setCurrentPageRecords(recordIds);
        setTotalRecords(response.count || 0);
        
        // Store asset_count for each record
        const assetCountMap = new Map<string, number>();
        response.documents.forEach(doc => {
          assetCountMap.set(doc.id, doc.asset_count ?? 0);
        });
        setRecordAssetCounts(assetCountMap);
        
        lastFetchParamsRef.current = paramsKey;

        // Find current record index in this page
        const index = recordIds.indexOf(id);
        if (index >= 0) {
          setCurrentRecordIndex(index);
          setContextCurrentRecordIndex(index);
        } else {
          // Record not in current page - try to find it by searching nearby pages
          // This is a fallback - ideally the page should be known from URL or context
          setCurrentRecordIndex(-1);
          setContextCurrentRecordIndex(-1);
          // For now, navigation will be limited until we find the correct page
        }
      } catch (error) {
        console.error("Failed to fetch page records:", error);
        setCurrentPageRecords([]);
        setCurrentRecordIndex(-1);
        setContextCurrentRecordIndex(-1);
      } finally {
        setIsLoadingNavigation(false);
      }
    };

    fetchCurrentPageRecords();
  }, [
    decodedRepository,
    decodedCollectionName,
    currentPage,
    pageSize,
    filters.searchQuery,
    filters.statusFilter,
    filters.typeFilter,
    filters.confidenceFilter,
    sortBy,
    id, // Include id to refetch when record changes
  ]);

  // When record ID changes, try to find it in current page, or estimate page number
  useEffect(() => {
    if (!id || currentPageRecords.length === 0) return;

    const index = currentPageRecords.indexOf(id);
    if (index >= 0) {
      setCurrentRecordIndex(index);
      setContextCurrentRecordIndex(index);
    } else {
      // Record not in current page - we could search, but for performance,
      // just disable navigation until user navigates to a record in the current page
      setCurrentRecordIndex(-1);
      setContextCurrentRecordIndex(-1);
    }
  }, [id, currentPageRecords, setContextCurrentRecordIndex]);

  // Helper function to check if a record has assets
  const hasAssets = (recordId: string): boolean => {
    const assetCount = recordAssetCounts.get(recordId);
    return assetCount !== undefined && assetCount !== null && assetCount > 0;
  };

  // Navigation handlers - work with paginated records
  const handlePreviousRecord = () => {
    let skippedCount = 0;
    
    // First, try to find a valid previous record in the current page
    if (currentRecordIndex > 0 && currentPageRecords.length > 0) {
      // Search backwards from current index for a record with assets
      for (let i = currentRecordIndex - 1; i >= 0; i--) {
        const prevId = currentPageRecords[i];
        if (prevId && hasAssets(prevId)) {
          if (skippedCount > 0) {
            setToast({
              type: 'info',
              message: `Skipped ${skippedCount} record(s) with no assets`
            });
          }
          const newIndex = i;
          setCurrentRecordIndex(newIndex);
          setContextCurrentRecordIndex(newIndex);
          navigate(
            `/repositories/${encodeURIComponent(
              decodedRepository
            )}/collections/${encodeURIComponent(
              decodedCollectionName
            )}/review/${prevId}`
          );
          return;
        } else {
          skippedCount++;
        }
      }
    }
    
    // If no valid record found in current page, try previous page
    if (currentPage > 1) {
      if (skippedCount > 0) {
        setToast({
          type: 'info',
          message: `Skipped ${skippedCount} record(s) with no assets, moving to previous page`
        });
      }
      // Need to go to previous page - update page state, will trigger fetch
      const newPage = currentPage - 1;
      setCurrentPage(newPage);
      setContextCurrentPage(newPage);
      // Index will be set to last item of previous page after fetch
    } else if (skippedCount > 0) {
      setToast({
        type: 'info',
        message: `Skipped ${skippedCount} record(s) with no assets. No more records available.`
      });
    }
  };

  const handleNextRecord = () => {
    let skippedCount = 0;
    
    // First, try to find a valid next record in the current page
    if (
      currentRecordIndex >= 0 &&
      currentRecordIndex < currentPageRecords.length - 1
    ) {
      // Search forwards from current index for a record with assets
      for (let i = currentRecordIndex + 1; i < currentPageRecords.length; i++) {
        const nextId = currentPageRecords[i];
        if (nextId && hasAssets(nextId)) {
          if (skippedCount > 0) {
            setToast({
              type: 'info',
              message: `Skipped ${skippedCount} record(s) with no assets`
            });
          }
          const newIndex = i;
          setCurrentRecordIndex(newIndex);
          setContextCurrentRecordIndex(newIndex);
          navigate(
            `/repositories/${encodeURIComponent(
              decodedRepository
            )}/collections/${encodeURIComponent(
              decodedCollectionName
            )}/review/${nextId}`
          );
          return;
        } else {
          skippedCount++;
        }
      }
    }
    
    // If no valid record found in current page, try next page
    const totalPages = Math.ceil(totalRecords / pageSize);
    if (currentPage < totalPages) {
      if (skippedCount > 0) {
        setToast({
          type: 'info',
          message: `Skipped ${skippedCount} record(s) with no assets, moving to next page`
        });
      }
      const newPage = currentPage + 1;
      setCurrentPage(newPage);
      setContextCurrentPage(newPage);
      // Index will be set to 0 after fetch
    } else if (skippedCount > 0) {
      setToast({
        type: 'info',
        message: `Skipped ${skippedCount} record(s) with no assets. No more records available.`
      });
    }
  };

  // Calculate global position for display
  const globalRecordPosition = currentRecordIndex >= 0 && currentPage > 0
    ? (currentPage - 1) * pageSize + currentRecordIndex + 1
    : -1;

  // Fetch document data from API - CRITICAL: Load immediately, don't wait for navigation data
  useEffect(() => {
    const fetchDocument = async () => {
      if (!id) {
        setApiError("No document ID provided");
        setIsLoading(false);
        return;
      }

      // IMPORTANT: Document loads immediately, navigation data loads in parallel
      // This ensures document displays in seconds, not 1.2 minutes

      // Reset all asset-related state when document changes
      // IMPORTANT: Clear lastFetchedAssetRef FIRST to prevent stale asset fetches
      lastFetchedAssetRef.current = null;
      currentDocumentIdRef.current = id; // Track current document ID
      etagRef.current = null;
      // Reset asset index BEFORE clearing documentData to prevent useEffect from running with stale data
      setCurrentAssetIndex(0);
      setZoomLevel(100);
      setImagePosition({ x: 0, y: 0 });
      setIsDragging(false);
      // Clear documentData LAST to ensure other state is reset first
      setDocumentData(null); // Clear old document data to prevent stale asset references

      try {
        setIsLoading(true);
        setApiError(null);
        setTitle("");
        setCreationDate("");
        setCreator("");
        setResourceType("");
        setPeriod("");
        setMetadataRepository("");
        setRights("");
        setProductionMethod("");
        setDeviationNotes("");
        setCollection("");
        setRecipient("");
        setCitation("");
        setDescription("");
        setIdentifier("");

        const data = await apiService.getDocumentById(id);

        // Only set document data if we're still on the same document
        if (currentDocumentIdRef.current === id) {
          setDocumentData(data);

          // Store etag for optimistic concurrency
          etagRef.current = (data as any)._etag || null;

          // Reset asset index to 0 for new document
          setCurrentAssetIndex(0);

          // Set initial transcription from first asset
          if (data.asset_details && data.asset_details.length > 0) {
            const firstOcrTextOriginal =
              data.asset_details[0]?.ocr_result?.ocr_text_original || "";
            const firstOcrText =
              data.asset_details[0]?.ocr_result?.ocr_text || "";
            setTranscription(firstOcrTextOriginal); // Read-only original
            setModifiedText(firstOcrText); // Editable working copy
            initialTranscriptionRef.current = firstOcrText;
          } else {
            // Reset transcription if no assets
            setTranscription("");
            setModifiedText("");
            initialTranscriptionRef.current = "";
          }

          // Set initial visual description
          setVisualDescriptionModified(data.visual_detailed_description_flexible || "");

          // Populate metadata fields from API response
          if (data.metadata) {
            const titleVal = data.metadata.Title || "";
            const descVal = stripHtml(data.metadata.Description || "");
            const creationDateVal = data.metadata["Creation Date"] || "";
            const creatorVal = data.metadata.Creator || "";
            const recipientVal = data.metadata.Recipient || "";
            const citationVal = data.metadata.Citation || "";
            const collectionVal = data.metadata.Collection || "";
            // Use field mapping to get identifier value, fallback to API default if no mapping
            const identifierFieldName = fieldMapping?.identifier_field || "Source Record ID";
            const identifierVal = data.metadata[identifierFieldName] || "";
            const resourceTypeVal = data.metadata["Resource Type"] || "";
            const periodVal = data.metadata.Period || "";
            const repositoryVal = data.metadata.Repository || "";
            const rightsVal =
              data.metadata["Copyright Status"] ||
              data.metadata["Image Rights"] ||
              "";
            const productionMethodVal =
              data.metadata["Production Method"] || "";
            const languageVal = data.metadata.Language || "";

            setTitle(titleVal);
            setDescription(descVal);
            setCreationDate(creationDateVal);
            setCreator(creatorVal);
            setRecipient(recipientVal);
            setCitation(citationVal);
            setCollection(collectionVal);
            setIdentifier(identifierVal);
            setResourceType(resourceTypeVal);
            setPeriod(periodVal);
            setMetadataRepository(repositoryVal);
            setRights(rightsVal);
            setProductionMethod(productionMethodVal);
            setLanguage(languageVal);

            // Store original values for change detection
            originalValuesRef.current = {
              title: titleVal,
              description: descVal,
              creationDate: creationDateVal,
              creator: creatorVal,
              recipient: recipientVal,
              citation: citationVal,
              resourceType: resourceTypeVal,
              period: periodVal,
              rights: rightsVal,
              productionMethod: productionMethodVal,
              language: languageVal,
            };
          }

          // Load archivist fields
          setArchivistNotes(data.archivist_notes || "");
          // setMarkComplete(data.archivist_status === 'Reviewed' || data.archivist_status === 'reviewed')
          const archivistStatus = (data.archivist_status || "").toLowerCase();
          setMarkComplete(
            archivistStatus === "reviewed" || archivistStatus === "publishing"
          );
          setIsPublishing(archivistStatus === "publishing");
        }
      } catch (error) {
        console.error("Failed to fetch document:", error);
        setApiError("Failed to load document from API");
        setToast({
          type: "error",
          message: "Could not load document. Please try again.",
        });
      } finally {
        setIsLoading(false);
      }
    };

    fetchDocument();
  }, [id]);

  // Update identifier when field mapping changes (in case it loads after document)
  useEffect(() => {
    if (!documentData?.metadata) return;
    
    // Use field mapping if available, otherwise use API default
    const identifierFieldName = fieldMapping?.identifier_field || "Source Record ID";
    const identifierVal = documentData.metadata[identifierFieldName] || "";
    
    setIdentifier(identifierVal);
  }, [fieldMapping, documentData]);

  // Fetch asset details when currentAssetIndex changes or when documentData is first loaded
  useEffect(() => {
    const fetchAssetDetails = async () => {
      // Don't fetch if document is loading or if we don't have the right document
      if (!id) return;
      if (currentDocumentIdRef.current !== id) {
        console.log("Document ID mismatch (ref), skipping asset fetch");
        return;
      }
      if (!documentData) {
        console.log("No document data, skipping asset fetch");
        return;
      }
      // CRITICAL: Verify documentData.id matches current id to prevent using stale data
      if (documentData.id !== id) {
        console.log(
          "Document ID mismatch (documentData.id !== id), skipping asset fetch",
          {
            documentDataId: documentData.id,
            currentId: id,
          }
        );
        return;
      }
      // Ensure documentData belongs to current document (check if it has the same structure)
      if (!documentData.asset_details || currentAssetIndex < 0) return;
      if (currentAssetIndex >= documentData.asset_details.length) {
        console.log("Asset index out of bounds, skipping asset fetch");
        return;
      }

      const asset = documentData.asset_details[currentAssetIndex];
      if (!asset) {
        console.log("No asset at index, skipping asset fetch");
        return;
      }

      // Get asset_id (can be stored as 'asset_id' or 'id' depending on ingestion)
      const assetId = asset.asset_id || asset.id;

      if (!assetId) {
        // No asset_id available, skip fetching
        console.log("No asset_id, skipping asset fetch");
        return;
      }

      // Verify we're still on the correct document before making API call
      if (currentDocumentIdRef.current !== id) {
        console.log("Document changed before asset fetch, aborting");
        return;
      }

      // Check if we've already fetched this asset to avoid duplicate API calls
      const lastFetched = lastFetchedAssetRef.current;
      if (
        lastFetched &&
        lastFetched.documentId === id &&
        lastFetched.assetId === assetId &&
        lastFetched.index === currentAssetIndex
      ) {
        // Already fetched this asset, skip
        return;
      }

      try {
        // Final check before API call
        if (currentDocumentIdRef.current !== id) {
          console.log("Document changed right before API call, aborting");
          return;
        }

        // Fetch the asset details from the API
        const assetDetails = await apiService.getAssetById(id, assetId);

        // Double-check that we're still on the same document before updating
        if (currentDocumentIdRef.current !== id) {
          console.log("Document changed during asset fetch, skipping update");
          return;
        }

        // Update the last fetched reference
        lastFetchedAssetRef.current = {
          documentId: id,
          assetId,
          index: currentAssetIndex,
        };

        // Update the asset in documentData with the fetched details
        setDocumentData((prevData) => {
          // Ensure we're still working with the correct document
          if (
            !prevData ||
            currentDocumentIdRef.current !== id ||
            !prevData.asset_details
          )
            return prevData;

          const updatedAssetDetails = [...prevData.asset_details];
          if (currentAssetIndex < updatedAssetDetails.length) {
            updatedAssetDetails[currentAssetIndex] = assetDetails;
          }

          return {
            ...prevData,
            asset_details: updatedAssetDetails,
          };
        });
      } catch (error) {
        console.error("Failed to fetch asset details:", error);
        // Only show error if we're still on the same document
        if (currentDocumentIdRef.current === id) {
          setToast({
            type: "error",
            message: `Failed to load asset details: ${
              error instanceof Error ? error.message : "Unknown error"
            }`,
          });
        }
      }
    };

    // Only fetch if we have valid document data and it matches current document
    if (documentData && currentDocumentIdRef.current === id) {
      fetchAssetDetails();
    }
  }, [currentAssetIndex, id, documentData]);

  // Update transcription when asset changes
  useEffect(() => {
    if (currentAsset?.ocr_result) {
      const ocrTextOriginal = currentAsset.ocr_result.ocr_text_original || "";
      const ocrText = currentAsset.ocr_result.ocr_text || "";
      setTranscription(ocrTextOriginal); // Read-only original
      setModifiedText(ocrText); // Editable working copy
      initialTranscriptionRef.current = ocrText;
      setIsModified(false);
    }
    // Reset zoom and position when changing assets
    setZoomLevel(100);
    setImagePosition({ x: 0, y: 0 });
  }, [currentAssetIndex, currentAsset]);

  // Navigation handlers
  const handlePreviousAsset = () => {
    if (currentAssetIndex > 0) {
      setCurrentAssetIndex((prev) => prev - 1);
    }
  };

  const handleNextAsset = () => {
    if (currentAssetIndex < totalAssets - 1) {
      setCurrentAssetIndex((prev) => prev + 1);
    }
  };

  const handleAssetSelect = (index: number) => {
    setCurrentAssetIndex(index);
  };

  // Helper function to check if record is published
  const isRecordPublished = (): boolean => {
    if (!documentData) return false;
    const archivistStatus = (documentData.archivist_status || "").toLowerCase();
    return archivistStatus === "published";
  };

  // Handle metadata field blur - send changes to API
  const handleFieldBlur = async (fieldName: string, currentValue: string) => {
    if (!id) return;

    const originalValue = originalValuesRef.current[fieldName] || "";

    // Only send update if value actually changed
    if (originalValue.trim() === currentValue.trim()) {
      // Clear from dirty fields even if no change
      setDirtyFields((prev) => {
        const next = new Set(prev);
        next.delete(fieldName);
        return next;
      });
      return;
    }

    try {
      await runDocumentSave(async () => {
      // Check if record is published - if so, change status to pending
      const wasPublished = isRecordPublished();
      
      // Prepare update request with only the changed field
      // user_id and user_display are now injected by the backend from authentication
      const updateRequest: any = {
        metadata: {
          [fieldName]: currentValue,
        },
      };

      // If record was published, change status to pending
      if (wasPublished) {
        updateRequest.archivist_status = "pending";
      }

      // Send update to API
      const updatedDoc = await apiService.updateDocumentMetadata(
        id,
        updateRequest,
        etagRef.current || undefined
      );

      // Update etag for next update
      etagRef.current = (updatedDoc as any)._etag || null;

      // Update original value
      originalValuesRef.current[fieldName] = currentValue;

      // Update local state if status changed
      if (wasPublished) {
        setDocumentData((prevData) => {
          if (!prevData) return prevData;
          return {
            ...prevData,
            archivist_status: "pending",
          };
        });
        setMarkComplete(false);
        setIsPublishing(false);
      }

      // Clear from dirty fields after successful save
      setDirtyFields((prev) => {
        const next = new Set(prev);
        next.delete(fieldName);
        return next;
      });

      // Silent success - no toast (modern UX pattern)

      // Refresh audit history
      if (changeHistoryRef.current) {
        console.log("Refreshing audit history after field update...");
        await changeHistoryRef.current.refetch();
      } else {
        console.warn("Change history ref is not available");
      }

      // Refresh last saved timestamp in CorrectionsPanel
      if (correctionsPanelRef.current) {
        console.log("Refreshing last saved timestamp...");
        await correctionsPanelRef.current.refreshLastSaved();
      } else {
        console.warn("Corrections panel ref is not available");
      }
      });
    } catch (error) {
      onSaveError(error, `Failed to update ${fieldName}`);
      // Keep field in dirty set on error so user can retry
      throw error;
    }
  };

  // Handle archivist notes blur - send changes to API
  const handleArchivistNotesBlur = async (notes: string) => {
    if (!id) return;

    try {
      await runDocumentSave(async () => {
      // Check if record is published - if so, change status to pending
      const wasPublished = isRecordPublished();
      
      // user_id and user_display are now injected by the backend from authentication
      const updateRequest: any = {
        archivist_notes: notes,
      };

      // If record was published, change status to pending
      if (wasPublished) {
        updateRequest.archivist_status = "pending";
      }

      const updatedDoc = await apiService.updateDocumentMetadata(
        id,
        updateRequest,
        etagRef.current || undefined
      );

      etagRef.current = (updatedDoc as any)._etag || null;

      // Update local state if status changed
      if (wasPublished) {
        setDocumentData((prevData) => {
          if (!prevData) return prevData;
          return {
            ...prevData,
            archivist_status: "pending",
          };
        });
        setMarkComplete(false);
        setIsPublishing(false);
      }

      // Clear from dirty fields after successful save
      setDirtyFields((prev) => {
        const next = new Set(prev);
        next.delete("archivistNotes");
        return next;
      });

      // Silent success - no toast (modern UX pattern)

      if (changeHistoryRef.current) {
        await changeHistoryRef.current.refetch();
      }

      // Refresh last saved timestamp in CorrectionsPanel
      if (correctionsPanelRef.current) {
        await correctionsPanelRef.current.refreshLastSaved();
      }
      });
    } catch (error) {
      onSaveError(error, "Failed to update archivist notes");
      // Keep field in dirty set on error
      throw error;
    }
  };

  // Helper function to get current field value
  const getFieldValue = (fieldName: string): string => {
    const fieldMap: Record<string, string> = {
      title,
      description,
      creationDate,
      creator,
      recipient,
      citation,
      resourceType,
      period,
      rights,
      productionMethod,
      language,
      archivistNotes,
      modifiedText,
    };
    return fieldMap[fieldName] || "";
  };

  // Save all dirty fields at once
  const handleSaveAll = async () => {
    if (dirtyFields.size === 0 || !id) return;

    setIsSaving(true);

    try {
      for (const fieldName of Array.from(dirtyFields)) {
        const currentValue = getFieldValue(fieldName);

        if (fieldName === "modifiedText") {
          await handleOcrBlur(currentValue);
        } else if (fieldName === "archivistNotes") {
          await handleArchivistNotesBlur(currentValue);
        } else {
          await handleFieldBlur(fieldName, currentValue);
        }
      }

      setDirtyFields(new Set());

      // Silent success - no toast (modern UX pattern)
    } catch (error) {
      throw error;
    } finally {
      setIsSaving(false);
    }
  };

  const hasPortalPublishDate = (): boolean =>
    hasPortalPublishDateMetadata(
      documentData?.metadata?.["Date Published to Portal"]
    );

  // Handle approval for publishing - show confirm dialog
  const handleApproveForPublishingClick = () => {
    if (!hasPortalPublishDate()) {
      setToast({
        type: "error",
        message: "Cannot publish since there is no TRC object",
      });
      return;
    }
    setShowPublishConfirm(true);
  };

  // Actual publishing logic after confirmation
  const handleConfirmPublishing = async () => {
    if (!id) {
      setShowPublishConfirm(false);
      return;
    }

    if (!hasPortalPublishDate()) {
      setToast({
        type: "error",
        message: "Cannot publish since there is no TRC object",
      });
      setShowPublishConfirm(false);
      return;
    }

    setIsPublishingInProgress(true);
    try {
      const updateRequest = {
        archivist_status: "publishing",
      };

      const updatedDoc = await apiService.updateDocumentMetadata(
        id,
        updateRequest,
        etagRef.current || undefined
      );

      etagRef.current = (updatedDoc as any)._etag || null;

      // Update local state
      setIsPublishing(true);
      setDocumentData((prevData) => {
        if (!prevData) return prevData;
        return {
          ...prevData,
          archivist_status: "publishing",
        };
      });

      // Minimal feedback for important action
      setToast({
        type: "info",
        message: "Document approved for publishing",
      });

      if (changeHistoryRef.current) {
        await changeHistoryRef.current.refetch();
      }

      if (correctionsPanelRef.current) {
        await correctionsPanelRef.current.refreshLastSaved();
      }
    } catch (error) {
      onSaveError(error, "Failed to approve document for publishing");
    } finally {
      setIsPublishingInProgress(false);
      setShowPublishConfirm(false);
    }
  };

  // Handle undo publishing
  const handleUndoPublishing = async () => {
    if (!id) return;

    try {
      const updateRequest = {
        archivist_status: "reviewed",
      };

      const updatedDoc = await apiService.updateDocumentMetadata(
        id,
        updateRequest,
        etagRef.current || undefined
      );

      etagRef.current = (updatedDoc as any)._etag || null;

      // Update local state
      setIsPublishing(false);
      if (documentData) {
        setDocumentData({
          ...documentData,
          archivist_status: "reviewed",
        });
      }

      // Minimal feedback for important action
      setToast({
        type: "info",
        message: "Publishing approval undone",
      });

      if (changeHistoryRef.current) {
        await changeHistoryRef.current.refetch();
      }

      if (correctionsPanelRef.current) {
        await correctionsPanelRef.current.refreshLastSaved();
      }
    } catch (error) {
      onSaveError(error, "Failed to undo publishing approval");
    }
  };

  // Handle mark as complete change - send changes to API
  const handleMarkCompleteChange = async (isComplete: boolean) => {
    if (!id) return;

    setMarkComplete(isComplete);
    // setIsModified(true)

    try {
      // user_id and user_display are now injected by the backend from authentication
      // validated_by/published_by are set by the backend based on status
      const updateRequest = {
        archivist_status: isComplete ? "reviewed" : "pending",
      };

      const updatedDoc = await apiService.updateDocumentMetadata(
        id,
        updateRequest,
        etagRef.current || undefined
      );

      etagRef.current = (updatedDoc as any)._etag || null;
      if (documentData) {
        setDocumentData({
          ...documentData,
          archivist_status: isComplete ? "reviewed" : "pending",
        });
      }
      // Silent success for status change - user can see the status badge
      // No toast needed

      if (changeHistoryRef.current) {
        await changeHistoryRef.current.refetch();
      }

      // Refresh last saved timestamp in CorrectionsPanel
      if (correctionsPanelRef.current) {
        await correctionsPanelRef.current.refreshLastSaved();
      }
    } catch (error) {
      onSaveError(error, "Failed to update completion status");
      // Revert the checkbox state
      setMarkComplete(!isComplete);
    }
  };

  // Handle OCR text blur - send changes to API
  const handleVisualDescriptionBlur = async (visualDesc: string) => {
    if (!id || !documentData) return;

    const currentVisualDesc = documentData.visual_detailed_description_flexible || "";
    if (visualDesc === currentVisualDesc) {
      // No change, remove from dirty fields
      setDirtyFields((prev) => {
        const newSet = new Set(prev);
        newSet.delete("visualDescriptionModified");
        return newSet;
      });
      return;
    }

    try {
      setIsSaving(true);
      await runDocumentSave(async () => {
      const updateRequest = {
        visual_detailed_description_flexible: visualDesc,
        correlation_id: `visual-desc-update-${Date.now()}`,
      };

      const updatedDoc = await apiService.updateDocumentMetadata(
        id,
        updateRequest,
        etagRef.current || undefined
      );

      if (updatedDoc) {
        const freshDoc = await apiService.getDocumentById(id)
        lastFetchedAssetRef.current = null
        setDocumentData(freshDoc)
        etagRef.current = (freshDoc as any)._etag || (updatedDoc as any)._etag || null
        setDirtyFields((prev) => {
          const newSet = new Set(prev);
          newSet.delete("visualDescriptionModified");
          return newSet;
        });
        setToast({
          type: "success",
          message: "Visual description saved successfully",
        });
        if (correctionsPanelRef.current) {
          correctionsPanelRef.current.refreshLastSaved();
        }
      }
      })
    } catch (error: unknown) {
      onSaveError(error, "Failed to save visual description");
      throw error;
    } finally {
      setIsSaving(false);
    }
  };

  const handleOcrBlur = async (ocrText: string) => {
    if (!id || currentAssetIndex === undefined) return;

    const originalOcr = initialTranscriptionRef.current || "";

    if (originalOcr.trim() === ocrText.trim()) {
      setDirtyFields((prev) => {
        const next = new Set(prev);
        next.delete("modifiedText");
        return next;
      });
      return;
    }

    try {
      await runDocumentSave(async () => {
      const wasPublished = isRecordPublished();
      const ocrRequest: any = {
        asset_index: currentAssetIndex,
        ocr_text: ocrText,
      };

      const asset = documentData?.asset_details?.[currentAssetIndex];
      const assetKey = asset?.asset_id || asset?.id || "";

      const updatedDoc = await apiService.updateOcrText(
        id,
        assetKey,
        ocrRequest,
        etagRef.current || undefined
      );

      const freshDoc = await apiService.getDocumentById(id);
      etagRef.current = freshDoc._etag ?? updatedDoc._etag ?? null;

      const savedOcrText =
        freshDoc.asset_details?.[currentAssetIndex]?.ocr_result?.ocr_text
        ?? updatedDoc.asset_details?.[currentAssetIndex]?.ocr_result?.ocr_text
        ?? ocrText;
      setModifiedText(savedOcrText);
      initialTranscriptionRef.current = savedOcrText;
      setDocumentData((prevData) => {
        if (!prevData?.asset_details || !freshDoc.asset_details?.[currentAssetIndex]) {
          return freshDoc;
        }
        const nextAssets = [...prevData.asset_details];
        nextAssets[currentAssetIndex] = freshDoc.asset_details[currentAssetIndex];
        return { ...prevData, ...freshDoc, asset_details: nextAssets };
      });
      lastFetchedAssetRef.current = null;

      if (wasPublished) {
        const statusUpdatedDoc = await apiService.updateDocumentMetadata(
          id,
          { archivist_status: "pending" },
          etagRef.current || undefined
        );
        etagRef.current = statusUpdatedDoc._etag ?? null;
        setDocumentData((prevData) => {
          if (!prevData) return prevData;
          return { ...prevData, archivist_status: "pending" };
        });
        setMarkComplete(false);
        setIsPublishing(false);
      }

      setDirtyFields((prev) => {
        const next = new Set(prev);
        next.delete("modifiedText");
        return next;
      });

      if (changeHistoryRef.current) {
        await changeHistoryRef.current.refetch();
      }
      if (correctionsPanelRef.current) {
        await correctionsPanelRef.current.refreshLastSaved();
      }
      });
    } catch (error) {
      onSaveError(error, "Failed to update OCR text");
      throw error;
    }
  };

  // Zoom handlers
  const handleZoomIn = () => {
    setZoomLevel((prev) => Math.min(prev + 25, 1000));
  };

  const handleZoomOut = () => {
    setZoomLevel((prev) => Math.max(prev - 25, 50));
  };

  // Reset zoom and position
  const handleResetZoom = () => {
    setZoomLevel(100);
    setImagePosition({ x: 0, y: 0 });
  };

  // Drag handlers for panning
  const handleMouseDown = (e: React.MouseEvent) => {
    if (zoomLevel > 100) {
      setIsDragging(true);
      setDragStart({
        x: e.clientX - imagePosition.x,
        y: e.clientY - imagePosition.y,
      });
      e.preventDefault();
    }
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (isDragging && zoomLevel > 100) {
      setImagePosition({
        x: e.clientX - dragStart.x,
        y: e.clientY - dragStart.y,
      });
    }
  };

  const handleMouseUp = () => {
    setIsDragging(false);
  };

  const handleMouseLeave = () => {
    setIsDragging(false);
  };

  // Fullscreen handlers
  const handleOpenFullscreen = () => {
    setIsFullscreen(true);
  };

  const handleCloseFullscreen = () => {
    setIsFullscreen(false);
  };

  // Handle ESC key to close fullscreen
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isFullscreen) {
        handleCloseFullscreen();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isFullscreen]);

  // Warn on unsaved changes before browser navigation (refresh, close, etc.)
  useEffect(() => {
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (dirtyFields.size > 0 || isSaving) {
        const message = `You have ${dirtyFields.size} unsaved ${
          dirtyFields.size === 1 ? "change" : "changes"
        }. Your changes will be lost.`;
        e.preventDefault();
        e.returnValue = message; // Required for Chrome
        return message; // Required for some browsers
      }
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [dirtyFields, isSaving]);

  // TODO: Implement navigation blocking for React Router
  // Note: useBlocker requires React Router v6.4+ data router (createBrowserRouter)
  // For now, we rely on beforeunload event for browser navigation protection
  // React Router in-app navigation will need to be handled differently or
  // the app needs to be upgraded to use createBrowserRouter

  // Hide/show change history panel when modal opens/closes
  useEffect(() => {
    if (selectedChangeDetail) {
      // Modal is opening - store current state and hide change history
      previousShowChangeHistoryRef.current = showChangeHistory;
      setShowChangeHistory(false);
    } else {
      // Modal is closed - restore previous state
      if (previousShowChangeHistoryRef.current) {
        setShowChangeHistory(true);
        previousShowChangeHistoryRef.current = false; // Reset after restoring
      }
    }
  }, [selectedChangeDetail]);

  // Fetch content from URLs when selectedChangeDetail changes
  useEffect(() => {
    const fetchUrlContent = async () => {
      if (!selectedChangeDetail) {
        setOldValueContent(null);
        setNewValueContent(null);
        return;
      }

      setIsLoadingContent(true);
      setOldValueContent(null);
      setNewValueContent(null);

      try {
        const loadAuditValue = async (value: string): Promise<string | null> => {
          if (!value || typeof value !== "string") {
            return null;
          }
          if (value.startsWith("https://") && apiService.isAzureBlobStorageUrl(value)) {
            try {
              return await apiService.fetchBlobText(value);
            } catch (err) {
              console.error("Failed to fetch audit blob content:", err);
              return `Error loading content: ${
                err instanceof Error ? err.message : "Unknown error"
              }`;
            }
          }
          if (value.startsWith("https://")) {
            try {
              const response = await fetch(value);
              if (response.ok) {
                return await response.text();
              }
              return `Failed to load: ${response.statusText}`;
            } catch (err) {
              console.error("Failed to fetch audit URL content:", err);
              return `Error loading content: ${
                err instanceof Error ? err.message : "Unknown error"
              }`;
            }
          }
          return value;
        };

        const oldContent = await loadAuditValue(selectedChangeDetail.oldValue);
        setOldValueContent(oldContent);

        const newContent = await loadAuditValue(selectedChangeDetail.newValue);
        setNewValueContent(newContent);
      } finally {
        setIsLoadingContent(false);
      }
    };

    fetchUrlContent();
  }, [selectedChangeDetail]);

  // Keyboard shortcut: Ctrl+S / Cmd+S to save
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "s") {
        e.preventDefault();
        if (dirtyFields.size > 0 && !isSaving) {
          handleSaveAll();
        }
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [dirtyFields, isSaving]);

  // Save function
  const handleSave = async (markComplete = false) => {
    try {
      await handleSaveAll();
      setIsModified(false);
      setLastSaved(new Date());
      setToast({
        type: "success",
        message: markComplete
          ? "Document Reviewed and saved"
          : "Changes saved successfully",
      });
    } catch {
      // Save handlers already show the conflict dialog or error toast.
    }
  };

  // Save & Complete
  const handleSaveAndComplete = async () => {
    if (!transcription.trim()) {
      setToast({
        type: "error",
        message: "Please fill transcription before completing",
      });
      return;
    }
    await handleSave(true);
  };

  return (
    <div className="flex flex-col bg-museum-50 relative">
      <DocumentHeader
        title={title}
        creationDate={creationDate}
        creator={creator}
        collection={collection}
        repository={metadataRepository}
        identifier={identifier}
        resourceType={resourceType}
        severeDeviation={severeDeviation}
        status={(() => {
          const archivistStatus = (documentData?.archivist_status || "").trim();
          const normalized = archivistStatus.toLowerCase();

          if (normalized === "published") {
            return "Published";
          } else if (normalized === "publishing") {
            return "Publishing";
          } else if (normalized === "reviewed") {
            return "Reviewed";
          } else if (normalized === "failed" || normalized === "error") {
            return "Failed";
          } else {
            return "Pending";
          }
        })()}
        datePublishedToPortal={documentData?.metadata?.['Date Published to Portal']}
        sourceRecordId={documentData?.metadata?.['Source Record ID']}
        onSave={() => handleSave(false)}
        onSaveAndComplete={handleSaveAndComplete}
        onPreviousRecord={handlePreviousRecord}
        onNextRecord={handleNextRecord}
        currentRecordIndex={globalRecordPosition >= 0 ? globalRecordPosition - 1 : -1}
        totalRecords={totalRecords}
        showNavigation={totalRecords > 0 && globalRecordPosition >= 0 && !isLoadingNavigation}
      />

      {/* Read-only notice for users without edit permission */}
      {!canEdit && (
        <div className="mx-auto max-w-8xl px-4 sm:px-6 lg:px-8 py-2">
          <div className="flex items-center gap-2 text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-4 py-3">
            <Lock className="w-4 h-4 flex-shrink-0" />
            <span>You have read-only access. Only Admin or Archivist users can edit, approve, and ingest documents.</span>
          </div>
        </div>
      )}

      {documentStale && (
        <div className="mx-auto max-w-8xl px-4 sm:px-6 lg:px-8 py-2">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 text-sm text-amber-900 bg-amber-50 border border-amber-300 rounded-lg px-4 py-3">
            <div>
              <p className="font-medium">{DOCUMENT_CONFLICT_BANNER_TITLE}</p>
              <p className="text-amber-800 mt-1">
                {DOCUMENT_CONFLICT_BANNER_MESSAGE}
              </p>
            </div>
            <button
              type="button"
              onClick={handleReloadAfterConflict}
              disabled={isReloadingAfterConflict}
              className="shrink-0 px-4 py-2 bg-amber-600 text-white rounded-lg hover:bg-amber-700 disabled:opacity-50 transition-colors"
            >
              {isReloadingAfterConflict ? "Refreshing…" : "Refresh tab"}
            </button>
          </div>
        </div>
      )}
      
      <div className="px-3">
        {/* Loading State */}
        {isLoading && (
          <div className="flex justify-center items-center py-12">
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-museum-600"></div>
            <span className="ml-4 text-museum-600">Loading document...</span>
          </div>
        )}

        {/* Error State */}
        {apiError && !isLoading && (
          <div className="mx-auto max-w-8xl px-4 sm:px-6 lg:px-8 py-4">
            <div className="bg-red-50 border border-red-200 rounded-lg p-4">
              <p className="text-red-800">{apiError}</p>
            </div>
          </div>
        )}

        {/* Content - only show when not loading */}
        {!isLoading && (
          <>
            {/* Top: Source + AI Transcription */}
            <div className="flex flex-col flex-1 overflow-hidden">
              <div className="flex h-[calc(100vh-20.5rem)] overflow-hidden border-b border-museum-200">
                {/* Image Navigation Controls - Left Sidebar */}

                <div className="w-1/7 border-r border-museum-200 p-3 bg-white overflow-y-auto ocr-scrollbar">
                  {totalAssets > 0 ? (
                    <>
                      {/* Thumbnail Strip - Vertical */}
                      <div className="flex flex-col space-y-2">
                        {documentData?.asset_details?.map((asset, index) => (
                          <button
                            key={index}
                            className={`relative w-full rounded overflow-hidden border-2 transition-all ${
                              currentAssetIndex === index
                                ? "border-museum-800 ring-2 ring-museum-800 ring-offset-1"
                                : "border-museum-300 hover:border-museum-500"
                            }`}
                            onClick={() => handleAssetSelect(index)}
                            title={
                              asset.metadata?.["File Name"] ||
                              `Asset ${index + 1}`
                            }
                          >
                            <div className="aspect-(11/12) bg-museum-50 flex items-center justify-center">
                              {asset.blob_thumbnail_url ? (
                                <AuthenticatedBlobImage
                                  src={asset.blob_thumbnail_url}
                                  alt={`Thumbnail ${index + 1}`}
                                  className="w-full h-full object-contain"
                                />
                              ) : (
                                <span className="text-xs text-museum-400">
                                  No Asset
                                </span>
                              )}
                            </div>
                            <div
                              className={`absolute bottom-0 left-0 right-0 px-1 py-0.5 text-xs font-medium text-center ${
                                currentAssetIndex === index
                                  ? "bg-museum-800 text-white"
                                  : "bg-museum-100 text-museum-600"
                              }`}
                            >
                              {asset.metadata?.["File Name"] || `${index + 1}`}
                            </div>
                          </button>
                        ))}
                      </div>
                    </>
                  ) : (
                    <div className="text-center text-museum-400 py-4">No Assets</div>
                  )}
                </div>

                {/* Source Material */}
                <div className="w-3/7 overflow-y-auto border-r border-museum-200 flex flex-col">
                  {/* Image Controls Header */}
                  <div className="flex items-center justify-between px-3 py-1.5 border-b border-museum-200 bg-museum-50">
                    {/* Page Navigation */}
                    <div className="flex items-center space-x-3">
                      <button
                        onClick={handlePreviousAsset}
                        disabled={currentAssetIndex === 0 || totalAssets === 0}
                        className="p-2 text-museum-500 hover:text-museum-700 hover:bg-white rounded-lg transition-colors disabled:text-museum-300 disabled:cursor-not-allowed disabled:hover:bg-transparent"
                        title="Previous Page"
                      >
                        <ChevronLeft className="w-5 h-5" />
                      </button>
                      <span className="text-sm font-medium text-museum-700 min-w-[100px] text-center">
                        {totalAssets > 0
                          ? currentAsset?.metadata?.["File Name"] ||
                            `Asset ${currentAssetIndex + 1}`
                          : "No Assets"}
                      </span>
                      <button
                        onClick={handleNextAsset}
                        disabled={
                          currentAssetIndex >= totalAssets - 1 ||
                          totalAssets === 0
                        }
                        className="p-2 text-museum-500 hover:text-museum-700 hover:bg-white rounded-lg transition-colors disabled:text-museum-300 disabled:cursor-not-allowed disabled:hover:bg-transparent"
                        title="Next Image"
                      >
                        <ChevronRight className="w-5 h-5" />
                      </button>
                    </div>

                    {/* Zoom Controls */}
                    <div className="flex items-center space-x-2">
                      <button
                        className="p-2 text-museum-500 hover:text-museum-700 hover:bg-white rounded-lg transition-colors"
                        title="Zoom Out"
                        onClick={handleZoomOut}
                      >
                        <ZoomOut className="w-4 h-4" />
                      </button>

                      <button
                        className="p-2 text-museum-500 hover:text-museum-700 hover:bg-white rounded-lg transition-colors"
                        title="Zoom In"
                        onClick={handleZoomIn}
                      >
                        <ZoomIn className="w-4 h-4" />
                      </button>
                      <button
                        className="p-2 text-museum-500 hover:text-museum-700 hover:bg-white rounded-lg transition-colors"
                        title="Reset Zoom"
                        onClick={handleResetZoom}
                      >
                        <RotateCw className="w-4 h-4" />
                      </button>
                      <button
                        className="p-2 text-museum-500 hover:text-museum-700 hover:bg-white rounded-lg transition-colors"
                        title="Fullscreen"
                        onClick={handleOpenFullscreen}
                        disabled={!currentAsset?.blob_url}
                      >
                        <Maximize className="w-4 h-4" />
                      </button>
                    </div>
                  </div>

                  {/* Image Display */}
                  <div
                    ref={imageContainerRef}
                    className={`flex-1 bg-museum-100 overflow-y-auto ocr-scrollbar ${
                      zoomLevel > 100
                        ? isDragging
                          ? "cursor-grabbing"
                          : "cursor-grab"
                        : "cursor-default"
                    }`}
                    onMouseDown={handleMouseDown}
                    onMouseMove={handleMouseMove}
                    onMouseUp={handleMouseUp}
                    onMouseLeave={handleMouseLeave}
                  >
                    {currentAsset?.blob_url ? (
                      // <div className="bg-museum-50 shadow-sm h-full flex items-center justify-center">
                      //   {/* <img
                      //     src={currentAsset.blob_url}
                      //     alt={`Asset ${currentAssetIndex + 1}`}
                      //     className="max-w-full max-h-full rounded-lg shadow-lg object-contain select-none"
                      //     style={{
                      //       transform: `scale(${zoomLevel / 100}) translate(${imagePosition.x / (zoomLevel / 100)}px, ${imagePosition.y / (zoomLevel / 100)}px)`,
                      //       transition: isDragging ? 'none' : 'transform 0.2s ease-out'
                      //     }}
                      //     draggable={false}
                      //   /> */}

                      // </div>
                      <DocumentViewer
                        key={currentAsset.blob_url}
                        fileUrl={currentAsset.blob_url}
                        zoomLevel={zoomLevel}
                        imagePosition={imagePosition}
                        isDragging={isDragging}
                      />
                    ) : (
                      <div className="bg-white rounded-lg shadow-sm h-full flex items-center justify-center">
                        <p className="text-museum-500">Assets not available</p>
                      </div>
                    )}
                  </div>

                  {/* Image Navigation Controls */}
                  {/* {totalAssets > 0 && (
                  <div className="border-t border-museum-200 p-3 bg-white">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm font-medium text-museum-700">
                        Image {currentAssetIndex + 1} of {totalAssets}
                      </span>
                      <div className="flex items-center space-x-2">
                        <button
                          onClick={handlePreviousAsset}
                          disabled={currentAssetIndex === 0}
                          className="p-1 text-museum-500 hover:text-museum-700 disabled:text-museum-300 disabled:cursor-not-allowed transition-colors"
                          title="Previous Image"
                        >
                          <ChevronLeft className="w-5 h-5" />
                        </button>
                        <button
                          onClick={handleNextAsset}
                          disabled={currentAssetIndex >= totalAssets - 1}
                          className="p-1 text-museum-500 hover:text-museum-700 disabled:text-museum-300 disabled:cursor-not-allowed transition-colors"
                          title="Next Image"
                        >
                          <ChevronRight className="w-5 h-5" />
                        </button>
                      </div>
                    </div> */}
                  {/* Thumbnail Strip */}
                  {/* <div className="flex items-center space-x-2 overflow-x-auto custom-scrollbar">
                      {documentData?.asset_details?.map((asset, index) => (
                        <button
                          key={index}
                          className={`w-6 h-8 rounded flex items-center justify-center text-xs font-medium border-2 flex-shrink-0 ${
                            currentAssetIndex === index
                              ? 'bg-museum-800 text-white border-museum-800'
                              : 'bg-museum-100 border-museum-300 text-museum-600 hover:bg-museum-200'
                          }`}
                          onClick={() => handleAssetSelect(index)}
                          title={asset.filename || `Image ${index + 1}`}
                        >
                          {index + 1}
                        </button>
                      ))}
                    </div> */}
                  {/* </div>
                )} */}
                </div>

                {/* Human Corrections Panel */}
                <div className="w-3/7 overflow-y-auto">
                  <CorrectionsPanel
                    ref={correctionsPanelRef}
                    documentId={id || ""}
                    transcription={transcription}
                    onTranscriptionChange={() => {}} // Read-only, no changes allowed
                    modifiedText={modifiedText}
                    onModifiedTextChange={(value) => {
                      setModifiedText(value);
                      setIsModified(true);
                      setDirtyFields((prev) =>
                        new Set(prev).add("modifiedText")
                      );
                    }}
                    onModifiedTextBlur={handleOcrBlur}
                    visualDescriptionModified={visualDescriptionModified}
                    onVisualDescriptionChange={(value) => {
                      setVisualDescriptionModified(value);
                      setIsModified(true);
                      setDirtyFields((prev) =>
                        new Set(prev).add("visualDescriptionModified")
                      );
                    }}
                    onVisualDescriptionBlur={handleVisualDescriptionBlur}
                    title={title}
                    onTitleChange={(value) => {
                      setTitle(value);
                      setIsModified(true);
                      setDirtyFields((prev) => new Set(prev).add("title"));
                    }}
                    onTitleBlur={(value) => handleFieldBlur("title", value)}
                    description={description}
                    onDescriptionChange={(value) => {
                      setDescription(value);
                      setIsModified(true);
                      setDirtyFields((prev) =>
                        new Set(prev).add("description")
                      );
                    }}
                    onDescriptionBlur={(value) =>
                      handleFieldBlur("description", value)
                    }
                    creationDate={creationDate}
                    onCreationDateChange={(value) => {
                      setCreationDate(value);
                      setIsModified(true);
                      setDirtyFields((prev) =>
                        new Set(prev).add("creationDate")
                      );
                    }}
                    onCreationDateBlur={(value) =>
                      handleFieldBlur("creationDate", value)
                    }
                    creator={creator}
                    onCreatorChange={(value) => {
                      setCreator(value);
                      setIsModified(true);
                      setDirtyFields((prev) => new Set(prev).add("creator"));
                    }}
                    onCreatorBlur={(value) => handleFieldBlur("creator", value)}
                    recipient={recipient}
                    onRecipientChange={(value) => {
                      setRecipient(value);
                      setIsModified(true);
                      setDirtyFields((prev) => new Set(prev).add("recipient"));
                    }}
                    onRecipientBlur={(value) =>
                      handleFieldBlur("recipient", value)
                    }
                    citation={citation}
                    onCitationChange={(value) => {
                      setCitation(value);
                      setIsModified(true);
                      setDirtyFields((prev) => new Set(prev).add("citation"));
                    }}
                    onCitationBlur={(value) =>
                      handleFieldBlur("citation", value)
                    }
                    resourceType={resourceType}
                    onResourceTypeChange={(value) => {
                      setResourceType(value);
                      setIsModified(true);
                      setDirtyFields((prev) =>
                        new Set(prev).add("resourceType")
                      );
                    }}
                    onResourceTypeBlur={(value) =>
                      handleFieldBlur("resourceType", value)
                    }
                    period={period}
                    onPeriodChange={(value) => {
                      setPeriod(value);
                      setIsModified(true);
                      setDirtyFields((prev) => new Set(prev).add("period"));
                    }}
                    onPeriodBlur={(value) => handleFieldBlur("period", value)}
                    repository={metadataRepository}
                    onRepositoryChange={(value) => {
                      setMetadataRepository(value);
                      setIsModified(true);
                    }}
                    rights={rights}
                    onRightsChange={(value) => {
                      setRights(value);
                      setIsModified(true);
                      setDirtyFields((prev) => new Set(prev).add("rights"));
                    }}
                    onRightsBlur={(value) => handleFieldBlur("rights", value)}
                    productionMethod={productionMethod}
                    onProductionMethodChange={(value) => {
                      setProductionMethod(value);
                      setIsModified(true);
                      setDirtyFields((prev) =>
                        new Set(prev).add("productionMethod")
                      );
                    }}
                    onProductionMethodBlur={(value) =>
                      handleFieldBlur("productionMethod", value)
                    }
                    language={language}
                    onLanguageChange={(value) => {
                      setLanguage(value);
                      setIsModified(true);
                      setDirtyFields((prev) => new Set(prev).add("language"));
                    }}
                    onLanguageBlur={(value) =>
                      handleFieldBlur("language", value)
                    }
                    severeDeviation={severeDeviation}
                    onSevereDeviationChange={(value) => {
                      setSevereDeviation(value);
                      setIsModified(true);
                    }}
                    deviationNotes={deviationNotes}
                    onDeviationNotesChange={(value) => {
                      setDeviationNotes(value);
                      setIsModified(true);
                    }}
                    archivistNotes={archivistNotes}
                    onArchivistNotesChange={(value) => {
                      setArchivistNotes(value);
                      setIsModified(true);
                      setDirtyFields((prev) =>
                        new Set(prev).add("archivistNotes")
                      );
                    }}
                    onArchivistNotesBlur={handleArchivistNotesBlur}
                    markComplete={markComplete}
                    onMarkCompleteChange={handleMarkCompleteChange}
                    onFormChange={() => setIsModified(true)}
                    isPublishing={isPublishing}
                    onApproveForPublishing={handleApproveForPublishingClick}
                    onUndoPublishing={handleUndoPublishing}
                    compareOriginalText={currentOcrTextOriginal}
                    dirtyFields={dirtyFields}
                    compareModifiedText={modifiedText}
                    onSaveAll={handleSaveAll}
                    isSaving={isSaving}
                    unsavedCount={dirtyFields.size}
                    apiDocument={documentData}
                    currentAsset={currentAsset}
                    isPublished={isRecordPublished()}
                    readOnly={!canEdit}
                  />
                </div>
              </div>
            </div>
            {/* Change History Toggle Button */}
            {id && (
              <div className="flex justify-center py-4 border-t border-museum-200 bg-white">
                <button
                  onClick={() => setShowChangeHistory(!showChangeHistory)}
                  className="text-museum-accent hover:text-museum-accent/80 transition-colors cursor-pointer"
                >
                  {showChangeHistory ? "Hide" : "Show"} Change History
                </button>
              </div>
            )}
          </>
        )}
      </div>
      {/* Change History Section - Animated Overlay */}
      {id && showChangeHistory && (
        <>
          {/* Backdrop - Transparent */}
          <div
            className="fixed inset-0 bg-transparent z-40 transition-opacity duration-500"
            onClick={() => setShowChangeHistory(false)}
          />

          {/* Change History Panel - Slides up from bottom */}
          <div
            className={`fixed bottom-0 left-0 right-0 z-50 bg-white border-t border-museum-200 shadow-2xl transition-all duration-500 ease-out ${
              showChangeHistory
                ? "translate-y-0 opacity-100"
                : "translate-y-full opacity-0"
            }`}
            style={{
              height: "50vh",
              maxHeight: "50vh",
            }}
          >
            <div className="h-full flex flex-col">
              {/* Header */}
              <div className="flex items-center justify-between px-6 py-4 border-b border-museum-200">
                <div className="flex items-center space-x-2">
                  <Clock className="w-6 h-6 text-museum-600" />
                  <h3 className="text-lg font-semibold text-museum-900">
                    Change History
                  </h3>
                </div>
                <button
                  onClick={() => setShowChangeHistory(false)}
                  className="p-2 text-museum-600 hover:text-museum-900 hover:bg-museum-100 rounded-lg transition-colors cursor-pointer"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              {/* Content */}
              <div className="flex-1 overflow-y-auto">
                <ChangeHistory
                  ref={changeHistoryRef}
                  documentId={id}
                  onRowClick={(change: ChangeDetail) =>
                    setSelectedChangeDetail(change)
                  }
                />
              </div>
            </div>
          </div>
        </>
      )}

      {/* Footer */}
      {/* <ActionFooter
        isModified={isModified}
        lastSaved={lastSaved}
        onSave={() => handleSave(false)}
        onSaveAndComplete={handleSaveAndComplete}
      /> */}

      {/* Modals & Sidebar */}
      <AuditSidebar
        isOpen={showAuditSidebar}
        onClose={() => setShowAuditSidebar(false)}
        documentId={id || ""}
      />
      <KeyboardShortcutsModal
        isOpen={showShortcuts}
        onClose={() => setShowShortcuts(false)}
      />

      {/* Toast */}
      {toast && (
        <Toast
          type={toast.type}
          message={toast.message}
          onClose={() => setToast(null)}
        />
      )}

      {/* Change Detail Modal */}
      {selectedChangeDetail && (
        <>
          {/* Backdrop overlay with 50% opacity */}
          <div
            className="absolute inset-0 bg-black opacity-70 z-20 transition-opacity duration-300"
            onClick={() => setSelectedChangeDetail(null)}
          />

          {/* Modal - positioned within main content area */}
          <div
            className="absolute inset-0 z-20 flex items-center justify-center p-4 pointer-events-none"
            onClick={() => setSelectedChangeDetail(null)}
          >
            <div
              className="bg-white rounded-lg shadow-2xl max-w-4xl w-full max-h-[calc(100vh-8rem)] overflow-hidden flex flex-col pointer-events-auto"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="px-6 py-4 border-b border-museum-200 bg-museum-50 flex justify-between">
                <div>
                  <h3 className="text-lg font-semibold text-museum-900">
                    Change Details
                  </h3>
                  <p className="text-sm text-museum-600 mt-1">
                    {selectedChangeDetail.field} • Version{" "}
                    {selectedChangeDetail.version} •{" "}
                    {new Intl.DateTimeFormat("en-US", {
                      year: "numeric",
                      month: "short",
                      day: "numeric",
                      hour: "2-digit",
                      minute: "2-digit",
                    }).format(new Date(selectedChangeDetail.changedAt))}
                  </p>
                </div>
                <button
                  onClick={() => setSelectedChangeDetail(null)}
                  className="p-2 text-museum-500 hover:text-museum-700 hover:bg-white rounded-lg transition-colors"
                  aria-label="Close"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              <div className="flex-1 overflow-y-auto p-6">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <h4 className="text-sm font-semibold text-museum-700 mb-2">
                      Previous Value
                    </h4>
                    <div className="bg-red-50 border border-red-200 rounded-lg p-4 min-h-[100px] max-h-[400px] overflow-y-auto">
                      {isLoadingContent ? (
                        <div className="text-center py-4">
                          <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-red-600 mx-auto"></div>
                          <p className="text-museum-500 mt-2 text-sm">Loading content...</p>
                        </div>
                      ) : oldValueContent !== null ? (
                        <div className="text-sm text-museum-800 whitespace-pre-wrap break-words">
                          {oldValueContent}
                        </div>
                      ) : selectedChangeDetail.oldValue ? (
                        <div className="text-sm text-museum-800 break-all whitespace-pre-wrap">
                          {selectedChangeDetail.oldValue}
                        </div>
                      ) : (
                        <span className="text-museum-400 italic text-sm">
                          (empty)
                        </span>
                      )}
                    </div>
                  </div>
                  <div>
                    <h4 className="text-sm font-semibold text-museum-700 mb-2">
                      New Value
                    </h4>
                    <div className="bg-green-50 border border-green-200 rounded-lg p-4 min-h-[100px] max-h-[400px] overflow-y-auto">
                      {isLoadingContent ? (
                        <div className="text-center py-4">
                          <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-green-600 mx-auto"></div>
                          <p className="text-museum-500 mt-2 text-sm">Loading content...</p>
                        </div>
                      ) : newValueContent !== null ? (
                        <div className="text-sm text-museum-800 whitespace-pre-wrap break-words">
                          {newValueContent}
                        </div>
                      ) : selectedChangeDetail.newValue ? (
                        <div className="text-sm text-museum-800 break-all whitespace-pre-wrap">
                          {selectedChangeDetail.newValue}
                        </div>
                      ) : (
                        <span className="text-museum-400 italic text-sm">
                          (empty)
                        </span>
                      )}
                    </div>
                  </div>
                </div>

                <div className="mt-4 pt-4 border-t border-museum-200">
                  <div className="grid grid-cols-2 gap-4 text-sm">
                    <div>
                      <span className="font-semibold text-museum-700">
                        Changed By:
                      </span>
                      <span className="ml-2 text-museum-600">
                        {selectedChangeDetail.changedBy}
                      </span>
                    </div>
                    <div>
                      <span className="font-semibold text-museum-700">
                        Field Path:
                      </span>
                      <span className="ml-2 text-museum-600 font-mono text-xs">
                        {selectedChangeDetail.fieldPath}
                      </span>
                    </div>
                  </div>
                </div>
              </div>

              <div className="px-6 py-4 border-t border-museum-200 bg-museum-50 flex justify-end">
                <button
                  onClick={() => setSelectedChangeDetail(null)}
                  className="px-4 py-2 bg-museum-600 text-white rounded-lg hover:bg-museum-700 transition-colors"
                >
                  Close
                </button>
              </div>
            </div>
          </div>
        </>
      )}

      {/* Publishing Confirm Dialog */}
      <DocumentConflictDialog
        isOpen={showConflictDialog}
        onClose={() => setShowConflictDialog(false)}
        onReload={handleReloadAfterConflict}
        isReloading={isReloadingAfterConflict}
      />

      <ConfirmDialog
        isOpen={showPublishConfirm}
        onClose={() => setShowPublishConfirm(false)}
        onConfirm={handleConfirmPublishing}
        title="Approve for Publishing"
        message="This will mark the document as ready for publication. The document will be queued for publishing and processed automatically. Are you sure you want to proceed?"
        confirmText="Approve for Publishing"
        cancelText="Cancel"
        variant="info"
        isLoading={isPublishingInProgress}
      />

      {/* Fullscreen Image Modal */}
      {isFullscreen && currentAsset?.blob_url && (
        <div
          className="fixed inset-0 z-50 bg-black bg-opacity-95 flex items-center justify-center"
          onClick={handleCloseFullscreen}
        >
          <button
            onClick={handleCloseFullscreen}
            className="absolute top-4 right-4 p-2 text-white hover:text-museum-200 bg-black bg-opacity-50 hover:bg-opacity-70 rounded-full transition-all z-10"
            title="Close Fullscreen (ESC)"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              className="h-8 w-8"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M6 18L18 6M6 6l12 12"
              />
            </svg>
          </button>
          <div onClick={(e) => e.stopPropagation()}>
            <AuthenticatedBlobImage
              src={currentAsset.blob_url}
              alt={`Asset ${currentAssetIndex + 1} - Fullscreen`}
              className="max-w-full max-h-full object-contain p-4"
            />
          </div>
        </div>
      )}
    </div>
  );
};

const CollectionReviewPage: React.FC = () => {
  const { repository, collectionName } = useParams<{
    repository: string;
    collectionName: string;
    recordid: string;
  }>();
  const decodedRepository = repository ? decodeURIComponent(repository) : "";
  const decodedCollectionName = collectionName
    ? decodeURIComponent(collectionName)
    : "";

  return (
    <FiltersProvider
      initialRepository={decodedRepository}
      initialCollectionName={decodedCollectionName}
    >
      <CollectionReviewPageContent />
    </FiltersProvider>
  );
};

export default CollectionReviewPage;