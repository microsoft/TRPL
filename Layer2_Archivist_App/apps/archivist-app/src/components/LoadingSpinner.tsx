// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import React from 'react'

interface LoadingSpinnerProps {
  message?: string
  className?: string
  spinnerClassName?: string
}

const LoadingSpinner: React.FC<LoadingSpinnerProps> = ({
  message = 'Loading...',
  className = '',
  spinnerClassName = ''
}) => {
  return (
    <div className={`relative w-full h-full flex items-center justify-center bg-gray-50 ${className}`} style={{ minHeight: 'calc(100vh - 16rem)' }}>
      <div className="flex items-center">
        <div className={`animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 ${spinnerClassName}`}></div>
        {message && <span className="ml-4 text-gray-600">{message}</span>}
      </div>
    </div>
  )
}

export default LoadingSpinner

