// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import {
  ChevronLeft,
  ChevronRight,
  Loader2,
  ZoomIn,
  ZoomOut,
  RotateCw,
  Upload,
  CloudOff,
  CheckCircle2,
  XCircle,
  FileText,
  Edit,
  Edit3,
  GitCompare,
  Save,
  Clock,
  User,
  ShieldCheck,
  Globe,
  X,
} from 'lucide-react'
import { diffWords } from 'diff'
import AuthenticatedImage from '@/components/AuthenticatedImage'
import PageHeader from '@/components/PageHeader'
import SinglePagePdfViewer from '@/components/SinglePagePdfViewer'
import { apiService, DigitalItem, DigitalItemPage } from '@/services/api'
import { useAuth } from '@/contexts/AuthContext'

type OcrTab = 'original' | 'modified' | 'compare'

const MooreChronologyViewerPage: React.FC = () => {
  const { itemId = '' } = useParams<{ itemId: string }>()
  const { isAdmin } = useAuth()

  // Volume & page state
  const [volume, setVolume] = useState<DigitalItem | null>(null)
  const [pageData, setPageData] = useState<DigitalItemPage | null>(null)
  const [currentPage, setCurrentPage] = useState(1)
  const [pageInput, setPageInput] = useState('1')
  const [loadingVolume, setLoadingVolume] = useState(true)
  const [loadingPage, setLoadingPage] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [zoomLevel, setZoomLevel] = useState(100)

  // OCR correction state
  const [activeTab, setActiveTab] = useState<OcrTab>('modified')
  const [modifiedText, setModifiedText] = useState('')
  const [isDirty, setIsDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveMsg, setSaveMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  // Publish state
  const [publishing, setPublishing] = useState(false)
  const [unpublishing, setUnpublishing] = useState(false)
  const [publishMsg, setPublishMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const [markReviewed, setMarkReviewed] = useState(false)

  // History state
  const [showHistory, setShowHistory] = useState(false)
  const [historyEntries, setHistoryEntries] = useState<any[]>([])
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [selectedHistoryEntry, setSelectedHistoryEntry] = useState<{ field: string; from: string; to: string; version: number; changedBy: string; changedAt: string } | null>(null)

  const pageCount = volume?.page_count || pageData?.page_count || volume?.files?.length || 1

  // ─── Load volume ───────────────────────────────────────────────────────────
  useEffect(() => {
    if (!itemId) return
    setLoadingVolume(true)
    setError(null)
    apiService
      .getDigitalItem(itemId, 'moore-chronology')
      .then((vol) => {
        setVolume(vol)
        setMarkReviewed(vol.publish_status === 'reviewed' || vol.publish_status === 'published')
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoadingVolume(false))
  }, [itemId])

  // ─── Load page ────────────────────────────────────────────────────────────
  const loadPage = useCallback(
    async (pageNumber: number) => {
      if (!itemId) return
      const clamped = Math.min(Math.max(pageNumber, 1), pageCount)
      setLoadingPage(true)
      setError(null)
      setSaveMsg(null)
      try {
        const data = await apiService.getDigitalItemPage(itemId, 'moore-chronology', clamped)
        setPageData(data)
        setCurrentPage(clamped)
        setPageInput(String(clamped))
        // Initialize modified text from flexible (or original)
        setModifiedText(data.ocr_text_flexible || data.ocr_text_original || '')
        setIsDirty(false)
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : 'Failed to load page'
        if (msg.toLowerCase().includes('not found') || msg.includes('404')) {
          const fallback: DigitalItemPage = {
            parent_id: itemId,
            source: 'moore-chronology',
            page_number: clamped,
            page_count: volume?.page_count || pageCount,
            pdf_url: volume?.primary_pdf_url,
            ocr_text_original: '',
            title: volume?.title,
          }
          setPageData(fallback)
          setCurrentPage(clamped)
          setPageInput(String(clamped))
          setModifiedText('')
          setIsDirty(false)
          setError(null)
        } else {
          setError(msg)
        }
      } finally {
        setLoadingPage(false)
      }
    },
    [itemId, pageCount, volume],
  )

  useEffect(() => {
    if (volume) loadPage(1)
  }, [volume, loadPage])

  // ─── Navigation ───────────────────────────────────────────────────────────
  const goToPage = () => {
    const parsed = parseInt(pageInput, 10)
    if (!Number.isNaN(parsed)) loadPage(parsed)
  }
  const handlePrevious = () => loadPage(currentPage - 1)
  const handleNext = () => loadPage(currentPage + 1)

  // ─── Zoom ─────────────────────────────────────────────────────────────────
  const handleZoomIn = () => setZoomLevel((p) => Math.min(p + 25, 300))
  const handleZoomOut = () => setZoomLevel((p) => Math.max(p - 25, 50))
  const handleResetZoom = () => setZoomLevel(100)

  // ─── OCR Save ─────────────────────────────────────────────────────────────
  const handleSave = async () => {
    if (!itemId || !modifiedText.trim()) return
    setSaving(true)
    setSaveMsg(null)
    try {
      // Get full text, replace this page's portion
      const compareData = await apiService.compareDigitalItemOcr(itemId, 'moore-chronology')
      const flexibleFull = compareData.ocr_text_flexible || compareData.ocr_text_original || ''
      const pages = flexibleFull.split('--- Page Break ---')
      if (currentPage >= 1 && currentPage <= pages.length) {
        pages[currentPage - 1] = '\n\n' + modifiedText.trim() + '\n\n'
      }
      const newFullText = pages.join('--- Page Break ---')
      const result = await apiService.updateDigitalItemOcr(itemId, 'moore-chronology', newFullText)
      setSaveMsg({ type: 'success', text: result.message || `Saved as version ${result.version}` })
      setIsDirty(false)
      // Reload to get fresh data
      await loadPage(currentPage)
    } catch (e: unknown) {
      setSaveMsg({ type: 'error', text: e instanceof Error ? e.message : 'Save failed' })
    } finally {
      setSaving(false)
    }
  }

  // ─── Review / Publish / Unpublish ──────────────────────────────────────────
  const handleReview = async () => {
    if (!itemId) return
    try {
      await apiService.reviewDigitalItem(itemId, 'moore-chronology')
      if (volume) setVolume({ ...volume, publish_status: 'reviewed' })
      setMarkReviewed(true)
    } catch (e: unknown) {
      setPublishMsg({ type: 'error', text: e instanceof Error ? e.message : 'Review failed' })
    }
  }

  const handlePublish = async () => {
    if (!itemId) return
    setPublishMsg(null)
    setPublishing(true)
    try {
      const result = await apiService.publishDigitalItem(itemId, 'moore-chronology')
      setPublishMsg({ type: 'success', text: result.message })
      if (volume) setVolume({ ...volume, publish_status: 'published', published_at: result.published_at })
    } catch (e: unknown) {
      setPublishMsg({ type: 'error', text: e instanceof Error ? e.message : 'Publish failed' })
    } finally {
      setPublishing(false)
    }
  }

  const handleUnpublish = async () => {
    if (!itemId) return
    setPublishMsg(null)
    setUnpublishing(true)
    try {
      const result = await apiService.unpublishDigitalItem(itemId, 'moore-chronology')
      setPublishMsg({ type: 'success', text: result.message })
      if (volume) {
        const updated = { ...volume, publish_status: 'pending' } as DigitalItem
        delete (updated as any).published_at
        setVolume(updated)
        setMarkReviewed(false)
      }
    } catch (e: unknown) {
      setPublishMsg({ type: 'error', text: e instanceof Error ? e.message : 'Unpublish failed' })
    } finally {
      setUnpublishing(false)
    }
  }

  // ─── History ──────────────────────────────────────────────────────────────
  const handleToggleHistory = async () => {
    if (!showHistory && itemId) {
      setLoadingHistory(true)
      try {
        const data = await apiService.getDigitalItemOcrHistory(itemId, 'moore-chronology')
        setHistoryEntries(data.entries || [])
      } catch {
        setHistoryEntries([])
      } finally {
        setLoadingHistory(false)
      }
    }
    setShowHistory(!showHistory)
  }

  // ─── Diff computation ─────────────────────────────────────────────────────
  const differences = useMemo(() => {
    const orig = pageData?.ocr_text_original || ''
    const mod = modifiedText || ''
    if (!orig && !mod) return []
    return diffWords(orig, mod)
  }, [pageData?.ocr_text_original, modifiedText])

  // ─── Loading / Error states ───────────────────────────────────────────────
  if (loadingVolume) {
    return (
      <div className="bg-gray-50 min-h-screen flex items-center justify-center">
        <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
        <span className="ml-2 text-gray-500">Loading chronology...</span>
      </div>
    )
  }

  if (error && !volume) {
    return (
      <div className="bg-gray-50 min-h-screen px-6 py-6">
        <div className="bg-red-50 border border-red-200 rounded-xl p-4">
          <p className="text-sm text-red-700">{error}</p>
        </div>
      </div>
    )
  }

  const pdfUrl = pageData?.pdf_url || volume?.primary_pdf_url || ''
  const imageFiles = (!pdfUrl && pageData?.files)
    ? pageData.files.filter(f => !f.split('?')[0].toLowerCase().endsWith('.pdf'))
    : []
  const currentImage = imageFiles[currentPage - 1] || imageFiles[0] || ''

  const subtitleLine = [
    volume?.date_range ? `Period: ${volume.date_range}` : '',
    volume?.metadata?.creators || '',
  ].filter(Boolean).join(' · ')

  const isPublished = volume?.publish_status === 'published'
  const isReviewed = volume?.publish_status === 'reviewed'
  const publishStatus = volume?.publish_status || 'pending'

  return (
    <div className="bg-gray-50 min-h-screen flex flex-col">
      {/* Header */}
      <PageHeader
        title={volume?.title || 'Moore Chronology'}
        subtitle={subtitleLine || undefined}
      >
        <label htmlFor="goto-page" className="text-sm text-white/80">Page</label>
        <input
          id="goto-page"
          type="number"
          min={1}
          max={pageCount}
          value={pageInput}
          onChange={(e) => setPageInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && goToPage()}
          className="w-16 px-2 py-1.5 text-sm border border-white/30 bg-white/10 text-white rounded-lg focus:outline-none focus:ring-2 focus:ring-white/40"
        />
        <button
          onClick={goToPage}
          disabled={loadingPage}
          className="px-3 py-1.5 text-sm font-medium rounded-lg bg-white/20 text-white hover:bg-white/30 disabled:opacity-50"
        >
          Go
        </button>
        <span className="text-sm text-white/70">of {pageCount}</span>
        {/* Status badge */}
        {publishStatus === 'published' && (
          <span className="ml-3 inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-green-500/20 text-green-100 text-xs font-medium">
            <Globe className="w-3 h-3" /> Published
          </span>
        )}
        {publishStatus === 'reviewed' && (
          <span className="ml-3 inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-purple-500/20 text-purple-100 text-xs font-medium">
            <ShieldCheck className="w-3 h-3" /> Reviewed
          </span>
        )}
        {(publishStatus === 'pending' || (!publishStatus)) && (
          <span className="ml-3 inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-yellow-500/20 text-yellow-100 text-xs font-medium">
            <Clock className="w-3 h-3" /> Pending
          </span>
        )}
      </PageHeader>

      {error && (
        <div className="px-6 py-2 bg-red-50 border-b border-red-200">
          <span className="text-sm text-red-600">{error}</span>
        </div>
      )}

      {/* Main content: PDF left | Corrections panel right */}
      <div className="flex flex-1 min-h-0 overflow-hidden" style={{ height: 'calc(100vh - 5rem)' }}>

        {/* ═══════════════ LEFT: PDF Viewer ═══════════════ */}
        <div className="w-1/2 border-r border-gray-200 bg-white flex flex-col min-h-0">
          {/* Toolbar */}
          <div className="px-4 py-2 border-b border-gray-200 bg-gray-50 flex items-center gap-2 h-11 shrink-0">
            <h3 className="text-sm font-semibold text-gray-700">{pdfUrl ? 'Source PDF' : 'Source Images'}</h3>
            <button
              onClick={handlePrevious}
              disabled={currentPage <= 1 || loadingPage}
              className="p-1.5 rounded border border-gray-200 text-gray-600 hover:bg-gray-100 disabled:opacity-40"
              title="Previous page"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <span className="text-xs text-gray-500 font-medium">
              {currentPage} / {pageCount}
            </span>
            <button
              onClick={handleNext}
              disabled={currentPage >= pageCount || loadingPage}
              className="p-1.5 rounded border border-gray-200 text-gray-600 hover:bg-gray-100 disabled:opacity-40"
              title="Next page"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
            <div className="flex items-center gap-1 ml-auto">
              <button onClick={handleZoomOut} className="p-1.5 rounded text-gray-500 hover:text-gray-700 hover:bg-gray-100" title="Zoom Out">
                <ZoomOut className="w-4 h-4" />
              </button>
              <span className="text-xs text-gray-500 w-9 text-center">{zoomLevel}%</span>
              <button onClick={handleZoomIn} className="p-1.5 rounded text-gray-500 hover:text-gray-700 hover:bg-gray-100" title="Zoom In">
                <ZoomIn className="w-4 h-4" />
              </button>
              <button onClick={handleResetZoom} className="p-1.5 rounded text-gray-500 hover:text-gray-700 hover:bg-gray-100" title="Reset">
                <RotateCw className="w-4 h-4" />
              </button>
            </div>
          </div>
          {/* PDF / Image Content */}
          <div className="flex-1 min-h-0 overflow-auto bg-gray-100">
            {pdfUrl ? (
              <SinglePagePdfViewer fileUrl={pdfUrl} pageNumber={currentPage} scale={zoomLevel / 100} />
            ) : currentImage ? (
              <div className="p-4 flex flex-col items-center">
                <AuthenticatedImage
                  rawUrl={currentImage}
                  alt={`Source image ${currentPage}`}
                  className="max-w-full rounded shadow-sm border border-gray-200"
                  style={{ transform: `scale(${zoomLevel / 100})`, transformOrigin: 'top center' }}
                />
              </div>
            ) : (
              <div className="flex items-center justify-center h-full text-gray-500 text-sm">No source document available</div>
            )}
          </div>
        </div>

        {/* ═══════════════ RIGHT: Human Correction Panel ═══════════════ */}
        <div className="w-1/2 bg-white flex flex-col min-h-0 overflow-y-auto">

          {/* Panel Header */}
          <div className="px-6 py-3 border-b border-gray-200 bg-gray-50 flex items-center justify-between shrink-0">
            <div className="flex items-center gap-3">
              <h2 className="text-sm font-bold text-gray-800">Human Validation &amp; Corrections</h2>
            </div>
            <div className="flex items-center gap-2">
              {isDirty && (
                <span className="inline-flex items-center gap-1 text-xs text-amber-600 font-medium">
                  <span className="w-2 h-2 rounded-full bg-amber-500 animate-pulse" />
                  Unsaved
                </span>
              )}
              <button
                onClick={handleSave}
                disabled={saving || !isDirty}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 text-white text-xs font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
                Save
              </button>
            </div>
          </div>

          {/* Save message */}
          {saveMsg && (
            <div className={`px-6 py-2 text-xs flex items-center gap-1.5 shrink-0 ${saveMsg.type === 'success' ? 'bg-green-50 text-green-700 border-b border-green-100' : 'bg-red-50 text-red-700 border-b border-red-100'}`}>
              {saveMsg.type === 'success' ? <CheckCircle2 className="w-3.5 h-3.5" /> : <XCircle className="w-3.5 h-3.5" />}
              {saveMsg.text}
            </div>
          )}

          {/* ─── OCR Tabs Section ─── */}
          <div className="px-6 py-4 border-b border-gray-100 flex-1 flex flex-col min-h-0">
            {/* Tab buttons */}
            <div className="flex items-center border-b border-gray-200 mb-4">
              {([
                { id: 'original', icon: FileText, label: 'Original OCR' },
                { id: 'modified', icon: Edit, label: 'Modified Text' },
                { id: 'compare', icon: GitCompare, label: 'Compare View' },
              ] as const).map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                    activeTab === tab.id
                      ? 'border-blue-600 text-blue-700'
                      : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                  }`}
                >
                  <tab.icon className="w-4 h-4" />
                  {tab.label}
                </button>
              ))}
              {pageData?.has_edits && (
                <span className="ml-auto text-xs text-amber-600 font-medium">● Edited</span>
              )}
              {pageData?.ocr_version && pageData.ocr_version > 1 && (
                <span className="ml-2 text-xs text-gray-400">v{pageData.ocr_version}</span>
              )}
              <span className={`${pageData?.has_edits ? '' : 'ml-auto'} text-xs text-gray-500 bg-gray-100 px-2 py-0.5 rounded`}>
                OCR Accuracy: {volume?.ocr_accuracy != null ? `${(volume.ocr_accuracy * 100).toFixed(1)}%` : 'NA'}
              </span>
            </div>

            {/* Tab content */}
            {loadingPage ? (
              <div className="flex items-center justify-center py-16">
                <Loader2 className="w-5 h-5 animate-spin text-gray-400" />
              </div>
            ) : (
              <div className="flex-1 flex flex-col min-h-0">
                {activeTab === 'original' && (
                  <div className="flex-1 flex flex-col min-h-0">
                    <div className="flex items-center justify-between mb-2">
                      <label className="text-xs font-semibold text-gray-600 uppercase">Original OCR Text (Read-only)</label>
                      <span className="text-xs text-gray-400">{(pageData?.ocr_text_original || '').length.toLocaleString()} chars</span>
                    </div>
                    <div className="w-full flex-1 min-h-80 px-4 py-3 border border-gray-200 rounded-lg bg-gray-50 overflow-y-auto text-sm leading-relaxed whitespace-pre-wrap text-gray-700">
                      {pageData?.ocr_text_original?.trim() || <span className="text-gray-400 italic">No OCR text available</span>}
                    </div>
                  </div>
                )}

                {activeTab === 'modified' && (
                  <div className="flex-1 flex flex-col min-h-0">
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <label className="text-xs font-semibold text-gray-600 uppercase">Modified Text (Editable)</label>
                        {pageData?.ocr_version && pageData.ocr_version > 1 && (
                          <span className="text-xs text-blue-600 font-medium bg-blue-50 px-1.5 py-0.5 rounded">v{pageData.ocr_version}</span>
                        )}
                      </div>
                      <span className="text-xs text-gray-400">{modifiedText.length.toLocaleString()} chars</span>
                    </div>
                    <textarea
                      className="w-full flex-1 min-h-80 px-4 py-3 border border-gray-200 rounded-lg focus:ring-2 focus:ring-blue-400 focus:border-blue-400 resize-none text-sm leading-relaxed text-gray-700"
                      placeholder="Edit the OCR text here..."
                      value={modifiedText}
                      onChange={(e) => { setModifiedText(e.target.value); setIsDirty(true) }}
                    />
                  </div>
                )}

                {activeTab === 'compare' && (
                  <div className="flex-1 flex flex-col min-h-0">
                    <div className="flex items-center justify-between mb-3">
                      <label className="text-xs font-semibold text-gray-600 uppercase">Text Comparison</label>
                      <div className="flex items-center gap-3 text-xs">
                        <span className="flex items-center gap-1"><span className="w-3 h-3 bg-green-200 border border-green-400 rounded" />Added</span>
                        <span className="flex items-center gap-1"><span className="w-3 h-3 bg-red-200 border border-red-400 rounded" />Removed</span>
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <h4 className="text-xs font-semibold text-gray-500 uppercase mb-2">Original</h4>
                        <div className="w-full flex-1 min-h-72 px-4 py-3 border border-gray-200 rounded-lg bg-gray-50 overflow-y-auto text-sm leading-relaxed whitespace-pre-wrap">
                          {pageData?.ocr_text_original?.trim() || <span className="text-gray-400 italic">No original text</span>}
                        </div>
                      </div>
                      <div className="flex flex-col">
                        <h4 className="text-xs font-semibold text-gray-500 uppercase mb-2">Modified</h4>
                        <div className="w-full flex-1 min-h-72 px-4 py-3 border border-gray-200 rounded-lg bg-white overflow-y-auto text-sm leading-relaxed whitespace-pre-wrap">
                          {differences.length > 0 ? (
                            differences.map((d, i) =>
                              d.added ? (
                                <span key={i} className="bg-green-200 text-green-900 px-0.5">{d.value}</span>
                              ) : d.removed ? (
                                <span key={i} className="bg-red-200 text-red-900 px-0.5 line-through">{d.value}</span>
                              ) : (
                                <span key={i}>{d.value}</span>
                              )
                            )
                          ) : (
                            <span className="text-gray-400 italic">No differences</span>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* ─── Review & Publish Section (compact) ─── */}
          {isAdmin && (
            <div className="px-4 py-1.5 border-b border-gray-100 flex items-center gap-2 flex-wrap text-xs shrink-0">
              <ShieldCheck className="w-3.5 h-3.5 text-blue-600 shrink-0" />
              <label className="flex items-center gap-1 cursor-pointer text-xs text-gray-700">
                <input
                  type="checkbox"
                  checked={markReviewed || isReviewed || isPublished}
                  onChange={(e) => { if (e.target.checked) handleReview(); }}
                  disabled={isPublished || isReviewed}
                  className="w-3.5 h-3.5 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                />
                Reviewed
              </label>
              {publishMsg && (
                <span className={`text-xs flex items-center gap-1 ${publishMsg.type === 'success' ? 'text-green-600' : 'text-red-600'}`}>
                  {publishMsg.type === 'success' ? <CheckCircle2 className="w-3 h-3" /> : <XCircle className="w-3 h-3" />}
                  {publishMsg.text}
                </span>
              )}
              <div className="ml-auto flex items-center gap-2">
                {!isPublished && (
                  <button
                    onClick={handlePublish}
                    disabled={publishing || unpublishing || !isReviewed}
                    className="inline-flex items-center gap-1.5 px-3 py-1 bg-blue-600 text-white text-xs font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                    title={!isReviewed ? 'Mark as reviewed first' : 'Publish modified text to search index'}
                  >
                    {publishing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
                    Publish
                  </button>
                )}
                {isPublished && (
                  <button
                    onClick={handleUnpublish}
                    disabled={publishing || unpublishing}
                    className="inline-flex items-center gap-1.5 px-3 py-1 bg-red-600 text-white text-xs font-medium rounded-lg hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    {unpublishing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <CloudOff className="w-3.5 h-3.5" />}
                    Unpublish
                  </button>
                )}
              </div>
            </div>
          )}

          {/* ─── View Changes / History Section ─── */}
          <div className="flex justify-center py-1.5 shrink-0">
            <button
              onClick={handleToggleHistory}
              className="text-xs font-medium text-blue-600 hover:text-blue-800 transition-colors cursor-pointer"
            >
              {showHistory ? 'Hide' : 'Show'} Change History
            </button>
          </div>
        </div>
      </div>

      {/* ─── Change History Panel (slides up from bottom, like repos/collections) ─── */}
      {showHistory && (
        <>
          <div
            className="fixed inset-0 bg-transparent z-40"
            onClick={() => setShowHistory(false)}
          />
          <div
            className="fixed bottom-0 left-0 right-0 z-50 bg-white border-t border-gray-200 shadow-2xl transition-all duration-500 ease-out"
            style={{ height: '50vh', maxHeight: '50vh' }}
          >
            <div className="h-full flex flex-col">
              {/* Header */}
              <div className="flex items-center justify-between px-6 py-3 border-b border-gray-200">
                <div className="flex items-center gap-2">
                  <Clock className="w-5 h-5 text-gray-600" />
                  <h3 className="text-sm font-semibold text-gray-900">Change History</h3>
                </div>
                <button
                  onClick={() => setShowHistory(false)}
                  className="p-1.5 text-gray-500 hover:text-gray-800 hover:bg-gray-100 rounded-lg transition-colors"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
              {/* Content */}
              <div className="flex-1 overflow-y-auto px-6 py-4">
                {loadingHistory ? (
                  <div className="flex items-center justify-center py-8">
                    <Loader2 className="w-5 h-5 animate-spin text-gray-400" />
                    <span className="ml-2 text-sm text-gray-500">Loading history...</span>
                  </div>
                ) : historyEntries.length === 0 ? (
                  <p className="text-sm text-gray-400 text-center py-8">No changes recorded yet.</p>
                ) : (
                  <table className="w-full text-xs border-collapse text-center">
                    <thead>
                      <tr className="bg-gray-50 border-b border-gray-200">
                        <th className="px-4 py-2.5 text-sm font-semibold text-gray-700">Change</th>
                        <th className="px-4 py-2.5 text-sm font-semibold text-gray-700">Previous Text</th>
                        <th className="px-4 py-2.5 text-sm font-semibold text-gray-700">New Text</th>
                        <th className="px-4 py-2.5 text-sm font-semibold text-gray-700">Changed By</th>
                        <th className="px-4 py-2.5 text-sm font-semibold text-gray-700">Date &amp; Time</th>
                        <th className="px-4 py-2.5 text-sm font-semibold text-gray-700">Version</th>
                      </tr>
                    </thead>
                    <tbody>
                      {historyEntries.map((entry: any, eIdx: number) => {
                        // Prefer the /ocr_text field; skip blob URL fields
                        const textField = (entry.fields || []).find((f: any) => f.path === '/ocr_text')
                        const field = textField || (entry.fields || [])[0]
                        if (!field) return null
                        const fieldName = (field.path || '').split('/').pop() || field.path
                        const truncate = (v: any, max = 60) => {
                          const s = v == null ? '' : String(v)
                          return s.length <= max ? s : s.substring(0, max) + '…'
                        }
                        return (
                          <tr
                            key={entry.id || eIdx}
                            onClick={() => setSelectedHistoryEntry({
                              field: fieldName,
                              from: field.from || '',
                              to: field.to || '',
                              version: entry.version,
                              changedBy: entry.by?.display || 'System',
                              changedAt: entry.ts || '',
                            })}
                            className={`border-b border-gray-100 hover:bg-blue-50 transition-colors cursor-pointer ${
                              eIdx % 2 === 0 ? 'bg-white' : 'bg-gray-50'
                            }`}
                          >
                            <td className="px-4 py-2.5">
                              <span className="inline-flex items-center gap-1 font-medium text-gray-800">
                                <Edit3 className="w-3.5 h-3.5 text-gray-500" />
                                OCR Edit
                              </span>
                            </td>
                            <td className="px-4 py-2.5 text-gray-600 text-left max-w-40 truncate" title={String(field.from || '')}>
                              {truncate(field.from) || <span className="text-gray-400 italic">(original)</span>}
                            </td>
                            <td className="px-4 py-2.5 text-gray-800 text-left max-w-40 truncate" title={String(field.to || '')}>
                              {truncate(field.to)}
                            </td>
                            <td className="px-4 py-2.5 text-gray-700">
                              <span className="inline-flex items-center gap-1">
                                <User className="w-3.5 h-3.5 text-gray-400" />
                                {entry.by?.display || 'System'}
                              </span>
                            </td>
                            <td className="px-4 py-2.5 text-gray-500">
                              {entry.ts ? new Intl.DateTimeFormat('en-US', { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(entry.ts)) : '—'}
                            </td>
                            <td className="px-4 py-2.5 text-gray-500">v{entry.version}</td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          </div>
        </>
      )}

      {/* ─── Change Detail Modal ─── */}
      {selectedHistoryEntry && (
        <>
          <div
            className="fixed inset-0 bg-black/60 z-60"
            onClick={() => setSelectedHistoryEntry(null)}
          />
          <div
            className="fixed inset-0 z-60 flex items-center justify-center p-4 pointer-events-none"
            onClick={() => setSelectedHistoryEntry(null)}
          >
            <div
              className="bg-white rounded-lg shadow-2xl max-w-4xl w-full max-h-[calc(100vh-8rem)] overflow-hidden flex flex-col pointer-events-auto"
              onClick={(e) => e.stopPropagation()}
            >
              {/* Header */}
              <div className="px-6 py-3 border-b border-gray-200 bg-gray-50 flex items-center justify-between">
                <div>
                  <h3 className="text-sm font-semibold text-gray-900">Change Details</h3>
                  <p className="text-xs text-gray-500 mt-0.5">
                    {selectedHistoryEntry.field} · Version {selectedHistoryEntry.version} ·{' '}
                    {selectedHistoryEntry.changedAt ? new Intl.DateTimeFormat('en-US', { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(selectedHistoryEntry.changedAt)) : ''}
                  </p>
                </div>
                <button
                  onClick={() => setSelectedHistoryEntry(null)}
                  className="p-1.5 text-gray-500 hover:text-gray-700 hover:bg-white rounded-lg transition-colors"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
              {/* Body */}
              <div className="flex-1 overflow-y-auto p-6">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <h4 className="text-xs font-semibold text-gray-600 uppercase mb-2">Previous Value</h4>
                    <div className="bg-red-50 border border-red-200 rounded-lg p-4 min-h-25 max-h-100 overflow-y-auto text-sm text-gray-800 whitespace-pre-wrap wrap-break-word">
                      {selectedHistoryEntry.from || <span className="text-gray-400 italic">(empty)</span>}
                    </div>
                  </div>
                  <div>
                    <h4 className="text-xs font-semibold text-gray-600 uppercase mb-2">New Value</h4>
                    <div className="bg-green-50 border border-green-200 rounded-lg p-4 min-h-25 max-h-100 overflow-y-auto text-sm text-gray-800 whitespace-pre-wrap wrap-break-word">
                      {selectedHistoryEntry.to || <span className="text-gray-400 italic">(empty)</span>}
                    </div>
                  </div>
                </div>
                <div className="mt-4 pt-3 border-t border-gray-200 grid grid-cols-2 gap-4 text-sm">
                  <div><span className="font-semibold text-gray-700">Changed By:</span> <span className="text-gray-600">{selectedHistoryEntry.changedBy}</span></div>
                  <div><span className="font-semibold text-gray-700">Field:</span> <span className="text-gray-600 font-mono text-xs">{selectedHistoryEntry.field}</span></div>
                </div>
              </div>
              {/* Footer */}
              <div className="px-6 py-3 border-t border-gray-200 bg-gray-50 flex justify-end">
                <button
                  onClick={() => setSelectedHistoryEntry(null)}
                  className="px-4 py-1.5 bg-gray-700 text-white text-sm rounded-lg hover:bg-gray-800 transition-colors"
                >
                  Close
                </button>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

export default MooreChronologyViewerPage
