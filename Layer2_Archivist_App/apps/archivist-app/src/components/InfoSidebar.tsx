'use client'

import { X } from 'lucide-react'
import { QueueStats } from '@/types'

interface InfoSidebarProps {
  isOpen: boolean
  onClose: () => void
  stats: QueueStats
}

const InfoSidebar: React.FC<InfoSidebarProps> = ({ isOpen, onClose, stats }) => {
  if (!isOpen) return null

  return (
    <aside className="fixed right-0 top-0 h-full w-80 bg-white border-l border-museum-200 shadow-xl transform transition-transform duration-300 z-50">
      <div className="h-full flex flex-col">
        <div className="flex items-center justify-between p-6 border-b border-museum-200">
          <h3 className="text-lg font-semibold text-museum-900">Queue Information</h3>
          <button
            className="p-2 text-museum-500 hover:text-museum-700 hover:bg-museum-100 rounded-lg transition-colors"
            onClick={onClose}
            aria-label="Close sidebar"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
        
        <div className="flex-1 overflow-y-auto custom-scrollbar">
          <div className="p-6 space-y-6">
            {/* Queue Statistics */}
            <div>
              <h4 className="font-medium text-museum-900 mb-4">Queue Statistics</h4>
              <div className="grid grid-cols-2 gap-4">
                <div className="bg-museum-50 p-4 rounded-lg">
                  <div className="text-2xl font-bold text-red-600">{stats.pending}</div>
                  <div className="text-sm text-museum-600">Pending Review</div>
                </div>
                <div className="bg-museum-50 p-4 rounded-lg">
                  <div className="text-2xl font-bold text-green-600">{stats.completed}</div>
                  <div className="text-sm text-museum-600">Completed</div>
                </div>
                <div className="bg-museum-50 p-4 rounded-lg">
                  <div className="text-2xl font-bold text-amber-600">{stats.severeDeviations}</div>
                  <div className="text-sm text-museum-600">Severe Deviations</div>
                </div>
                <div className="bg-museum-50 p-4 rounded-lg">
                  <div className="text-2xl font-bold text-blue-600">{stats.inReview}</div>
                  <div className="text-sm text-museum-600">In Review</div>
                </div>
              </div>
            </div>

            {/* Progress Overview */}
            <div>
              <h4 className="font-medium text-museum-900 mb-4">Progress Overview</h4>
              <div className="space-y-4">
                <div>
                  <div className="flex justify-between text-sm mb-1">
                    <span className="text-museum-600">Overall Progress</span>
                    <span className="text-museum-900 font-medium">84.5%</span>
                  </div>
                  <div className="w-full bg-museum-200 rounded-full h-2">
                    <div className="bg-green-500 h-2 rounded-full" style={{ width: '84.5%' }}></div>
                  </div>
                </div>
                <div>
                  <div className="flex justify-between text-sm mb-1">
                    <span className="text-museum-600">This Week</span>
                    <span className="text-museum-900 font-medium">67 completed</span>
                  </div>
                  <div className="w-full bg-museum-200 rounded-full h-2">
                    <div className="bg-blue-500 h-2 rounded-full" style={{ width: '67%' }}></div>
                  </div>
                </div>
              </div>
            </div>

            {/* Source Distribution */}
            <div>
              <h4 className="font-medium text-museum-900 mb-4">Source Distribution</h4>
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <div className="w-3 h-3 bg-blue-600 rounded-full"></div>
                    <span className="text-sm text-museum-700">Library of Congress</span>
                  </div>
                  <span className="text-sm font-medium text-museum-900">127</span>
                </div>
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <div className="w-3 h-3 bg-red-700 rounded-full"></div>
                    <span className="text-sm text-museum-700">Harvard</span>
                  </div>
                  <span className="text-sm font-medium text-museum-900">89</span>
                </div>
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <div className="w-3 h-3 bg-green-600 rounded-full"></div>
                    <span className="text-sm text-museum-700">Morris Collection</span>
                  </div>
                  <span className="text-sm font-medium text-museum-900">23</span>
                </div>
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <div className="w-3 h-3 bg-purple-600 rounded-full"></div>
                    <span className="text-sm text-museum-700">Roosevelt Archive</span>
                  </div>
                  <span className="text-sm font-medium text-museum-900">8</span>
                </div>
              </div>
            </div>

            {/* Recent Activity */}
            <div>
              <h4 className="font-medium text-museum-900 mb-4">Recent Activity</h4>
              <div className="space-y-3">
                <div className="flex items-start space-x-3">
                  <div className="w-2 h-2 bg-green-500 rounded-full mt-2"></div>
                  <div className="flex-1 text-sm">
                    <p className="text-museum-900"><strong>Archivist A</strong> completed 3 items</p>
                    <p className="text-museum-500">2 hours ago</p>
                  </div>
                </div>
                <div className="flex items-start space-x-3">
                  <div className="w-2 h-2 bg-blue-500 rounded-full mt-2"></div>
                  <div className="flex-1 text-sm">
                    <p className="text-museum-900"><strong>Archivist B</strong> started reviewing item</p>
                    <p className="text-museum-500">45 minutes ago</p>
                  </div>
                </div>
                <div className="flex items-start space-x-3">
                  <div className="w-2 h-2 bg-amber-500 rounded-full mt-2"></div>
                  <div className="flex-1 text-sm">
                    <p className="text-museum-900">Severe deviation flagged in <strong>loc-2013646805</strong></p>
                    <p className="text-museum-500">1 hour ago</p>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </aside>
  )
}

export default InfoSidebar
