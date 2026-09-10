// import React, { useState, useEffect } from 'react'
// import { Clock, X, CheckCircle } from 'lucide-react'

// interface ActionFooterProps {
//   isModified: boolean
//   lastSaved: Date
//   onSave: () => void
//   onSaveAndComplete: () => void
//   onDiscard: () => void
// }

// const ActionFooter: React.FC<ActionFooterProps> = ({
//   lastSaved,
//   onSave,
//   onSaveAndComplete,
//   onDiscard
// }) => {
//   const [showToast, setShowToast] = useState(false)

//   const formatLastSaved = (date: Date) => {
//     const now = new Date()
//     const diffInMinutes = Math.floor((now.getTime() - date.getTime()) / (1000 * 60))
//     if (diffInMinutes < 1) return 'Just now'
//     if (diffInMinutes === 1) return '1 minute ago'
//     if (diffInMinutes < 60) return `${diffInMinutes} minutes ago`
//     const diffInHours = Math.floor(diffInMinutes / 60)
//     if (diffInHours === 1) return '1 hour ago'
//     if (diffInHours < 24) return `${diffInHours} hours ago`
//     const diffInDays = Math.floor(diffInHours / 24)
//     if (diffInDays === 1) return '1 day ago'
//     return `${diffInDays} days ago`
//   }

//   const handleSaveAndComplete = () => {
//     // Run whatever logic your parent sends
//     onSaveAndComplete()
//     // Show toast
//     setShowToast(true)
//   }

//   useEffect(() => {
//     if (showToast) {
//       const timer = setTimeout(() => setShowToast(false), 3000)
//       return () => clearTimeout(timer)
//     }
//   }, [showToast])

//   return (
//     <>
    

//       {/* Footer Section */}
//       <footer className="bg-white border-t border-gray-200 px-6 py-4 sticky bottom-0">
//         <div className="flex items-center justify-between">
//           <div className="flex items-center space-x-4">
//             <span className="text-sm text-gray-600">
//               <Clock className="w-3 h-3 mr-1 inline" />
//               Started review: 23 minutes ago
//             </span>
//           </div>

//           <div className="flex items-center space-x-3">
//             <button
//               className="px-4 py-2 text-gray-700 border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors flex items-center space-x-2"
//               onClick={onDiscard}
//             >
//               <X className="w-4 h-4" />
//               <span>Discard Changes</span>
//             </button>

//             <button
//               className="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors flex items-center space-x-2"
//               onClick={handleSaveAndComplete}
//             >
//               <CheckCircle className="w-4 h-4" />
//               <span>Save & Complete</span>
//             </button>
//           </div>
//         </div>
//       </footer>
//     </>
//   )
// }

// export default ActionFooter

import React, { useState, useEffect } from 'react'
import { Clock, CheckCircle } from 'lucide-react'

interface ActionFooterProps {
  isModified: boolean
  lastSaved: Date
  onSave: () => void
  onSaveAndComplete: () => void
}

const ActionFooter: React.FC<ActionFooterProps> = ({
  lastSaved,
  onSave,
  onSaveAndComplete
}) => {
  const [showToast, setShowToast] = useState(false)

  const formatLastSaved = (date: Date) => {
    const now = new Date()
    const diffInMinutes = Math.floor((now.getTime() - date.getTime()) / (1000 * 60))
    if (diffInMinutes < 1) return 'Just now'
    if (diffInMinutes === 1) return '1 minute ago'
    if (diffInMinutes < 60) return `${diffInMinutes} minutes ago`
    const diffInHours = Math.floor(diffInMinutes / 60)
    if (diffInHours === 1) return '1 hour ago'
    if (diffInHours < 24) return `${diffInHours} hours ago`
    const diffInDays = Math.floor(diffInHours / 24)
    if (diffInDays === 1) return '1 day ago'
    return `${diffInDays} days ago`
  }

  const handleSaveAndComplete = () => {
    onSaveAndComplete()
    setShowToast(true)
  }

  useEffect(() => {
    if (showToast) {
      const timer = setTimeout(() => setShowToast(false), 3000)
      return () => clearTimeout(timer)
    }
  }, [showToast])

  return (
    <>
      {/* Footer Section */}
      <footer className="bg-white border-t border-gray-200 px-6 py-4 sticky bottom-0">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-4">
            <span className="text-sm text-gray-600">
              <Clock className="w-3 h-3 mr-1 inline" />
              Started review: 23 minutes ago
            </span>
          </div>

          <div className="flex items-center space-x-3">
            <button
              className="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors flex items-center space-x-2"
              onClick={handleSaveAndComplete}
            >
              <CheckCircle className="w-4 h-4" />
              <span>Save & Complete</span>
            </button>
          </div>
        </div>
      </footer>
    </>
  )
}

export default ActionFooter
