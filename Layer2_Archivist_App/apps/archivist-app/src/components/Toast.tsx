// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

// 'use client'

// import { useState, useEffect } from 'react'
// import { CheckCircle, X } from 'lucide-react'

// interface ToastProps {
//   type: 'success' | 'error' | 'warning' | 'info'
//   message: string
//   duration?: number
//   onClose: () => void
// }

// const Toast: React.FC<ToastProps> = ({ type, message, duration = 5000, onClose }) => {
//   const [isVisible, setIsVisible] = useState(true)

//   useEffect(() => {
//     const timer = setTimeout(() => {
//       setIsVisible(false)
//       setTimeout(onClose, 300) // Allow fade out animation
//     }, duration)

//     return () => clearTimeout(timer)
//   }, [duration, onClose])

//   const getToastStyles = () => {
//     switch (type) {
//       case 'success':
//         return 'bg-green-50 border-green-200'
//       case 'error':
//         return 'bg-red-50 border-red-200'
//       case 'warning':
//         return 'bg-amber-50 border-amber-200'
//       case 'info':
//         return 'bg-blue-50 border-blue-200'
//       default:
//         return 'bg-green-50 border-green-200'
//     }
//   }

//   const getIconColor = () => {
//     switch (type) {
//       case 'success':
//         return 'bg-green-500'
//       case 'error':
//         return 'bg-red-500'
//       case 'warning':
//         return 'bg-amber-500'
//       case 'info':
//         return 'bg-blue-500'
//       default:
//         return 'bg-green-500'
//     }
//   }

//   const getTextColor = () => {
//     switch (type) {
//       case 'success':
//         return 'text-green-900'
//       case 'error':
//         return 'text-red-900'
//       case 'warning':
//         return 'text-amber-900'
//       case 'info':
//         return 'text-blue-900'
//       default:
//         return 'text-green-900'
//     }
//   }

//   const getMessageColor = () => {
//     switch (type) {
//       case 'success':
//         return 'text-green-700'
//       case 'error':
//         return 'text-red-700'
//       case 'warning':
//         return 'text-amber-700'
//       case 'info':
//         return 'text-blue-700'
//       default:
//         return 'text-green-700'
//     }
//   }

//   if (!isVisible) return null

//   return (
//     <div className={`border rounded-lg p-4 shadow-lg max-w-md transition-opacity duration-300 ${getToastStyles()}`}>
//       <div className="flex items-start space-x-3">
//         <div className={`w-5 h-5 ${getIconColor()} rounded-full flex items-center justify-center flex-shrink-0 mt-0.5`}>
//           <CheckCircle className="text-white text-xs" />
//         </div>
//         <div className="flex-1">
//           <p className={`text-sm ${getMessageColor()}`}>{message}</p>
//         </div>
//         <button
//           className={`${getTextColor()} hover:opacity-70 transition-opacity`}
//           onClick={onClose}
//           aria-label="Close notification"
//         >
//           <X className="w-4 h-4" />
//         </button>
//       </div>
//     </div>
//   )
// }

// export default Toast

'use client'

import { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { CheckCircle, X } from 'lucide-react'

interface ToastProps {
  type: 'success' | 'error' | 'warning' | 'info'
  message: string
  duration?: number
  onClose: () => void
}

const Toast: React.FC<ToastProps> = ({ type, message, duration = 5000, onClose }) => {
  const [isVisible, setIsVisible] = useState(true)

  useEffect(() => {
    const timer = setTimeout(() => {
      setIsVisible(false)
      setTimeout(onClose, 300) // allow fade-out
    }, duration)

    return () => clearTimeout(timer)
  }, [duration, onClose])

  const getToastStyles = () => {
    switch (type) {
      case 'success':
        return 'bg-green-50 border-green-200'
      case 'error':
        return 'bg-red-50 border-red-200'
      case 'warning':
        return 'bg-amber-50 border-amber-200'
      case 'info':
        return 'bg-blue-50 border-blue-200'
      default:
        return 'bg-green-50 border-green-200'
    }
  }

  const getIconColor = () => {
    switch (type) {
      case 'success':
        return 'bg-green-500'
      case 'error':
        return 'bg-red-500'
      case 'warning':
        return 'bg-amber-500'
      case 'info':
        return 'bg-blue-500'
      default:
        return 'bg-green-500'
    }
  }

  const getTextColor = () => {
    switch (type) {
      case 'success':
        return 'text-green-900'
      case 'error':
        return 'text-red-900'
      case 'warning':
        return 'text-amber-900'
      case 'info':
        return 'text-blue-900'
      default:
        return 'text-green-900'
    }
  }

  const getMessageColor = () => {
    switch (type) {
      case 'success':
        return 'text-green-700'
      case 'error':
        return 'text-red-700'
      case 'warning':
        return 'text-amber-700'
      case 'info':
        return 'text-blue-700'
      default:
        return 'text-green-700'
    }
  }

  if (!isVisible) return null

  const body = typeof document !== 'undefined' ? document.body : null
  const node = (
    <div className="pointer-events-none fixed inset-x-0 top-[80px] z-[30000] flex justify-end px-5">
      <div
        className={`pointer-events-auto border rounded-lg p-4 shadow-lg max-w-md transition-opacity duration-300 ${getToastStyles()}`}
      >
        <div className="flex items-start space-x-3">
          <div
            className={`w-5 h-5 ${getIconColor()} rounded-full flex items-center justify-center flex-shrink-0 mt-0.5`}
          >
            <CheckCircle className="text-white text-xs" />
          </div>
          <div className="flex-1">
            <p className={`text-sm ${getMessageColor()}`}>{message}</p>
          </div>
          <button
            className={`${getTextColor()} hover:opacity-70 transition-opacity`}
            onClick={onClose}
            aria-label="Close notification"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  )

  return body ? createPortal(node, body) : null
}

export default Toast
