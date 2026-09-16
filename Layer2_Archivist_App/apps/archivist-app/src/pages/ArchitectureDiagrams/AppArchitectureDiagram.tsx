// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

export default function AppArchitectureDiagram() {
  return (
    <div className="bg-white rounded-xl shadow-lg p-6 overflow-auto">
      <svg viewBox="0 0 1200 700" className="w-full h-auto min-w-[1000px]">
        <defs>
          <marker id="arr" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
            <polygon points="0 0, 8 3, 0 6" fill="#64748b"/>
          </marker>
          <marker id="arr-blue" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
            <polygon points="0 0, 8 3, 0 6" fill="#3b82f6"/>
          </marker>
          <marker id="arr-orange" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
            <polygon points="0 0, 8 3, 0 6" fill="#d97706"/>
          </marker>
          <marker id="arr-green" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
            <polygon points="0 0, 8 3, 0 6" fill="#059669"/>
          </marker>
          <marker id="arr-purple" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
            <polygon points="0 0, 8 3, 0 6" fill="#7c3aed"/>
          </marker>
          <filter id="sh" x="-5%" y="-5%" width="110%" height="115%">
            <feDropShadow dx="1" dy="2" stdDeviation="3" floodOpacity="0.1"/>
          </filter>
        </defs>

        {/* LAYER 1: USERS & AUTH */}
        <g transform="translate(40, 20)">
          <rect width="1110" height="80" rx="10" fill="#dbeafe" stroke="#3b82f6" strokeWidth="2" filter="url(#sh)"/>
          <rect width="100" height="26" rx="10" fill="#3b82f6"/>
          <text x="50" y="18" textAnchor="middle" fill="white" className="text-[10px] font-semibold">USERS</text>
          
          <g transform="translate(120, 18)">
            <rect width="100" height="44" rx="6" fill="white" stroke="#3b82f6" strokeWidth="1.5"/>
            <text x="50" y="18" textAnchor="middle" className="fill-slate-800 text-[11px] font-semibold">🔬 Archivists</text>
            <text x="50" y="34" textAnchor="middle" className="fill-slate-500 text-[9px]">Review & Approve</text>
          </g>
          <g transform="translate(235, 18)">
            <rect width="100" height="44" rx="6" fill="white" stroke="#3b82f6" strokeWidth="1.5"/>
            <text x="50" y="18" textAnchor="middle" className="fill-slate-800 text-[11px] font-semibold">👨‍💼 Admins</text>
            <text x="50" y="34" textAnchor="middle" className="fill-slate-500 text-[9px]">Pipeline & Config</text>
          </g>
          <g transform="translate(350, 18)">
            <rect width="100" height="44" rx="6" fill="white" stroke="#3b82f6" strokeWidth="1.5"/>
            <text x="50" y="18" textAnchor="middle" className="fill-slate-800 text-[11px] font-semibold">🔍 Researchers</text>
            <text x="50" y="34" textAnchor="middle" className="fill-slate-500 text-[9px]">Search & Browse</text>
          </g>
          
          {/* Azure AD */}
          <g transform="translate(780, 12)">
            <rect width="310" height="56" rx="6" fill="white" stroke="#0078d4" strokeWidth="2"/>
            <text x="155" y="22" textAnchor="middle" className="fill-slate-800 text-[12px] font-semibold">🔐 Azure AD (Entra ID)</text>
            <text x="155" y="40" textAnchor="middle" className="fill-slate-600 text-[10px]">SSO • RBAC • OAuth 2.0 / OIDC</text>
          </g>
          
          {/* Auth arrow */}
          <line x1="450" y1="40" x2="775" y2="40" stroke="#3b82f6" strokeWidth="1.5" strokeDasharray="5,3" markerEnd="url(#arr-blue)"/>
        </g>

        {/* Arrow: Users to Frontend/Backend */}
        <line x1="300" y1="100" x2="300" y2="125" stroke="#64748b" strokeWidth="2" markerEnd="url(#arr)"/>
        <line x1="900" y1="100" x2="900" y2="125" stroke="#64748b" strokeWidth="2" markerEnd="url(#arr)"/>

        {/* LAYER 2: FRONTEND */}
        <g transform="translate(40, 130)">
          <rect width="520" height="125" rx="10" fill="#ecfdf5" stroke="#059669" strokeWidth="2" filter="url(#sh)"/>
          <rect width="160" height="26" rx="10" fill="#059669"/>
          <text x="80" y="18" textAnchor="middle" fill="white" className="text-[10px] font-semibold">FRONTEND (React)</text>
          
          <g transform="translate(15, 35)">
            <rect width="150" height="78" rx="6" fill="white" stroke="#059669" strokeWidth="1.5"/>
            <text x="75" y="16" textAnchor="middle" className="fill-slate-800 text-[11px] font-semibold">⚛️ React + TypeScript</text>
            <text x="75" y="30" textAnchor="middle" className="fill-slate-500 text-[8px]">Vite Build System</text>
            <line x1="10" y1="38" x2="140" y2="38" stroke="#d1fae5"/>
            <text x="75" y="50" textAnchor="middle" className="fill-slate-500 text-[8px]">• Dashboard & Stats</text>
            <text x="75" y="62" textAnchor="middle" className="fill-slate-500 text-[8px]">• Document Review</text>
            <text x="75" y="74" textAnchor="middle" className="fill-slate-500 text-[8px]">• Pipeline Monitor</text>
          </g>
          
          <g transform="translate(180, 35)">
            <rect width="150" height="78" rx="6" fill="white" stroke="#059669" strokeWidth="1.5"/>
            <text x="75" y="16" textAnchor="middle" className="fill-slate-800 text-[11px] font-semibold">🧩 Components</text>
            <text x="75" y="34" textAnchor="middle" className="fill-slate-500 text-[8px]">📊 Charts & Stats</text>
            <text x="75" y="46" textAnchor="middle" className="fill-slate-500 text-[8px]">📝 Document Editor</text>
            <text x="75" y="58" textAnchor="middle" className="fill-slate-500 text-[8px]">🖼️ Image Viewer</text>
            <text x="75" y="70" textAnchor="middle" className="fill-slate-500 text-[8px]">📄 OCR Display</text>
          </g>
          
          <g transform="translate(345, 35)">
            <rect width="160" height="78" rx="6" fill="white" stroke="#059669" strokeWidth="1.5"/>
            <text x="80" y="16" textAnchor="middle" className="fill-slate-800 text-[11px] font-semibold">🔧 Services</text>
            <text x="80" y="34" textAnchor="middle" className="fill-slate-500 text-[8px]">🔗 API Client (fetch)</text>
            <text x="80" y="46" textAnchor="middle" className="fill-slate-500 text-[8px]">🔐 Auth Service</text>
            <text x="80" y="58" textAnchor="middle" className="fill-slate-500 text-[8px]">📈 App Insights</text>
            <text x="80" y="70" textAnchor="middle" className="fill-slate-500 text-[8px]">🔄 React Context</text>
          </g>
        </g>

        {/* LAYER 2: BACKEND */}
        <g transform="translate(600, 130)">
          <rect width="550" height="125" rx="10" fill="#fff7ed" stroke="#ea580c" strokeWidth="2" filter="url(#sh)"/>
          <rect width="180" height="26" rx="10" fill="#ea580c"/>
          <text x="90" y="18" textAnchor="middle" fill="white" className="text-[10px] font-semibold">BACKEND API (FastAPI)</text>
          
          <g transform="translate(15, 35)">
            <rect width="155" height="78" rx="6" fill="white" stroke="#ea580c" strokeWidth="1.5"/>
            <text x="77" y="16" textAnchor="middle" className="fill-slate-800 text-[11px] font-semibold">🛣️ API Routes</text>
            <text x="77" y="30" textAnchor="middle" className="fill-slate-500 text-[8px]">/api/v1/documents</text>
            <text x="77" y="42" textAnchor="middle" className="fill-slate-500 text-[8px]">/api/v1/repositories</text>
            <text x="77" y="54" textAnchor="middle" className="fill-slate-500 text-[8px]">/api/v1/pipeline</text>
            <text x="77" y="66" textAnchor="middle" className="fill-slate-500 text-[8px]">/api/v1/epub</text>
          </g>
          
          <g transform="translate(185, 35)">
            <rect width="170" height="78" rx="6" fill="white" stroke="#ea580c" strokeWidth="1.5"/>
            <text x="85" y="16" textAnchor="middle" className="fill-slate-800 text-[11px] font-semibold">⚙️ Services</text>
            <text x="85" y="30" textAnchor="middle" className="fill-slate-500 text-[8px]">📄 Cosmos Service</text>
            <text x="85" y="42" textAnchor="middle" className="fill-slate-500 text-[8px]">📦 Blob Service</text>
            <text x="85" y="54" textAnchor="middle" className="fill-slate-500 text-[8px]">📊 Statistics Service</text>
            <text x="85" y="66" textAnchor="middle" className="fill-slate-500 text-[8px]">🔄 Pipeline Service</text>
          </g>
          
          <g transform="translate(370, 35)">
            <rect width="165" height="78" rx="6" fill="white" stroke="#ea580c" strokeWidth="1.5"/>
            <text x="82" y="16" textAnchor="middle" className="fill-slate-800 text-[11px] font-semibold">🔧 Core</text>
            <text x="82" y="34" textAnchor="middle" className="fill-slate-500 text-[8px]">📋 Pydantic Schemas</text>
            <text x="82" y="46" textAnchor="middle" className="fill-slate-500 text-[8px]">🔐 Auth Middleware</text>
            <text x="82" y="58" textAnchor="middle" className="fill-slate-500 text-[8px]">⚙️ Configuration</text>
            <text x="82" y="70" textAnchor="middle" className="fill-slate-500 text-[8px]">📝 Logging</text>
          </g>
        </g>

        {/* Arrow: Frontend to Backend */}
        <line x1="560" y1="192" x2="595" y2="192" stroke="#64748b" strokeWidth="2.5" markerEnd="url(#arr)"/>
        <text x="578" y="185" textAnchor="middle" className="fill-slate-700 text-[10px] font-semibold">REST</text>

        {/* Arrows: Frontend/Backend to Functions/Platform */}
        <line x1="300" y1="255" x2="300" y2="280" stroke="#d97706" strokeWidth="2" markerEnd="url(#arr-orange)"/>
        <line x1="875" y1="255" x2="875" y2="280" stroke="#0078d4" strokeWidth="2" markerEnd="url(#arr-blue)"/>

        {/* LAYER 3: AZURE FUNCTIONS */}
        <g transform="translate(40, 285)">
          <rect width="520" height="100" rx="10" fill="#fef3c7" stroke="#d97706" strokeWidth="2" filter="url(#sh)"/>
          <rect width="220" height="26" rx="10" fill="#d97706"/>
          <text x="110" y="18" textAnchor="middle" fill="white" className="text-[10px] font-semibold">AZURE FUNCTIONS (Durable)</text>
          
          <g transform="translate(15, 35)">
            <rect width="230" height="52" rx="6" fill="white" stroke="#d97706" strokeWidth="1.5"/>
            <text x="115" y="18" textAnchor="middle" className="fill-slate-800 text-[11px] font-semibold">📥 Search Ingest</text>
            <text x="115" y="34" textAnchor="middle" className="fill-slate-500 text-[8px]">DataIngestOrchestrator</text>
            <text x="115" y="46" textAnchor="middle" className="fill-slate-500 text-[8px]">Cosmos → AI Search Index</text>
          </g>
          
          <g transform="translate(265, 35)">
            <rect width="235" height="52" rx="6" fill="white" stroke="#d97706" strokeWidth="1.5"/>
            <text x="117" y="18" textAnchor="middle" className="fill-slate-800 text-[11px] font-semibold">📖 EPUB Processing</text>
            <text x="117" y="34" textAnchor="middle" className="fill-slate-500 text-[8px]">Parse • Extract • Ingest • Delete</text>
            <text x="117" y="46" textAnchor="middle" className="fill-slate-500 text-[8px]">Queue-triggered workflow</text>
          </g>
        </g>

        {/* LAYER 3: AZURE PLATFORM */}
        <g transform="translate(600, 285)">
          <rect width="550" height="100" rx="10" fill="#e8f4fd" stroke="#0078d4" strokeWidth="2" filter="url(#sh)"/>
          <rect width="180" height="26" rx="10" fill="#0078d4"/>
          <text x="90" y="18" textAnchor="middle" fill="white" className="text-[10px] font-semibold">AZURE PLATFORM</text>
          
          <g transform="translate(15, 35)">
            <rect width="160" height="52" rx="6" fill="white" stroke="#0078d4" strokeWidth="1.5"/>
            <text x="80" y="18" textAnchor="middle" className="fill-slate-800 text-[11px] font-semibold">📨 Service Bus</text>
            <text x="80" y="34" textAnchor="middle" className="fill-slate-500 text-[8px]">data-ingestion-queue</text>
            <text x="80" y="46" textAnchor="middle" className="fill-slate-500 text-[8px]">epub-queue</text>
          </g>
          
          <g transform="translate(190, 35)">
            <rect width="160" height="52" rx="6" fill="white" stroke="#0078d4" strokeWidth="1.5"/>
            <text x="80" y="18" textAnchor="middle" className="fill-slate-800 text-[11px] font-semibold">🌐 App Service</text>
            <text x="80" y="34" textAnchor="middle" className="fill-slate-500 text-[8px]">Web Hosting</text>
            <text x="80" y="46" textAnchor="middle" className="fill-slate-500 text-[8px]">Easy Auth (Azure AD)</text>
          </g>
          
          <g transform="translate(365, 35)">
            <rect width="170" height="52" rx="6" fill="white" stroke="#0078d4" strokeWidth="1.5"/>
            <text x="85" y="18" textAnchor="middle" className="fill-slate-800 text-[11px] font-semibold">🔑 Key Vault + Insights</text>
            <text x="85" y="34" textAnchor="middle" className="fill-slate-500 text-[8px]">Secrets Management</text>
            <text x="85" y="46" textAnchor="middle" className="fill-slate-500 text-[8px]">📊 Application Insights</text>
          </g>
        </g>

        {/* Arrows: Functions/Platform to Data */}
        <line x1="300" y1="385" x2="300" y2="410" stroke="#059669" strokeWidth="2" markerEnd="url(#arr-green)"/>
        <line x1="875" y1="385" x2="875" y2="410" stroke="#7c3aed" strokeWidth="2" markerEnd="url(#arr-purple)"/>

        {/* LAYER 4: DATA LAYER */}
        <g transform="translate(40, 415)">
          <rect width="770" height="105" rx="10" fill="#ecfdf5" stroke="#059669" strokeWidth="2" filter="url(#sh)"/>
          <rect width="130" height="26" rx="10" fill="#059669"/>
          <text x="65" y="18" textAnchor="middle" fill="white" className="text-[10px] font-semibold">DATA LAYER</text>
          
          {/* Cosmos DB */}
          <g transform="translate(15, 32)">
            <rect width="230" height="62" rx="6" fill="white" stroke="#059669" strokeWidth="1.5"/>
            <text x="115" y="16" textAnchor="middle" className="fill-slate-800 text-[11px] font-semibold">🗃️ Azure Cosmos DB (NoSQL)</text>
            <g transform="translate(8, 24)">
              <rect width="60" height="16" rx="3" fill="#d1fae5" stroke="#10b981"/>
              <text x="30" y="12" textAnchor="middle" className="fill-slate-600 text-[8px]">records</text>
            </g>
            <g transform="translate(78, 24)">
              <rect width="60" height="16" rx="3" fill="#d1fae5" stroke="#10b981"/>
              <text x="30" y="12" textAnchor="middle" className="fill-slate-600 text-[8px]">statistics</text>
            </g>
            <g transform="translate(148, 24)">
              <rect width="60" height="16" rx="3" fill="#d1fae5" stroke="#10b981"/>
              <text x="30" y="12" textAnchor="middle" className="fill-slate-600 text-[8px]">audit</text>
            </g>
            <g transform="translate(8, 44)">
              <rect width="60" height="16" rx="3" fill="#d1fae5" stroke="#10b981"/>
              <text x="30" y="12" textAnchor="middle" className="fill-slate-600 text-[8px]">batchstatus</text>
            </g>
            <g transform="translate(78, 44)">
              <rect width="60" height="16" rx="3" fill="#d1fae5" stroke="#10b981"/>
              <text x="30" y="12" textAnchor="middle" className="fill-slate-600 text-[8px]">epub</text>
            </g>
          </g>
          
          {/* Blob Storage */}
          <g transform="translate(260, 32)">
            <rect width="230" height="62" rx="6" fill="white" stroke="#059669" strokeWidth="1.5"/>
            <text x="115" y="16" textAnchor="middle" className="fill-slate-800 text-[11px] font-semibold">📦 Azure Blob Storage</text>
            <g transform="translate(8, 24)">
              <rect width="60" height="16" rx="3" fill="#d1fae5" stroke="#10b981"/>
              <text x="30" y="12" textAnchor="middle" className="fill-slate-600 text-[8px]">originals</text>
            </g>
            <g transform="translate(78, 24)">
              <rect width="60" height="16" rx="3" fill="#d1fae5" stroke="#10b981"/>
              <text x="30" y="12" textAnchor="middle" className="fill-slate-600 text-[8px]">thumbnails</text>
            </g>
            <g transform="translate(148, 24)">
              <rect width="60" height="16" rx="3" fill="#d1fae5" stroke="#10b981"/>
              <text x="30" y="12" textAnchor="middle" className="fill-slate-600 text-[8px]">ocr-text</text>
            </g>
            <g transform="translate(8, 44)">
              <rect width="60" height="16" rx="3" fill="#d1fae5" stroke="#10b981"/>
              <text x="30" y="12" textAnchor="middle" className="fill-slate-600 text-[8px]">batch-io</text>
            </g>
            <g transform="translate(78, 44)">
              <rect width="60" height="16" rx="3" fill="#d1fae5" stroke="#10b981"/>
              <text x="30" y="12" textAnchor="middle" className="fill-slate-600 text-[8px]">epub-files</text>
            </g>
          </g>
          
          {/* AI Search */}
          <g transform="translate(505, 32)">
            <rect width="250" height="62" rx="6" fill="white" stroke="#059669" strokeWidth="1.5"/>
            <text x="125" y="16" textAnchor="middle" className="fill-slate-800 text-[11px] font-semibold">🔍 Azure AI Search</text>
            <g transform="translate(20, 26)">
              <rect width="95" height="16" rx="3" fill="#d1fae5" stroke="#10b981"/>
              <text x="47" y="12" textAnchor="middle" className="fill-slate-600 text-[8px]">records-index</text>
            </g>
            <g transform="translate(130, 26)">
              <rect width="95" height="16" rx="3" fill="#d1fae5" stroke="#10b981"/>
              <text x="47" y="12" textAnchor="middle" className="fill-slate-600 text-[8px]">epub-index</text>
            </g>
            <text x="125" y="56" textAnchor="middle" className="fill-slate-500 text-[8px]">Full-text • Semantic • Vector</text>
          </g>
        </g>

        {/* LAYER 4: AI SERVICES */}
        <g transform="translate(850, 415)">
          <rect width="300" height="105" rx="10" fill="#faf5ff" stroke="#7c3aed" strokeWidth="2" filter="url(#sh)"/>
          <rect width="130" height="26" rx="10" fill="#7c3aed"/>
          <text x="65" y="18" textAnchor="middle" fill="white" className="text-[10px] font-semibold">AI SERVICES</text>
          
          <g transform="translate(15, 32)">
            <rect width="270" height="62" rx="6" fill="white" stroke="#7c3aed" strokeWidth="1.5"/>
            <text x="135" y="16" textAnchor="middle" className="fill-slate-800 text-[11px] font-semibold">🤖 Azure OpenAI</text>
            <rect x="10" y="24" width="250" height="16" rx="3" fill="#ede9fe" stroke="#8b5cf6"/>
            <text x="135" y="36" textAnchor="middle" className="fill-slate-600 text-[9px] font-medium">GPT-4.1 Vision • OCR & Metadata</text>
            <rect x="10" y="44" width="250" height="16" rx="3" fill="#ede9fe" stroke="#8b5cf6"/>
            <text x="135" y="56" textAnchor="middle" className="fill-slate-600 text-[9px] font-medium">ada-3-large • Vector Embeddings</text>
          </g>
        </g>

        {/* LEGEND */}
        <g transform="translate(40, 545)">
          <rect width="1110" height="70" rx="8" fill="#f8fafc" stroke="#e2e8f0"/>
          <text x="15" y="20" className="fill-slate-800 text-[11px] font-bold">LEGEND</text>
          
          <g transform="translate(15, 35)">
            <rect width="12" height="12" rx="2" fill="#dbeafe" stroke="#3b82f6"/>
            <text x="18" y="10" className="fill-slate-500 text-[9px]">Users/Auth</text>
          </g>
          <g transform="translate(95, 35)">
            <rect width="12" height="12" rx="2" fill="#ecfdf5" stroke="#059669"/>
            <text x="18" y="10" className="fill-slate-500 text-[9px]">Frontend/Data</text>
          </g>
          <g transform="translate(195, 35)">
            <rect width="12" height="12" rx="2" fill="#fff7ed" stroke="#ea580c"/>
            <text x="18" y="10" className="fill-slate-500 text-[9px]">Backend</text>
          </g>
          <g transform="translate(275, 35)">
            <rect width="12" height="12" rx="2" fill="#fef3c7" stroke="#d97706"/>
            <text x="18" y="10" className="fill-slate-500 text-[9px]">Functions</text>
          </g>
          <g transform="translate(360, 35)">
            <rect width="12" height="12" rx="2" fill="#e8f4fd" stroke="#0078d4"/>
            <text x="18" y="10" className="fill-slate-500 text-[9px]">Azure PaaS</text>
          </g>
          <g transform="translate(450, 35)">
            <rect width="12" height="12" rx="2" fill="#faf5ff" stroke="#7c3aed"/>
            <text x="18" y="10" className="fill-slate-500 text-[9px]">AI Services</text>
          </g>
          
          <g transform="translate(550, 28)">
            <text x="0" y="0" className="fill-slate-700 text-[10px] font-semibold">Application Flow:</text>
            <text x="0" y="16" className="fill-slate-500 text-[9px]">Users → React SPA → FastAPI Backend → Azure Functions → Data Layer (Cosmos/Blob/Search) → AI Services</text>
          </g>
        </g>
        
      </svg>
    </div>
  )
}
