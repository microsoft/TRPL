import React, { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Calendar, Mail, ArrowRight, ArrowLeft } from 'lucide-react'
import PageHeader from '@/components/PageHeader'
import { apiService } from '@/services/api'

const MooreChronologyPage: React.FC = () => {
  const navigate = useNavigate()
  const [letterCount, setLetterCount] = useState<number | null>(null)

  useEffect(() => {
    apiService
      .getDigitalItemsBySource('moore-chronology', 'document')
      .then((res) => setLetterCount(res.count))
      .catch(() => setLetterCount(0))
  }, [])

  const categories = [
    {
      id: 'chronologies',
      title: 'Chronologies',
      description: "Ten chronological volumes documenting Theodore Roosevelt's daily life from 1858 to 1919.",
      icon: Calendar,
      iconBg: 'bg-amber-100',
      iconColor: 'text-amber-600',
      count: 10,
      path: '/digital-resources/moore-chronology/chronologies',
    },
    {
      id: 'letter',
      title: 'Letter',
      description: 'Correspondence related to the Moore Chronology research.',
      icon: Mail,
      iconBg: 'bg-indigo-100',
      iconColor: 'text-indigo-600',
      count: letterCount,
      path: '/digital-resources/moore-chronology/letter',
    },
  ]

  return (
    <div className="bg-gray-50">
      <PageHeader
        title="Moore Chronology"
        subtitle="Detailed chronological record of Theodore Roosevelt's daily life"
      />

      <div className="px-6 py-6">
        {/* Back navigation */}
        <button
          onClick={() => navigate('/digital-resources')}
          className="flex items-center gap-2 text-sm text-gray-600 hover:text-museum-accent mb-6 transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to Digital Resources
        </button>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {categories.map((category) => {
            const Icon = category.icon
            return (
              <div
                key={category.id}
                className="bg-white rounded-xl border border-gray-200 p-6 shadow-sm hover:shadow-lg transition-all duration-300 hover:-translate-y-1 cursor-pointer group"
                onClick={() => navigate(category.path)}
              >
                <div className="flex items-center justify-between mb-4">
                  <div className={`w-14 h-14 ${category.iconBg} rounded-xl flex items-center justify-center`}>
                    <Icon className={`w-7 h-7 ${category.iconColor}`} />
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-sm font-medium text-gray-500 bg-gray-100 px-3 py-1 rounded-full">
                      {category.count === null ? '…' : `${category.count} ${category.count === 1 ? 'item' : 'items'}`}
                    </span>
                    <ArrowRight className="w-5 h-5 text-gray-300 group-hover:text-gray-600 group-hover:translate-x-1 transition-all" />
                  </div>
                </div>
                <h3 className="text-lg font-semibold text-gray-900 mb-2 group-hover:text-museum-accent transition-colors">
                  {category.title}
                </h3>
                <p className="text-sm text-gray-500 leading-relaxed">
                  {category.description}
                </p>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

export default MooreChronologyPage
