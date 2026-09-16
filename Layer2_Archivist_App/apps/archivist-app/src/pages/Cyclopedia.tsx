// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import React, { useEffect, useState, useRef } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, ArrowRight, BookOpen, Loader2, Upload, CloudOff, CheckCircle2, XCircle } from 'lucide-react'
import PageHeader from '@/components/PageHeader'
import AuthenticatedImage from '@/components/AuthenticatedImage'
import { apiService, DigitalItem } from '@/services/api'
import { useAuth } from '@/contexts/AuthContext'

type LinkedTopic = NonNullable<DigitalItem['linked_topics']>[number]

/** Renders text with linked topic terms highlighted; shows hover card on hover */
const RichText: React.FC<{ text: string; topics: LinkedTopic[] }> = ({ text, topics }) => {
  const [activeTopic, setActiveTopic] = useState<LinkedTopic | null>(null)
  const [pos, setPos] = useState<{ top: number; left: number }>({ top: 0, left: 0 })
  const containerRef = useRef<HTMLDivElement>(null)
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  if (!topics || topics.length === 0) {
    return <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-line">{text}</p>
  }

  const sortedTopics = [...topics].sort((a, b) => b.display.length - a.display.length)
  const topicMap = new Map<string, LinkedTopic>()
  sortedTopics.forEach(t => topicMap.set(t.display.toLowerCase(), t))

  const escapedTerms = sortedTopics.map(t => t.display.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  const regex = new RegExp(`(${escapedTerms.join('|')})`, 'gi')
  const parts = text.split(regex)

  const handleMouseEnter = (e: React.MouseEvent, topic: LinkedTopic) => {
    if (timeoutRef.current) clearTimeout(timeoutRef.current)
    const rect = (e.target as HTMLElement).getBoundingClientRect()
    const containerRect = containerRef.current?.getBoundingClientRect() || { top: 0, left: 0 }
    setPos({
      top: rect.bottom - containerRect.top + 4,
      left: Math.min(rect.left - containerRect.left, 300),
    })
    setActiveTopic(topic)
  }

  const handleMouseLeave = () => {
    timeoutRef.current = setTimeout(() => setActiveTopic(null), 200)
  }

  return (
    <div ref={containerRef} className="relative">
      <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-line">
        {parts.map((part, i) => {
          const topic = topicMap.get(part.toLowerCase())
          if (topic) {
            return (
              <span
                key={i}
                className="text-blue-700 font-medium border-b border-dotted border-blue-400 cursor-help"
                onMouseEnter={(e) => handleMouseEnter(e, topic)}
                onMouseLeave={handleMouseLeave}
              >
                {part}
              </span>
            )
          }
          return <React.Fragment key={i}>{part}</React.Fragment>
        })}
      </p>
      {activeTopic && (
        <div
          className="absolute z-50 w-96 bg-white border border-gray-200 rounded-xl shadow-xl p-4 max-h-64 overflow-y-auto"
          style={{ top: pos.top, left: pos.left }}
          onMouseEnter={() => { if (timeoutRef.current) clearTimeout(timeoutRef.current) }}
          onMouseLeave={handleMouseLeave}
        >
          <div className="flex gap-3">
            {activeTopic.thumbnail_url && (
              <AuthenticatedImage
                rawUrl={activeTopic.thumbnail_url}
                alt={activeTopic.title}
                className="w-16 h-16 rounded-lg object-cover shrink-0"
              />
            )}
            <div className="min-w-0">
              <h4 className="text-sm font-semibold text-gray-900 mb-1">{activeTopic.title}</h4>
              <p className="text-xs text-gray-600 leading-relaxed">{activeTopic.summary}</p>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

type BlobContent = { plain_text: string; linked_topics: NonNullable<DigitalItem['linked_topics']> }

/** Detail view for a single section */
const CyclopediaDetail: React.FC = () => {
  const { itemId } = useParams<{ itemId: string }>()
  const navigate = useNavigate()
  const { isAdmin } = useAuth()
  const [item, setItem] = useState<DigitalItem | null>(null)
  const [loading, setLoading] = useState(true)
  const [contentLoading, setContentLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [blobContent, setBlobContent] = useState<BlobContent | null>(null)
  const [publishing, setPublishing] = useState(false)
  const [unpublishing, setUnpublishing] = useState(false)
  const [publishMsg, setPublishMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  useEffect(() => {
    if (!itemId) return
    apiService.getDigitalItem(itemId, 'tr-cyclopedia')
      .then(res => setItem(res))
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

  const handlePublish = async () => {
    if (!itemId) return
    setPublishMsg(null)
    setPublishing(true)
    try {
      const result = await apiService.publishDigitalItem(itemId, 'tr-cyclopedia')
      setPublishMsg({ type: 'success', text: result.message })
      if (item) setItem({ ...item, publish_status: 'published', published_at: result.published_at })
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
      const result = await apiService.unpublishDigitalItem(itemId, 'tr-cyclopedia')
      setPublishMsg({ type: 'success', text: result.message })
      if (item) {
        const updated = { ...item, publish_status: 'unpublished' } as DigitalItem
        delete (updated as any).published_at
        setItem(updated)
      }
    } catch (e: unknown) {
      setPublishMsg({ type: 'error', text: e instanceof Error ? e.message : 'Unpublish failed' })
    } finally {
      setUnpublishing(false)
    }
  }

  return (
    <div className="bg-gray-50 min-h-screen">
      <PageHeader
        title={item?.title || 'Loading...'}
        subtitle="Theodore Roosevelt Cyclopedia"
      />
      <div className="px-6 py-6 max-w-4xl mx-auto">
        <button
          onClick={() => navigate('/digital-resources/cyclopedia')}
          className="flex items-center gap-2 text-sm text-gray-600 hover:text-museum-accent mb-6 transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to Cyclopedia
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

        {!loading && !error && item && (
          <article className="bg-white rounded-xl border border-gray-200 p-8 shadow-sm">
            <div className="flex items-center gap-3 mb-6">
              <div className="w-12 h-12 bg-blue-100 rounded-xl flex items-center justify-center">
                <BookOpen className="w-6 h-6 text-blue-600" />
              </div>
              <h2 className="text-xl font-semibold text-gray-900">{item.title}</h2>
            </div>
            {contentLoading && (
              <div className="flex items-center gap-2 py-4 text-gray-400">
                <Loader2 className="w-4 h-4 animate-spin" />
                <span className="text-sm">Loading content...</span>
              </div>
            )}
            {!contentLoading && (() => {
              const text = blobContent?.plain_text || item.plain_text
              const topics = blobContent?.linked_topics || item.linked_topics || []
              return text ? <RichText text={text} topics={topics} /> : null
            })()}

            {/* Publish / Unpublish (admin only) */}
            {isAdmin && (
              <div className="mt-8 pt-6 border-t border-gray-100">
                {publishMsg && (
                  <div className={`mb-4 p-3 rounded-lg text-sm flex items-center gap-2 ${publishMsg.type === 'success' ? 'bg-green-50 border border-green-200 text-green-700' : 'bg-red-50 border border-red-200 text-red-700'}`}>
                    {publishMsg.type === 'success' ? <CheckCircle2 className="w-4 h-4 shrink-0" /> : <XCircle className="w-4 h-4 shrink-0" />}
                    {publishMsg.text}
                  </div>
                )}
                <div className="flex items-center gap-3">
                  <button
                    onClick={handlePublish}
                    disabled={publishing || unpublishing}
                    className="inline-flex items-center gap-1.5 px-4 py-2 bg-green-600 text-white text-sm font-medium rounded-lg hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    {publishing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
                    Publish to Search
                  </button>
                  <button
                    onClick={handleUnpublish}
                    disabled={publishing || unpublishing}
                    className="inline-flex items-center gap-1.5 px-4 py-2 bg-gray-600 text-white text-sm font-medium rounded-lg hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    {unpublishing ? <Loader2 className="w-4 h-4 animate-spin" /> : <CloudOff className="w-4 h-4" />}
                    Unpublish
                  </button>
                  {item.publish_status === 'published' && (
                    <span className="text-xs text-green-600 font-medium">✓ Published</span>
                  )}
                </div>
              </div>
            )}
          </article>
        )}
      </div>
    </div>
  )
}

/** Listing page showing all sections as cards */
const CyclopediaPage: React.FC = () => {
  const navigate = useNavigate()
  const [items, setItems] = useState<DigitalItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    apiService.getDigitalItemsBySource('tr-cyclopedia')
      .then(res => {
        const sorted = [...res.items].sort((a, b) => (a.order ?? 99) - (b.order ?? 99))
        setItems(sorted)
      })
      .catch(err => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div className="bg-gray-50 min-h-screen">
      <PageHeader
        title="Theodore Roosevelt Cyclopedia"
        subtitle="A comprehensive collection of the significant sayings, conversations, and writings of Theodore Roosevelt"
      />
      <div className="px-6 py-6">
        <button
          onClick={() => navigate('/digital-resources')}
          className="flex items-center gap-2 text-sm text-gray-600 hover:text-museum-accent mb-6 transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to Public Resources
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
              // Use internalized image_url (set during ingestion) for the card thumbnail;
              // fall back to the first linked_topics thumbnail for older items.
              const thumb = item.image_url || item.linked_topics?.find(t => t.thumbnail_url)?.thumbnail_url
              const preview = item.description || item.plain_text?.slice(0, 150)
              return (
                <div
                  key={item.id}
                  onClick={() => navigate(`/digital-resources/cyclopedia/${item.id}`)}
                  className="bg-white rounded-xl border border-gray-200 shadow-sm hover:shadow-lg transition-all duration-300 hover:-translate-y-1 cursor-pointer group overflow-hidden"
                >
                  {thumb && (
                    <div className="h-36 bg-gray-100 overflow-hidden flex items-center justify-center">
                      <AuthenticatedImage
                        rawUrl={thumb}
                        alt=""
                        className="max-w-full max-h-full object-contain group-hover:scale-105 transition-transform duration-300"
                      />
                    </div>
                  )}
                  <div className="p-6">
                    <div className="flex items-center justify-between mb-4">
                      <div className="w-12 h-12 bg-blue-100 rounded-xl flex items-center justify-center">
                        <BookOpen className="w-6 h-6 text-blue-600" />
                      </div>
                      <ArrowRight className="w-5 h-5 text-gray-300 group-hover:text-gray-600 group-hover:translate-x-1 transition-all" />
                    </div>
                    <h3 className="text-base font-semibold text-gray-900 mb-2 group-hover:text-museum-accent transition-colors">
                      {item.title}
                    </h3>
                    {preview && (
                      <p className="text-xs text-gray-500 leading-relaxed line-clamp-3">
                        {preview}{preview.length >= 150 ? '...' : ''}
                      </p>
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

export { CyclopediaDetail }
export default CyclopediaPage
