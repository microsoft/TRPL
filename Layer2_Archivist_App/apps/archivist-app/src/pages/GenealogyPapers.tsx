import React, { useEffect, useState, useRef } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, ArrowRight, BookOpen, Volume2, Image as ImageIcon, Loader2, ChevronDown, FileText, Users, AlignLeft, Upload, EyeOff, CheckCircle2, XCircle } from 'lucide-react'
import PageHeader from '@/components/PageHeader'
import AuthenticatedImage from '@/components/AuthenticatedImage'
import AuthenticatedAudio from '@/components/AuthenticatedAudio'
import { apiService, DigitalItem } from '@/services/api'
import { useAuth } from '@/contexts/AuthContext'

const ICON_COLORS: Record<string, { bg: string; text: string }> = {
  document: { bg: 'bg-blue-100', text: 'text-blue-600' },
  index: { bg: 'bg-indigo-100', text: 'text-indigo-600' },
  'index-letter': { bg: 'bg-indigo-100', text: 'text-indigo-600' },
  speeches: { bg: 'bg-amber-100', text: 'text-amber-600' },
  papers: { bg: 'bg-purple-100', text: 'text-purple-600' },
  family: { bg: 'bg-rose-100', text: 'text-rose-600' },
  genealogy: { bg: 'bg-green-100', text: 'text-green-600' },
  chronology: { bg: 'bg-cyan-100', text: 'text-cyan-600' },
  bibliography: { bg: 'bg-orange-100', text: 'text-orange-600' },
  introduction: { bg: 'bg-gray-100', text: 'text-gray-600' },
}

/** Whether a section item acts as a hub (has sub-items to drill into) */
function isHubPattern(pattern?: string): boolean {
  return pattern === 'speeches_sections' || pattern === 'index_hub' || pattern === 'content_hub'
}

/** Interactive cyclopedia entry with hover preview */
const EntryCard: React.FC<{ entry: { letter?: string; heading?: string; body?: string } }> = ({ entry }) => {
  const [expanded, setExpanded] = useState(false)
  const [hovered, setHovered] = useState(false)
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const handleMouseEnter = () => {
    if (timeoutRef.current) clearTimeout(timeoutRef.current)
    setHovered(true)
  }

  const handleMouseLeave = () => {
    timeoutRef.current = setTimeout(() => setHovered(false), 300)
  }

  const bodyPreview = entry.body ? entry.body.slice(0, 200) : ''

  return (
    <div
      className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm hover:shadow-md transition-all relative"
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      <div
        className="flex items-center justify-between cursor-pointer"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-baseline gap-2 min-w-0">
          {entry.letter && (
            <span className="text-xs font-semibold text-amber-600 bg-amber-50 px-2 py-0.5 rounded shrink-0">
              {entry.letter}
            </span>
          )}
          <h4 className="text-sm font-semibold text-gray-900 truncate">{entry.heading}</h4>
        </div>
        <ChevronDown className={`w-4 h-4 text-gray-400 shrink-0 ml-2 transition-transform ${expanded ? 'rotate-180' : ''}`} />
      </div>

      {/* Hover tooltip preview */}
      {hovered && !expanded && bodyPreview && (
        <div className="absolute z-40 left-0 right-0 top-full mt-1 bg-white border border-gray-200 rounded-xl shadow-xl p-4 pointer-events-none">
          <p className="text-xs text-gray-600 leading-relaxed line-clamp-4">{bodyPreview}...</p>
        </div>
      )}

      {/* Expanded full content */}
      {expanded && entry.body && (
        <div className="mt-3 pt-3 border-t border-gray-100">
          <p className="text-sm text-gray-600 leading-relaxed whitespace-pre-line">{entry.body}</p>
        </div>
      )}
    </div>
  )
}

type BlobContent = {
  plain_text?: string
  document_sections?: any[]
  cyclopedia_entries?: any[]
  toc_entries?: any[]
  linked_topics?: any[]
}

/** Detail view for a single item */
export const GenealogyPapersDetail: React.FC = () => {
  const { itemId } = useParams<{ itemId: string }>()
  const navigate = useNavigate()
  const { isAdmin, isArchivist, isDataFoundations } = useAuth()
  const canPublish = isAdmin || isArchivist || isDataFoundations
  const [item, setItem] = useState<DigitalItem | null>(null)
  const [children, setChildren] = useState<DigitalItem[]>([])
  const [allItems, setAllItems] = useState<DigitalItem[]>([])
  const [expandedEntry, setExpandedEntry] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [contentLoading, setContentLoading] = useState(false)
  const [blobContent, setBlobContent] = useState<BlobContent | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [publishStatus, setPublishStatus] = useState<string | null>(null)
  const [publishLoading, setPublishLoading] = useState<'publish' | 'unpublish' | null>(null)
  const [publishFeedback, setPublishFeedback] = useState<{ type: 'success' | 'error'; message: string } | null>(null)

  useEffect(() => {
    if (!itemId) return
    Promise.all([
      apiService.getDigitalItem(itemId, 'genealogy-papers'),
      apiService.getDigitalItemsBySource('genealogy-papers', undefined, 500),
    ])
      .then(([detail, all]) => {
        setItem(detail)
        setPublishStatus(detail.publish_status ?? null)
        setAllItems(all.items)
        if (isHubPattern(detail.pattern)) {
          const kids = all.items
            .filter((i: DigitalItem) => i.parent_module_id === detail.module_id)
            .sort((a: DigitalItem, b: DigitalItem) => {
              if (detail.pattern === 'index_hub') {
                return (a.title || '').localeCompare(b.title || '')
              }
              // Sub-items (speeches, papers entries) don't have order set;
              // fall back to alphabetical when neither side has a defined order.
              const ao = a.order ?? null
              const bo = b.order ?? null
              if (ao !== null && bo !== null) return ao - bo
              return (a.title || '').localeCompare(b.title || '')
            })
          setChildren(kids)
        }
      })
      .catch(err => setError(err.message))
      .finally(() => setLoading(false))
  }, [itemId])

  // Load full content from blob storage when content_url is available
  useEffect(() => {
    if (!item?.content_url) return
    setContentLoading(true)
    apiService.fetchAssetProxyContent(item.content_url)
      .then(res => {
        if (!res.ok) throw new Error(`Failed to load content (${res.status})`)
        return res.json()
      })
      .then((data: BlobContent) => setBlobContent(data))
      .catch(() => setBlobContent(null))
      .finally(() => setContentLoading(false))
  }, [item?.content_url])

  if (loading) {
    return (
      <div className="bg-gray-50 min-h-screen">
        <PageHeader title="Loading..." subtitle="Digitized Genealogy & Papers by the TRA" />
        <div className="flex items-center justify-center py-12">
          <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
          <span className="ml-2 text-gray-500">Loading...</span>
        </div>
      </div>
    )
  }

  if (error || !item) {
    return (
      <div className="bg-gray-50 min-h-screen">
        <PageHeader title="Error" subtitle="Digitized Genealogy & Papers by the TRA" />
        <div className="px-6 py-6 max-w-4xl mx-auto">
          <button
            onClick={() => navigate('/digital-resources/genealogy-papers')}
            className="flex items-center gap-2 text-sm text-gray-600 hover:text-museum-accent mb-6 transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
            Back to Genealogy &amp; Papers
          </button>
          <div className="bg-red-50 border border-red-200 rounded-xl p-4">
            <p className="text-sm text-red-700">{error || 'Item not found'}</p>
          </div>
        </div>
      </div>
    )
  }

  // Back navigation: if this item has a parent hub, go back to it instead of the root page
  const parentItem = item.parent_module_id
    ? allItems.find(i => i.module_id === item.parent_module_id)
    : null
  const backPath = parentItem
    ? `/digital-resources/genealogy-papers/${parentItem.id}`
    : '/digital-resources/genealogy-papers'
  const backLabel = parentItem
    ? `Back to ${parentItem.title || 'section'}`
    : 'Back to Genealogy & Papers'

  // Publish handlers — declared before hub-view early return so both views can use them
  const handlePublish = async () => {
    if (!item) return
    setPublishLoading('publish')
    setPublishFeedback(null)
    try {
      await apiService.publishDigitalItem(item.id, 'genealogy-papers')
      setPublishStatus('published')
      setPublishFeedback({ type: 'success', message: 'Item published to search index.' })
    } catch (e: unknown) {
      setPublishFeedback({ type: 'error', message: e instanceof Error ? e.message : 'Failed to publish.' })
    } finally {
      setPublishLoading(null)
    }
  }

  const handleUnpublish = async () => {
    if (!item) return
    setPublishLoading('unpublish')
    setPublishFeedback(null)
    try {
      await apiService.unpublishDigitalItem(item.id, 'genealogy-papers')
      setPublishStatus('unpublished')
      setPublishFeedback({ type: 'success', message: 'Item removed from search index.' })
    } catch (e: unknown) {
      setPublishFeedback({ type: 'error', message: e instanceof Error ? e.message : 'Failed to unpublish.' })
    } finally {
      setPublishLoading(null)
    }
  }

  // Hub view (speeches / index / content): shows list of children to drill into
  if (isHubPattern(item.pattern) && children.length > 0) {
    const hubColors = ICON_COLORS[item.item_type || 'document'] || ICON_COLORS.document

    // ── Index A-Z hub: alphabet grid ──────────────────────────────────────
    if (item.pattern === 'index_hub') {
      const letterMap = new Map(
        children.map(c => [(c.title || '').replace(/^Index\s+/i, '').trim().toUpperCase(), c.id])
      )
      const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('')
      return (
        <div className="bg-gray-50 min-h-screen">
          <PageHeader title="Index A–Z" subtitle="Encyclopedia of Theodore Roosevelt's writings and speeches" />
          <div className="px-6 py-6 max-w-4xl mx-auto">
            <button
              onClick={() => navigate('/digital-resources/genealogy-papers')}
              className="flex items-center gap-2 text-sm text-gray-600 hover:text-museum-accent mb-6 transition-colors"
            >
              <ArrowLeft className="w-4 h-4" />
              Back to Genealogy &amp; Papers
            </button>
            <div className="bg-white rounded-xl border border-gray-200 p-6 shadow-sm mb-8">
              <h2 className="text-base font-semibold text-gray-900 mb-2">Browse Index A–Z</h2>
              <p className="text-sm text-gray-600">Utilizing this index, you can look up topics in the TRA Cyclopedia. Select a letter from the alphabet to browse entries.</p>
            </div>
            <div className="grid grid-cols-6 sm:grid-cols-9 gap-3">
              {alphabet.map(letter => {
                const id = letterMap.get(letter)
                return id ? (
                  <button
                    key={letter}
                    onClick={() => navigate(`/digital-resources/genealogy-papers/${id}`)}
                    className="aspect-square flex items-center justify-center text-xl font-bold rounded-xl border-2 border-museum-accent text-museum-accent hover:bg-museum-accent hover:text-white transition-all duration-200 shadow-sm hover:shadow-md"
                  >
                    {letter}
                  </button>
                ) : (
                  <div key={letter} className="aspect-square flex items-center justify-center text-xl font-bold rounded-xl border-2 border-gray-100 text-gray-200 cursor-default">
                    {letter}
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      )
    }

    return (
      <div className="bg-gray-50 min-h-screen">
        <PageHeader title={item.title || ''} subtitle={`${children.length} item${children.length !== 1 ? 's' : ''}`} />
        <div className="px-6 py-6 max-w-4xl mx-auto">
          <div className="flex items-center justify-between mb-6">
            <button
              onClick={() => navigate('/digital-resources/genealogy-papers')}
              className="flex items-center gap-2 text-sm text-gray-600 hover:text-museum-accent transition-colors"
            >
              <ArrowLeft className="w-4 h-4" />
              Back to Genealogy &amp; Papers
            </button>

            {/* Publish / Unpublish controls for hub items */}
            {canPublish && (
              <div className="flex items-center gap-2">
                {publishFeedback && (
                  <span className={`flex items-center gap-1 text-xs font-medium ${publishFeedback.type === 'success' ? 'text-emerald-600' : 'text-red-600'}`}>
                    {publishFeedback.type === 'success'
                      ? <CheckCircle2 className="w-3.5 h-3.5" />
                      : <XCircle className="w-3.5 h-3.5" />}
                    {publishFeedback.message}
                  </span>
                )}
                {publishStatus === 'published' && !publishFeedback && (
                  <span className="text-xs text-emerald-600 font-medium flex items-center gap-1">
                    <CheckCircle2 className="w-3.5 h-3.5" /> Published
                  </span>
                )}
                <button
                  onClick={handlePublish}
                  disabled={publishLoading !== null}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  {publishLoading === 'publish' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
                  Publish
                </button>
                <button
                  onClick={handleUnpublish}
                  disabled={publishLoading !== null}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-white border border-gray-300 text-gray-700 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  {publishLoading === 'unpublish' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <EyeOff className="w-3.5 h-3.5" />}
                  Unpublish
                </button>
              </div>
            )}
          </div>

          {item.plain_text && (
            <div className="bg-white rounded-xl border border-gray-200 p-6 shadow-sm mb-6">
              <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-line">{item.plain_text}</p>
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {children.map((child) => {
              const childColors = ICON_COLORS[child.item_type || 'document'] || ICON_COLORS.document
              return (
                <div
                  key={child.id}
                  onClick={() => navigate(`/digital-resources/genealogy-papers/${child.id}`)}
                  className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm hover:shadow-lg transition-all duration-300 hover:-translate-y-0.5 cursor-pointer group flex items-center justify-between"
                >
                  <div className="flex items-center gap-4 min-w-0">
                    <div className={`w-10 h-10 shrink-0 ${childColors.bg} rounded-lg flex items-center justify-center`}>
                      {child.image_url
                        ? <ImageIcon className={`w-5 h-5 ${childColors.text}`} />
                        : <BookOpen className={`w-5 h-5 ${childColors.text}`} />}
                    </div>
                    <div className="min-w-0">
                      <h3 className="text-sm font-semibold text-gray-900 group-hover:text-museum-accent transition-colors truncate">
                        {child.title || child.hub_label}
                      </h3>
                      {child.description && (
                        <p className="text-xs text-gray-500 mt-0.5 truncate">{child.description.slice(0, 80)}</p>
                      )}
                    </div>
                  </div>
                  <ArrowRight className="w-5 h-5 text-gray-300 group-hover:text-gray-600 transition-all shrink-0 ml-3" />
                </div>
              )
            })}
          </div>
        </div>
      </div>
    )
  }

  // Content detail view — prefer blob content when available
  const plainText = blobContent?.plain_text || item.plain_text
  const sections = blobContent?.document_sections || item.document_sections || []
  const entries = blobContent?.cyclopedia_entries || item.cyclopedia_entries || []
  const tocEntries = blobContent?.toc_entries || item.toc_entries || []
  const allImages: { src: string; alt?: string }[] = item.images || (item.image_url ? [{ src: item.image_url, alt: item.image_alt }] : [])

  return (
    <div className="bg-gray-50 min-h-screen">
      <PageHeader title={item.title || ''} subtitle="Digitized Genealogy & Papers by the TRA" />
      <div className="px-6 py-6 max-w-4xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <button
            onClick={() => navigate(backPath)}
            className="flex items-center gap-2 text-sm text-gray-600 hover:text-museum-accent transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
            {backLabel}
          </button>

          {/* Publish / Unpublish controls — visible to admins/archivists only */}
          {canPublish && (
            <div className="flex items-center gap-2">
              {publishFeedback && (
                <span className={`flex items-center gap-1 text-xs font-medium ${publishFeedback.type === 'success' ? 'text-emerald-600' : 'text-red-600'}`}>
                  {publishFeedback.type === 'success'
                    ? <CheckCircle2 className="w-3.5 h-3.5" />
                    : <XCircle className="w-3.5 h-3.5" />}
                  {publishFeedback.message}
                </span>
              )}
              {publishStatus === 'published' && !publishFeedback && (
                <span className="text-xs text-emerald-600 font-medium flex items-center gap-1">
                  <CheckCircle2 className="w-3.5 h-3.5" /> Published
                </span>
              )}
              <button
                onClick={handlePublish}
                disabled={publishLoading !== null}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                title="Publish to search index"
              >
                {publishLoading === 'publish' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
                Publish
              </button>
              <button
                onClick={handleUnpublish}
                disabled={publishLoading !== null}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-white border border-gray-300 text-gray-700 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                title="Remove from search index"
              >
                {publishLoading === 'unpublish' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <EyeOff className="w-3.5 h-3.5" />}
                Unpublish
              </button>
            </div>
          )}
        </div>

        {item.image_url && (
          <div className={`mb-6 ${allImages.length > 1 ? 'grid grid-cols-2 gap-3' : 'flex justify-center'}`}>
            {allImages.map((img, idx) => (
              <div key={idx} className="flex justify-center">
                <AuthenticatedImage
                  rawUrl={img.src}
                  alt={img.alt || item.title || ''}
                  className="max-w-full max-h-80 rounded-xl shadow-md border border-gray-200 object-contain"
                />
              </div>
            ))}
          </div>
        )}

        {contentLoading && (
          <div className="flex items-center gap-2 py-4 text-gray-400">
            <Loader2 className="w-4 h-4 animate-spin" />
            <span className="text-sm">Loading content...</span>
          </div>
        )}

        {/* ── Index letter view (A, B, C…) ─────────────────────────────── */}
        {!contentLoading && item.item_type === 'index' && (sections.length > 0 || !!plainText) && (() => {
          const indexSiblings = allItems
            .filter(i => i.item_type === 'index' && i.parent_module_id === item.parent_module_id)
            .sort((a, b) => (a.title || '').localeCompare(b.title || ''))
          const siblingMap = new Map(indexSiblings.map(i => [(i.title || '').replace(/^Index\s+/i, '').trim().toUpperCase(), i.id]))
          const currentLetter = (item.title || '').replace(/^Index\s+/i, '').trim().toUpperCase()
          const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('')
          const NOISE = new Set(['Browse Index A-Z', 'Browse the Index'])

          // Build entries from document_sections when available
          let entries = sections.filter((s: any) =>
            s.heading &&
            !NOISE.has(s.heading) &&
            !/^[A-Z]$/.test(s.heading.trim()) &&
            !/^[A-Z]\s+[A-Z]\s+[A-Z]/.test(s.heading)
          )

          // Fallback: parse plain_text into entries when document_sections is absent
          if (entries.length === 0 && plainText) {
            const noiseRe = /^(Index\s+[A-Z]|Browse\s+Index|[A-Z](\s+[A-Z]){4,})$/i
            const paras = plainText.split(/\n{2,}/).map((p: string) => p.trim()).filter(Boolean)
            let pendingHeading = ''
            const parsed: Array<{heading: string; body: string}> = []
            for (const para of paras) {
              if (!para || noiseRe.test(para) || /^[A-Z]$/.test(para)) continue
              if (para.length < 80 && !para.includes('\n')) {
                // Short line = new entry heading
                if (pendingHeading) parsed.push({ heading: pendingHeading, body: '' })
                pendingHeading = para
              } else {
                // Body for the last heading
                if (pendingHeading) {
                  parsed.push({ heading: pendingHeading, body: para })
                  pendingHeading = ''
                }
              }
            }
            if (pendingHeading) parsed.push({ heading: pendingHeading, body: '' })
            entries = parsed
          }
          return (
            <>
              {/* Alphabet nav bar */}
              <div className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm mb-6">
                <div className="flex flex-wrap gap-1.5">
                  {alphabet.map(letter => {
                    const id = siblingMap.get(letter)
                    const isCurrent = letter === currentLetter
                    if (isCurrent) return (
                      <span key={letter} className="w-8 h-8 flex items-center justify-center text-sm font-bold rounded-lg bg-museum-accent text-white">
                        {letter}
                      </span>
                    )
                    if (id) return (
                      <button key={letter} onClick={() => { setExpandedEntry(null); navigate(`/digital-resources/genealogy-papers/${id}`) }}
                        className="w-8 h-8 flex items-center justify-center text-sm font-semibold rounded-lg border border-gray-200 text-gray-700 hover:border-museum-accent hover:text-museum-accent transition-colors">
                        {letter}
                      </button>
                    )
                    return (
                      <span key={letter} className="w-8 h-8 flex items-center justify-center text-sm text-gray-200 cursor-default">{letter}</span>
                    )
                  })}
                </div>
              </div>

              {/* Entry count */}
              <p className="text-xs text-gray-400 mb-3 px-1">{entries.length} entries under &ldquo;{item.title}&rdquo;</p>

              {/* Expandable entry accordion */}
              <div className="space-y-1">
                {entries.map((entry: any, i: number) => (
                  <div key={i} className="bg-white rounded-lg border border-gray-200 overflow-hidden shadow-sm">
                    <button
                      onClick={() => setExpandedEntry(expandedEntry === i ? null : i)}
                      className="w-full text-left px-5 py-3 flex items-center justify-between gap-3 hover:bg-gray-50 transition-colors"
                    >
                      <span className="text-sm font-medium text-gray-900 leading-snug">{entry.heading}</span>
                      {entry.body && (
                        <ChevronDown className={`w-4 h-4 shrink-0 text-gray-400 transition-transform duration-200 ${expandedEntry === i ? 'rotate-180' : ''}`} />
                      )}
                    </button>
                    {expandedEntry === i && entry.body && (
                      <div className="px-5 pb-4 pt-1 border-t border-gray-100 bg-gray-50">
                        <p className="text-sm text-gray-700 leading-relaxed italic">&ldquo;{entry.body}&rdquo;</p>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </>
          )
        })()}

        {!contentLoading && item.item_type !== 'index' && plainText && (
          <div className="bg-white rounded-xl border border-gray-200 p-6 shadow-sm">
            <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-line">{plainText}</p>
          </div>
        )}

        {!contentLoading && item.item_type !== 'index' && !plainText && sections.length > 0 && (
          <div className="space-y-4">
            {sections.filter((sec: any) => sec.body).map((sec: any, i: number) => (
              <div key={i} className="bg-white rounded-xl border border-gray-200 p-6 shadow-sm">
                {sec.heading && <h3 className="text-base font-semibold text-gray-900 mb-3">{sec.heading}</h3>}
                <p className="text-sm text-gray-600 leading-relaxed whitespace-pre-line">{sec.body}</p>
              </div>
            ))}
          </div>
        )}

        {entries.length > 0 && (
          <div className="space-y-2">
            <p className="text-sm text-gray-500 mb-2">{entries.length} entries — hover to preview, click to expand</p>
            {entries.map((entry: any, i: number) => (
              <EntryCard key={i} entry={entry} />
            ))}
          </div>
        )}

        {tocEntries.length > 0 && (
          <div className="bg-white rounded-xl border border-gray-200 p-6 shadow-sm">
            <h3 className="text-base font-semibold text-gray-900 mb-3">Table of Contents ({tocEntries.length} items)</h3>
            <ul className="space-y-1">
              {tocEntries.map((entry: any, i: number) => (
                <li key={i} className="text-sm text-gray-600 py-1 border-b border-gray-50 last:border-0">
                  {typeof entry === 'string' ? entry : entry.title || JSON.stringify(entry)}
                </li>
              ))}
            </ul>
          </div>
        )}

        {!contentLoading && !plainText && sections.length === 0 && entries.length === 0 && tocEntries.length === 0 && !item.image_url && (
          <div className="bg-white rounded-xl border border-gray-200 p-8 shadow-sm text-center">
            <p className="text-gray-500">Content details not yet available for this item.</p>
          </div>
        )}
      </div>
    </div>
  )
}

/** Landing page — grid of top-level items */
const GenealogyPapersPage: React.FC = () => {
  const navigate = useNavigate()
  const [items, setItems] = useState<DigitalItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    apiService.getDigitalItemsBySource('genealogy-papers', undefined, 500)
      .then(res => {
        const sorted = [...res.items].sort((a, b) => (a.order ?? 99) - (b.order ?? 99))
        // First try items without a parent (true top-level hubs)
        let topLevel = sorted.filter(i => !i.parent_module_id)
        // If no top-level items exist, derive sections from unique parent_module_id values
        if (topLevel.length === 0 && sorted.length > 0) {
          const moduleIdSet = new Set(sorted.map(i => i.module_id).filter(Boolean))
          // Items whose parent is not in the dataset are effectively top-level
          topLevel = sorted.filter(i => i.parent_module_id && !moduleIdSet.has(i.parent_module_id))
          // If all parents are also in the dataset, group by parent_module_id instead
          if (topLevel.length === 0) {
            const seen = new Set<string>()
            topLevel = []
            for (const item of sorted) {
              const key = item.parent_module_id || item.module_id || item.id
              if (!seen.has(key)) {
                seen.add(key)
                topLevel.push({
                  ...item,
                  title: item.hub_label || item.title,
                  id: item.parent_id || item.id,
                  pattern: 'content_hub',
                } as DigitalItem)
              }
            }
          }
        }
        setItems(topLevel)
      })
      .catch(err => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div className="bg-gray-50 min-h-screen">
      <PageHeader
        title="Digitized Genealogy & Papers by the TRA"
        subtitle="Theodore Roosevelt Association Cyclopedia — speeches, public papers, genealogy, and encyclopedic index"
      />
      <div className="px-6 py-6">
        <button
          onClick={() => navigate('/digital-resources')}
          className="flex items-center gap-2 text-sm text-gray-600 hover:text-museum-accent mb-6 transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to Digital Resources
        </button>

        {loading && (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
            <span className="ml-2 text-gray-500">Loading...</span>
          </div>
        )}

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-xl p-4 mb-6">
            <p className="text-sm text-red-700">{error}</p>
          </div>
        )}

        {!loading && !error && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
            {items.map((item) => {
              const colors = ICON_COLORS[item.item_type || 'document'] || ICON_COLORS.document
              const count = item.entry_count || item.section_count || item.toc_entry_count
              const isHub = isHubPattern(item.pattern)
              const hasImage = !!item.image_url

              // Choose icon based on item type
              let SectionIcon = BookOpen
              if (item.item_type === 'speeches') SectionIcon = Volume2
              else if (item.item_type === 'family') SectionIcon = Users
              else if (item.item_type === 'genealogy') SectionIcon = Users
              else if (item.item_type === 'chronology') SectionIcon = FileText
              else if (item.item_type === 'papers') SectionIcon = FileText
              else if (item.item_type === 'index') SectionIcon = AlignLeft
              else if (hasImage) SectionIcon = ImageIcon

              return (
                <div
                  key={item.id}
                  onClick={() => navigate(`/digital-resources/genealogy-papers/${item.id}`)}
                  className="bg-white rounded-xl border border-gray-200 shadow-sm hover:shadow-lg transition-all duration-300 hover:-translate-y-1 cursor-pointer group overflow-hidden"
                >
                  {hasImage && (
                    <div className="h-40 bg-gray-100 overflow-hidden flex items-center justify-center">
                      <AuthenticatedImage
                        rawUrl={item.image_url}
                        alt={item.image_alt || item.title || ''}
                        className="max-w-full max-h-full object-contain group-hover:scale-105 transition-transform duration-300"
                      />
                    </div>
                  )}
                  <div className="p-6">
                    <div className="flex items-center justify-between mb-4">
                      <div className={`w-12 h-12 ${colors.bg} rounded-xl flex items-center justify-center`}>
                        <SectionIcon className={`w-6 h-6 ${colors.text}`} />
                      </div>
                      <ArrowRight className="w-5 h-5 text-gray-300 group-hover:text-gray-600 group-hover:translate-x-1 transition-all" />
                    </div>
                    <h3 className="text-base font-semibold text-gray-900 mb-1 group-hover:text-museum-accent transition-colors leading-snug">
                      {item.title || item.hub_label}
                    </h3>
                    {item.plain_text && !isHub && (
                      <p className="text-xs text-gray-500 leading-relaxed line-clamp-2 mt-1">
                        {item.plain_text.slice(0, 120)}
                      </p>
                    )}
                    {isHub && count !== undefined && count > 0 && (
                      <p className="text-xs text-gray-400 mt-2">{count} items</p>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

export default GenealogyPapersPage
