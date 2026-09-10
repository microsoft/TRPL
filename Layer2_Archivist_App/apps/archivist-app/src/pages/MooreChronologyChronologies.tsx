import React, { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Calendar, ArrowLeft, Loader2 } from 'lucide-react'
import PageHeader from '@/components/PageHeader'
import AuthenticatedImage from '@/components/AuthenticatedImage'
import { apiService, DigitalItem } from '@/services/api'

const MooreChronologyChronologiesPage: React.FC = () => {
  const navigate = useNavigate()
  const [items, setItems] = useState<DigitalItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [brokenThumbnails, setBrokenThumbnails] = useState<Set<string>>(new Set())

  useEffect(() => {
    apiService
      .getDigitalItemsBySource('moore-chronology', 'chronology')
      .then((res) => {
        const sorted = [...res.items].sort((a, b) => {
          const yearA = parseInt((a.date_range || '').match(/\d{4}/)?.[0] || '9999', 10)
          const yearB = parseInt((b.date_range || '').match(/\d{4}/)?.[0] || '9999', 10)
          return yearA - yearB
        })
        setItems(sorted)
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  const showSpinner = loading

  return (
    <div className="bg-gray-50">
      <PageHeader
        title="Moore Chronologies"
        subtitle="Ten chronological volumes documenting Theodore Roosevelt's daily life"
      />

      <div className="px-6 py-6">
        {showSpinner && (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
            <span className="ml-2 text-gray-500">Loading chronologies...</span>
          </div>
        )}

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-xl p-4 mb-6">
            <p className="text-sm text-red-700">{error}</p>
          </div>
        )}

        {!showSpinner && !error && (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {items.map((item) => {
              const thumbnail =
                item.thumbnail_url ||
                item.files?.find((f) => /\.(jpg|jpeg|png|webp)$/i.test(f.filename || ''))?.url
              const showThumbnail = Boolean(thumbnail) && !brokenThumbnails.has(item.id)

              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() =>
                    navigate(`/digital-resources/moore-chronology/chronologies/${item.id}`)
                  }
                  className="bg-white rounded-xl border border-gray-200 overflow-hidden shadow-sm hover:shadow-lg transition-all duration-300 hover:-translate-y-0.5 group text-left"
                >
                  <div className="aspect-[3/2] bg-gray-100 overflow-hidden">
                    {showThumbnail ? (
                      <AuthenticatedImage
                        rawUrl={thumbnail}
                        alt={item.title}
                        className="w-full h-full object-contain group-hover:scale-[1.02] transition-transform duration-300"
                        onError={() => {
                          setBrokenThumbnails((prev) => {
                            const next = new Set(prev)
                            next.add(item.id)
                            return next
                          })
                        }}
                      />
                    ) : (
                      <div className="w-full h-full flex items-center justify-center">
                        <Calendar className="w-10 h-10 text-amber-500" />
                      </div>
                    )}
                  </div>
                  <div className="p-4">
                    <h3 className="text-sm font-semibold text-gray-900 group-hover:text-museum-accent transition-colors line-clamp-2">
                      {item.title}
                    </h3>
                    {item.metadata?.collection && (
                      <p className="text-xs text-gray-500 mt-1">{item.metadata.collection}</p>
                    )}
                    <div className="mt-2 flex items-center flex-wrap gap-x-3 gap-y-1">
                      {item.date_range && (
                        <span className="text-xs text-gray-400 font-medium">{item.date_range}</span>
                      )}
                      {item.metadata?.creators && (
                        <span className="text-xs text-gray-400">
                          {item.metadata.creators}
                        </span>
                      )}
                      {item.ocr_accuracy != null && (
                        <span className={`text-xs font-medium px-1.5 py-0.5 rounded ${item.ocr_accuracy >= 0.9 ? 'bg-green-100 text-green-700' : item.ocr_accuracy >= 0.7 ? 'bg-yellow-100 text-yellow-700' : 'bg-red-100 text-red-700'}`}>
                          OCR {(item.ocr_accuracy * 100).toFixed(1)}%
                        </span>
                      )}
                    </div>
                  </div>
                </button>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

export default MooreChronologyChronologiesPage
