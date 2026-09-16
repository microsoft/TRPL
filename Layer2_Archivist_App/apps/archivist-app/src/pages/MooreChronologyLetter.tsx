// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import React, { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Mail, ArrowLeft, Loader2 } from 'lucide-react'
import PageHeader from '@/components/PageHeader'
import AuthenticatedImage from '@/components/AuthenticatedImage'
import { apiService, DigitalItem } from '@/services/api'

const MooreChronologyLetterPage: React.FC = () => {
  const navigate = useNavigate()
  const [items, setItems] = useState<DigitalItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [brokenThumbnails, setBrokenThumbnails] = useState<Set<string>>(new Set())

  useEffect(() => {
    apiService.getDigitalItemsBySource('moore-chronology', 'document')
      .then(res => setItems(res.items))
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div className="bg-gray-50">
      <PageHeader
        title="Moore Chronology – Letters"
        subtitle="Correspondence related to the Moore Chronology"
      />

      <div className="px-6 py-6">
        {/* Back navigation */}
        <button
          onClick={() => navigate('/digital-resources/moore-chronology')}
          className="flex items-center gap-2 text-sm text-gray-600 hover:text-museum-accent mb-6 transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to Moore Chronology
        </button>

        {loading && (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
            <span className="ml-2 text-gray-500">Loading letters...</span>
          </div>
        )}

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-xl p-4 mb-6">
            <p className="text-sm text-red-700">{error}</p>
          </div>
        )}

        {!loading && !error && items.length > 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {items.map((item) => {
              const thumbnail = item.thumbnail_url
              const showThumbnail = Boolean(thumbnail) && !brokenThumbnails.has(item.id)

              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() =>
                    navigate(`/digital-resources/moore-chronology/letter/${item.id}`)
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
                        <Mail className="w-10 h-10 text-indigo-500" />
                      </div>
                    )}
                  </div>
                  <div className="p-4">
                    <h3 className="text-sm font-semibold text-gray-800 group-hover:text-museum-accent transition-colors line-clamp-2">
                      {item.title}
                    </h3>
                    {item.date_range && (
                      <p className="text-xs text-gray-500 mt-1">{item.date_range}</p>
                    )}
                    {item.files && item.files.length > 0 && (
                      <p className="text-xs text-gray-400 mt-1">
                        {item.files.length} {item.files.length === 1 ? 'file' : 'files'}
                      </p>
                    )}
                  </div>
                </button>
              )
            })}
          </div>
        )}

        {!loading && !error && items.length === 0 && (
          <div className="bg-white border border-gray-200 rounded-xl p-4">
            <p className="text-sm text-gray-600">No letter items found.</p>
          </div>
        )}
      </div>
    </div>
  )
}

export default MooreChronologyLetterPage
