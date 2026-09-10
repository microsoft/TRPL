import React from 'react'

interface PaginationOverlayProps {
  isVisible: boolean
  message?: string
}

const PaginationOverlay: React.FC<PaginationOverlayProps> = ({
  isVisible,
  message = 'Loading page...'
}) => {
  if (!isVisible) return null

  return (
    <div className="absolute inset-0 bg-white bg-opacity-75 flex justify-center items-center z-10">
      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
      <span className="ml-3 text-gray-600 font-medium">{message}</span>
    </div>
  )
}

export default PaginationOverlay

