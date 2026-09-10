import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  ChevronLeft,
  RefreshCw,
  FileText,
  ScanText,
  Brain,
  Clock,
  ArrowUpDown,
  ArrowRight,
  Filter,
  AlertTriangle,
} from 'lucide-react'
import {
  apiService,
  CollectionOcrReport as OcrReportData,
  ResourceTypeOcrStats,
} from '@/services/api'
import PageHeader from '@/components/PageHeader'
import LoadingSpinner from '@/components/LoadingSpinner'
import Toast from '@/components/Toast'

/* ───── Helpers ───── */

type AccuracyTier = 'excellent' | 'good' | 'warning' | 'critical' | 'none'

function tierOf(value: number | null): AccuracyTier {
  if (value === null) return 'none'
  if (value >= 90) return 'excellent'
  if (value >= 75) return 'good'
  if (value >= 50) return 'warning'
  return 'critical'
}

const TIER_META: Record<
  AccuracyTier,
  { label: string; text: string; bg: string; bar: string; dot: string; solid: string }
> = {
  excellent: {
    label: 'Excellent',
    text: 'text-green-700',
    bg: 'bg-green-50 border-green-200',
    bar: 'bg-green-500',
    dot: 'bg-green-500',
    solid: '#22c55e',
  },
  good: {
    label: 'Good',
    text: 'text-yellow-700',
    bg: 'bg-yellow-50 border-yellow-200',
    bar: 'bg-yellow-500',
    dot: 'bg-yellow-500',
    solid: '#eab308',
  },
  warning: {
    label: 'Warning',
    text: 'text-orange-700',
    bg: 'bg-orange-50 border-orange-200',
    bar: 'bg-orange-500',
    dot: 'bg-orange-500',
    solid: '#f97316',
  },
  critical: {
    label: 'Critical',
    text: 'text-red-700',
    bg: 'bg-red-50 border-red-200',
    bar: 'bg-red-500',
    dot: 'bg-red-500',
    solid: '#ef4444',
  },
  none: {
    label: 'No data',
    text: 'text-gray-500',
    bg: 'bg-gray-50 border-gray-200',
    bar: 'bg-gray-300',
    dot: 'bg-gray-300',
    solid: '#d1d5db',
  },
}

/* Distinct palette for resource types in charts */
const PALETTE = [
  '#6366f1', '#8b5cf6', '#ec4899', '#f43f5e',
  '#f97316', '#eab308', '#22c55e', '#14b8a6',
  '#06b6d4', '#0ea5e9', '#3b82f6', '#a855f7',
  '#d946ef', '#f59e0b', '#84cc16', '#10b981',
]

/* ───── Page ───── */

type SortKey = 'count' | 'ocr' | 'metadata' | 'name'
type TierFilter = 'all' | AccuracyTier

const CollectionOcrReportPage: React.FC = () => {
  const { repository, collectionName } = useParams<{
    repository: string
    collectionName: string
  }>()
  const navigate = useNavigate()

  const [report, setReport] = useState<OcrReportData | null>(null)
  const [loading, setLoading] = useState(true)
  const [rebuilding, setRebuilding] = useState(false)

  const [toast, setToast] = useState<{
    type: 'success' | 'error' | 'info'
    message: string
  } | null>(null)

  /* Filters */
  const [resourceTypeFilter, setResourceTypeFilter] = useState<string>('all')
  const [sortKey, setSortKey] = useState<SortKey>('count')
  const [ocrTierFilter, setOcrTierFilter] = useState<TierFilter>('all')
  const [metaTierFilter, setMetaTierFilter] = useState<TierFilter>('all')

  /* Selection for cross-highlighting with the pie chart */
  const [selectedResourceType, setSelectedResourceType] = useState<string | null>(null)
  const chartsRef = useRef<HTMLDivElement>(null)

  const decodedRepo = repository ? decodeURIComponent(repository) : ''
  const decodedCollection = collectionName
    ? decodeURIComponent(collectionName)
    : ''

  const fetchReport = useCallback(async () => {
    if (!decodedRepo || !decodedCollection) return
    setLoading(true)
    try {
      const data = await apiService.getCollectionOcrReport(
        decodedRepo,
        decodedCollection
      )
      setReport(data)
    } catch {
      setToast({ type: 'error', message: 'Failed to load OCR report' })
    } finally {
      setLoading(false)
    }
  }, [decodedRepo, decodedCollection])

  useEffect(() => {
    fetchReport()
  }, [fetchReport])

  const handleRebuild = async () => {
    setRebuilding(true)
    try {
      await apiService.rebuildCollectionOcrReport(
        decodedRepo,
        decodedCollection
      )
      await fetchReport()
      setToast({ type: 'success', message: 'OCR report rebuilt successfully' })
    } catch {
      setToast({ type: 'error', message: 'Failed to rebuild OCR report' })
    } finally {
      setRebuilding(false)
    }
  }

  /* All resource type names for the dropdown */
  const allResourceTypes = useMemo(() => {
    if (!report) return [] as string[]
    return [...report.byResourceType]
      .map((r) => r.resourceType)
      .sort((a, b) => a.localeCompare(b))
  }, [report])

  /* Derived: filtered + sorted resource types */
  const filteredTypes = useMemo(() => {
    if (!report) return [] as ResourceTypeOcrStats[]
    let list = report.byResourceType

    if (resourceTypeFilter !== 'all') {
      list = list.filter((r) => r.resourceType === resourceTypeFilter)
    }

    if (ocrTierFilter !== 'all') {
      list = list.filter((r) => tierOf(r.avgOcrAccuracy) === ocrTierFilter)
    }

    if (metaTierFilter !== 'all') {
      list = list.filter((r) => tierOf(r.avgMetadataAccuracy) === metaTierFilter)
    }

    const sorted = [...list]
    switch (sortKey) {
      case 'count':
        sorted.sort((a, b) => b.documentCount - a.documentCount)
        break
      case 'ocr':
        sorted.sort((a, b) => (b.avgOcrAccuracy ?? -1) - (a.avgOcrAccuracy ?? -1))
        break
      case 'metadata':
        sorted.sort(
          (a, b) =>
            (b.avgMetadataAccuracy ?? -1) - (a.avgMetadataAccuracy ?? -1)
        )
        break
      case 'name':
        sorted.sort((a, b) => a.resourceType.localeCompare(b.resourceType))
        break
    }
    return sorted
  }, [report, resourceTypeFilter, ocrTierFilter, metaTierFilter, sortKey])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-[60vh]">
        <LoadingSpinner />
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full bg-gradient-to-br from-slate-50 via-gray-50 to-slate-100">
      <PageHeader
        title={
          <span className="flex items-center gap-2">
            <ScanText className="w-6 h-6" />
            OCR Accuracy Report
          </span>
        }
        subtitle={`${decodedRepo} / ${decodedCollection}`}
      />

      <div className="flex-1 overflow-auto px-8 py-6" onClick={() => setSelectedResourceType(null)}>
        <div className="w-full space-y-6">
          {/* Top bar: back + rebuild */}
          <div className="flex items-center justify-between">
            <button
              onClick={() =>
                navigate(
                  `/repositories/${encodeURIComponent(decodedRepo)}/collections`
                )
              }
              className="flex items-center gap-1.5 text-sm font-medium text-gray-600 hover:text-museum-accent transition-colors"
            >
              <ChevronLeft className="w-4 h-4" />
              Back to {decodedRepo}
            </button>

            <div className="flex items-center gap-3">
              {report?.lastUpdated && (
                <span className="flex items-center gap-1.5 text-xs text-gray-500">
                  <Clock className="w-3.5 h-3.5" />
                  Updated{' '}
                  {new Date(report.lastUpdated).toLocaleString(undefined, {
                    month: 'short',
                    day: 'numeric',
                    hour: '2-digit',
                    minute: '2-digit',
                  })}
                </span>
              )}
              <button
                onClick={handleRebuild}
                disabled={rebuilding}
                className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg bg-museum-accent text-white hover:bg-museum-accent/90 disabled:opacity-50 shadow-sm transition-colors"
              >
                <RefreshCw
                  className={`w-4 h-4 ${rebuilding ? 'animate-spin' : ''}`}
                />
                {rebuilding ? 'Rebuilding...' : 'Rebuild Report'}
              </button>
            </div>
          </div>

          {!report ? (
            <EmptyState />
          ) : (
            <>
              {/* Scroll anchor — above KPI so chart headings are visible */}
              <div ref={chartsRef} />

              {/* KPI row */}
              <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                <KpiCard
                  icon={<FileText className="w-5 h-5" />}
                  iconBg="bg-slate-100 text-slate-600"
                  label="Total Documents"
                  value={report.totalDocuments.toLocaleString()}
                  sub={`${report.byResourceType.length} resource type${report.byResourceType.length !== 1 ? 's' : ''}`}
                />
                <KpiCard
                  icon={<AlertTriangle className="w-5 h-5" />}
                  iconBg="bg-red-100 text-red-600"
                  label="Errors"
                  value={(report.errorCount ?? 0).toLocaleString()}
                  sub={`Actual: ${(report.totalDocuments - (report.errorCount ?? 0)).toLocaleString()} documents`}
                />
                <KpiCard
                  icon={<ScanText className="w-5 h-5" />}
                  iconBg="bg-blue-100 text-blue-600"
                  label="Overall OCR Accuracy"
                  value={
                    report.overallOcrAccuracy !== null
                      ? `${report.overallOcrAccuracy}%`
                      : 'N/A'
                  }
                  tier={tierOf(report.overallOcrAccuracy)}
                  progress={report.overallOcrAccuracy}
                />
                <KpiCard
                  icon={<Brain className="w-5 h-5" />}
                  iconBg="bg-purple-100 text-purple-600"
                  label="Metadata Accuracy"
                  value={
                    report.overallMetadataAccuracy !== null
                      ? `${report.overallMetadataAccuracy}%`
                      : 'N/A'
                  }
                  tier={tierOf(report.overallMetadataAccuracy)}
                  progress={report.overallMetadataAccuracy}
                />
              </div>

              {/* Charts row */}
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
                  <div className="flex items-center gap-2 mb-1">
                    <div className="w-8 h-8 rounded-lg flex items-center justify-center bg-blue-100 text-blue-600">
                      <ScanText className="w-4 h-4" />
                    </div>
                    <h3 className="text-base font-semibold text-gray-900">
                      OCR Accuracy by Resource Type
                    </h3>
                  </div>
                  <AccuracyPieChart
                    data={report.byResourceType}
                    field="avgOcrAccuracy"
                    selected={selectedResourceType}
                  />
                </div>

                <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
                  <div className="flex items-center gap-2 mb-1">
                    <div className="w-8 h-8 rounded-lg flex items-center justify-center bg-purple-100 text-purple-600">
                      <Brain className="w-4 h-4" />
                    </div>
                    <h3 className="text-base font-semibold text-gray-900">
                      Metadata Accuracy by Resource Type
                    </h3>
                  </div>
                  <AccuracyPieChart
                    data={report.byResourceType}
                    field="avgMetadataAccuracy"
                    selected={selectedResourceType}
                  />
                </div>
              </div>

              {/* Filters + table */}
              <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
                <div className="px-6 py-4 border-b border-gray-100 flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h2 className="text-lg font-semibold text-gray-900">
                      Accuracy by Resource Type
                    </h2>
                    <p className="text-xs text-gray-500">
                      Showing {filteredTypes.length} of{' '}
                      {report.byResourceType.length} resource types
                    </p>
                  </div>

                  <div className="flex flex-wrap items-center gap-2">
                    <div className="flex items-center gap-1.5 text-sm">
                      <Filter className="w-4 h-4 text-gray-400" />
                      <select
                        value={resourceTypeFilter}
                        onChange={(e) =>
                          setResourceTypeFilter(e.target.value)
                        }
                        className="px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-museum-accent/30 focus:border-museum-accent bg-white max-w-[200px]"
                      >
                        <option value="all">All resource types</option>
                        {allResourceTypes.map((rt) => (
                          <option key={rt} value={rt}>
                            {rt}
                          </option>
                        ))}
                      </select>
                    </div>

                    <div className="flex items-center gap-1.5 text-sm">
                      <ScanText className="w-4 h-4 text-blue-500" />
                      <select
                        value={ocrTierFilter}
                        onChange={(e) =>
                          setOcrTierFilter(e.target.value as TierFilter)
                        }
                        className="px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-museum-accent/30 focus:border-museum-accent bg-white"
                      >
                        <option value="all">OCR: All</option>
                        <option value="excellent">OCR: Excellent (≥90%)</option>
                        <option value="good">OCR: Good (75–89%)</option>
                        <option value="warning">OCR: Warning (50–74%)</option>
                        <option value="critical">OCR: Critical (&lt;50%)</option>
                        <option value="none">OCR: No data</option>
                      </select>
                    </div>

                    <div className="flex items-center gap-1.5 text-sm">
                      <Brain className="w-4 h-4 text-purple-500" />
                      <select
                        value={metaTierFilter}
                        onChange={(e) =>
                          setMetaTierFilter(e.target.value as TierFilter)
                        }
                        className="px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-museum-accent/30 focus:border-museum-accent bg-white"
                      >
                        <option value="all">Metadata: All</option>
                        <option value="excellent">Metadata: Excellent (≥90%)</option>
                        <option value="good">Metadata: Good (75–89%)</option>
                        <option value="warning">Metadata: Warning (50–74%)</option>
                        <option value="critical">Metadata: Critical (&lt;50%)</option>
                        <option value="none">Metadata: No data</option>
                      </select>
                    </div>

                    <div className="flex items-center gap-1.5 text-sm">
                      <ArrowUpDown className="w-4 h-4 text-gray-400" />
                      <select
                        value={sortKey}
                        onChange={(e) => setSortKey(e.target.value as SortKey)}
                        className="px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-museum-accent/30 focus:border-museum-accent bg-white"
                      >
                        <option value="count">Sort: Most documents</option>
                        <option value="ocr">Sort: Best OCR</option>
                        <option value="metadata">Sort: Best metadata</option>
                        <option value="name">Sort: Name (A–Z)</option>
                      </select>
                    </div>
                  </div>
                </div>

                {filteredTypes.length === 0 ? (
                  <div className="p-12 text-center text-gray-400">
                    No resource types match your filters
                  </div>
                ) : (
                  <div>
                    <div
                      className="grid gap-3 px-6 py-3 bg-gray-50 text-xs font-semibold text-gray-500 uppercase tracking-wider border-b border-gray-100"
                      style={{ gridTemplateColumns: '3fr 2fr 3fr 3fr auto', paddingLeft: 'calc(1.5rem + 4px)' }}
                    >
                      <div>Resource Type</div>
                      <div className="text-center">Documents</div>
                      <div className="text-center">OCR Accuracy</div>
                      <div className="text-center">Metadata Accuracy</div>
                      <div className="w-[28px]"></div>
                    </div>
                    <div className="divide-y divide-gray-50">

                    {filteredTypes.map((rt, idx) => (
                      <ResourceTypeRow
                        key={rt.resourceType}
                        data={rt}
                        total={report.totalDocuments - (report.errorCount ?? 0)}
                        colorIndex={idx}
                        isSelected={selectedResourceType === rt.resourceType}
                        onSelect={() => {
                          setSelectedResourceType((prev) =>
                            prev === rt.resourceType ? null : rt.resourceType
                          )
                          chartsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                        }}
                        onNavigate={() => {
                          const stored = sessionStorage.getItem('collectionFilters')
                          const filters = stored ? JSON.parse(stored) : {}
                          filters.typeFilter = rt.resourceType
                          filters.repository = decodedRepo
                          filters.collectionName = decodedCollection
                          sessionStorage.setItem('collectionFilters', JSON.stringify(filters))
                          navigate(
                            `/repositories/${encodeURIComponent(decodedRepo)}/collections/${encodeURIComponent(decodedCollection)}`
                          )
                        }}
                      />
                    ))}
                    </div>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      {toast && (
        <Toast
          type={toast.type}
          message={toast.message}
          onClose={() => setToast(null)}
        />
      )}
    </div>
  )
}

/* ───── Subcomponents ───── */

function EmptyState() {
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-16 text-center">
      <FileText className="w-14 h-14 text-gray-300 mx-auto mb-4" />
      <p className="text-lg font-medium text-gray-700 mb-1">
        No OCR report available
      </p>
      <p className="text-sm text-gray-500">
        Use the Statistics Admin page to rebuild OCR reports.
      </p>
    </div>
  )
}

function KpiCard({
  icon,
  iconBg,
  label,
  value,
  sub,
  tier,
  progress,
}: {
  icon: React.ReactNode
  iconBg: string
  label: string
  value: string
  sub?: string
  tier?: AccuracyTier
  progress?: number | null
}) {
  const meta = tier ? TIER_META[tier] : null
  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5 hover:shadow-md transition-shadow">
      <div className="flex items-center justify-between mb-3">
        <div className={`w-9 h-9 rounded-lg flex items-center justify-center ${iconBg}`}>
          {icon}
        </div>
        {meta && tier !== 'none' && (
          <span
            className={`text-[10px] font-semibold uppercase tracking-wider px-2 py-1 rounded-full border ${meta.bg} ${meta.text}`}
          >
            {meta.label}
          </span>
        )}
      </div>
      <p className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-1">
        {label}
      </p>
      <p className="text-3xl font-bold text-gray-900">{value}</p>
      {sub && <p className="text-xs text-gray-400 mt-1">{sub}</p>}
      {progress !== undefined && progress !== null && (
        <div className="mt-3 h-1.5 bg-gray-100 rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${meta?.bar ?? 'bg-gray-300'}`}
            style={{ width: `${Math.min(100, Math.max(0, progress))}%` }}
          />
        </div>
      )}
    </div>
  )
}

/* Pie chart: accuracy per resource type */
function AccuracyPieChart({
  data,
  field,
  selected,
}: {
  data: ResourceTypeOcrStats[]
  field: 'avgOcrAccuracy' | 'avgMetadataAccuracy'
  selected: string | null
}) {
  const rows = [...data]
    .filter((d) => d[field] !== null && (d[field] as number) > 0)
    .sort((a, b) => (b[field] ?? 0) - (a[field] ?? 0))

  if (rows.length === 0) {
    return (
      <div className="h-60 flex items-center justify-center text-sm text-gray-400">
        No data available
      </div>
    )
  }

  const slices = rows.map((r, i) => ({
    key: r.resourceType,
    value: r[field] as number,
    color: PALETTE[i % PALETTE.length],
  }))

  const total = slices.reduce((s, x) => s + x.value, 0) || 1
  const size = 260
  const radius = 115
  const popOffset = 8
  const cx = size / 2
  const cy = size / 2

  let cumulative = 0
  const paths = slices.map((slice) => {
    const startAngle = (cumulative / total) * Math.PI * 2
    cumulative += slice.value
    const endAngle = (cumulative / total) * Math.PI * 2
    const largeArc = endAngle - startAngle > Math.PI ? 1 : 0
    const midAngle = (startAngle + endAngle) / 2

    const isSelected = selected === slice.key
    const offsetX = isSelected ? popOffset * Math.sin(midAngle) : 0
    const offsetY = isSelected ? -popOffset * Math.cos(midAngle) : 0

    const x1 = cx + offsetX + radius * Math.sin(startAngle)
    const y1 = cy + offsetY - radius * Math.cos(startAngle)
    const x2 = cx + offsetX + radius * Math.sin(endAngle)
    const y2 = cy + offsetY - radius * Math.cos(endAngle)

    const d = [
      `M ${cx + offsetX} ${cy + offsetY}`,
      `L ${x1} ${y1}`,
      `A ${radius} ${radius} 0 ${largeArc} 1 ${x2} ${y2}`,
      'Z',
    ].join(' ')

    return { d, color: slice.color, key: slice.key, value: slice.value, isSelected }
  })

  const overallAvg =
    rows.reduce((s, r) => s + (r[field] as number), 0) / rows.length
  const selectedSlice = paths.find((p) => p.isSelected)
  const hasSelection = !!selectedSlice

  return (
    <div className="flex flex-col items-center">
      <div
        className="relative flex-shrink-0"
        style={{ width: size, height: size }}
      >
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
          {paths.map((p, i) => (
            <path
              key={i}
              d={p.d}
              fill={p.color}
              stroke="white"
              strokeWidth={p.isSelected ? 3 : 2}
              style={{
                opacity: hasSelection && !p.isSelected ? 0.25 : 1,
                transition: 'opacity 200ms, transform 200ms',
              }}
            >
              <title>{`${p.key}: ${p.value.toFixed(1)}%`}</title>
            </path>
          ))}
        </svg>
      </div>

      {/* Info pill below the pie — only visible when a resource type is selected */}
      {selectedSlice && (
        <div className="mt-4 px-4 py-2 rounded-full border border-gray-200 bg-white shadow-sm text-center min-w-[180px]">
          <div className="flex items-center justify-center gap-2">
            <span
              className="w-3 h-3 rounded-sm flex-shrink-0"
              style={{ backgroundColor: selectedSlice.color }}
            />
            <span className="text-xs font-medium text-gray-700 truncate max-w-[160px]">
              {selectedSlice.key}
            </span>
            <span className="text-sm font-bold text-gray-900 tabular-nums">
              {selectedSlice.value.toFixed(1)}%
            </span>
          </div>
        </div>
      )}
    </div>
  )
}

function ResourceTypeRow({
  data,
  total,
  colorIndex,
  isSelected,
  onSelect,
  onNavigate,
}: {
  data: ResourceTypeOcrStats
  total: number
  colorIndex: number
  isSelected: boolean
  onSelect: () => void
  onNavigate: () => void
}) {
  const pct = total > 0 ? ((data.documentCount / total) * 100).toFixed(1) : '0'
  const ocrTier = tierOf(data.avgOcrAccuracy)
  const metaTier = tierOf(data.avgMetadataAccuracy)
  const color = PALETTE[colorIndex % PALETTE.length]

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={(e) => { e.stopPropagation(); onSelect() }}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onSelect()
        }
      }}
      className={`grid grid-cols-13 gap-3 px-6 py-3 items-center cursor-pointer transition-colors border-l-4 ${
        isSelected
          ? 'bg-indigo-50 border-l-indigo-500'
          : 'border-l-transparent hover:bg-gray-50'
      }`}
      style={{ gridTemplateColumns: '3fr 2fr 3fr 3fr auto' }}
    >
      <div className="flex items-center gap-2.5 min-w-0">
        <span
          className="w-2 h-6 rounded-sm flex-shrink-0"
          style={{ backgroundColor: color }}
        />
        <span
          className={`text-sm truncate ${
            isSelected ? 'font-semibold text-indigo-900' : 'font-medium text-gray-900'
          }`}
          title={data.resourceType}
        >
          {data.resourceType}
        </span>
      </div>

      <div className="text-center">
        <div className="text-sm font-semibold text-gray-900">
          {data.documentCount.toLocaleString()}
        </div>
        <div className="text-[11px] text-gray-400">{pct}% of total</div>
      </div>

      <div className="flex justify-center">
        <AccuracyBar value={data.avgOcrAccuracy} tier={ocrTier} />
      </div>

      <div className="flex justify-center">
        <AccuracyBar value={data.avgMetadataAccuracy} tier={metaTier} />
      </div>

      <div className="flex justify-center">
        <button
          onClick={(e) => {
            e.stopPropagation()
            onNavigate()
          }}
          title="View documents for this resource type"
          className="p-1.5 rounded-md hover:bg-gray-200 transition-colors"
        >
          <ArrowRight className="w-4 h-4 text-gray-900" />
        </button>
      </div>
    </div>
  )
}

function AccuracyBar({
  value,
  tier,
}: {
  value: number | null
  tier: AccuracyTier
}) {
  const meta = TIER_META[tier]
  return (
    <div className="flex items-center gap-2 w-full max-w-[220px]">
      <div className="flex-1 h-1.5 bg-gray-100 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${meta.bar}`}
          style={{
            width: value !== null ? `${Math.min(100, Math.max(0, value))}%` : '0%',
          }}
        />
      </div>
      <span className={`text-xs font-semibold tabular-nums w-10 text-right ${meta.text}`}>
        {value !== null ? `${value}%` : '—'}
      </span>
    </div>
  )
}

export default CollectionOcrReportPage
