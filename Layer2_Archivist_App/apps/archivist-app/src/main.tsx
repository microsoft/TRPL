// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import { initializeAppInsights } from './services/appInsights'

// Initialize Application Insights before rendering
initializeAppInsights()

createRoot(document.getElementById('root')!).render(
  <App />,
)
