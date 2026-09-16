// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

'use client'

export default function DataPipelineDiagram() {
  return (
    <div className="bg-white rounded-xl shadow-lg p-6 overflow-auto">
      <svg viewBox="0 0 1200 720" className="w-full h-auto min-w-[1000px]">
        <defs>
          <marker id="arrow" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
            <polygon points="0 0, 8 3, 0 6" fill="#64748b"/>
          </marker>
          <marker id="arrow-orange" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
            <polygon points="0 0, 8 3, 0 6" fill="#ea580c"/>
          </marker>
          <marker id="arrow-green" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
            <polygon points="0 0, 8 3, 0 6" fill="#059669"/>
          </marker>
          <marker id="arrow-purple" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
            <polygon points="0 0, 8 3, 0 6" fill="#7c3aed"/>
          </marker>
          <marker id="arrow-cyan" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
            <polygon points="0 0, 8 3, 0 6" fill="#0891b2"/>
          </marker>
          <filter id="shadow" x="-5%" y="-5%" width="110%" height="115%">
            <feDropShadow dx="1" dy="2" stdDeviation="3" floodOpacity="0.1"/>
          </filter>
        </defs>

        {/* ROW 1: EXTERNAL SOURCE */}
        <g transform="translate(40, 20)">
          <text x="0" y="15" className="fill-slate-800 text-[12px] font-bold">① EXTERNAL SOURCE</text>
          <rect x="0" y="25" width="180" height="60" rx="8" fill="#fef3c7" stroke="#d97706" strokeWidth="2" filter="url(#shadow)"/>
          <text x="90" y="50" textAnchor="middle" className="fill-slate-800 text-[12px] font-semibold">📦 Content Source Adapter</text>
          <text x="90" y="68" textAnchor="middle" className="fill-slate-600 text-[10px]">Provider-neutral contract</text>
        </g>

        {/* Arrow: Source to Pipeline */}
        <line x1="130" y1="105" x2="130" y2="135" stroke="#d97706" strokeWidth="2" markerEnd="url(#arrow-orange)"/>

        {/* ROW 2: PIPELINE STAGES - Row 1 (Stages 1-4) */}
        <g transform="translate(40, 145)">
          <text x="0" y="15" className="fill-slate-800 text-[12px] font-bold">② PIPELINE STAGES (Azure Durable Functions) - 10 Stages</text>
          
          {/* Pipeline container - Row 1 */}
          <rect x="0" y="25" width="690" height="100" rx="10" fill="#fff7ed" stroke="#ea580c" strokeWidth="2" strokeDasharray="6,3"/>
          
          {/* Stage 1 */}
          <g transform="translate(10, 32)">
            <rect width="155" height="86" rx="6" fill="white" stroke="#ea580c" strokeWidth="1.5"/>
            <rect width="155" height="22" rx="6" fill="#ea580c"/>
            <text x="77" y="15" textAnchor="middle" fill="white" className="text-[9px] font-semibold">1. Content Source Sync</text>
            <text x="77" y="40" textAnchor="middle" className="fill-slate-600 text-[8px]">Fetch records from</text>
            <text x="77" y="52" textAnchor="middle" className="fill-slate-600 text-[8px]">typed adapter</text>
            <rect x="27" y="60" width="100" height="18" rx="4" fill="#fef3c7" stroke="#d97706"/>
            <text x="77" y="73" textAnchor="middle" className="fill-slate-600 text-[7px]">HTTP Trigger</text>
          </g>
          
          {/* Arrow 1→2 */}
          <line x1="170" y1="75" x2="180" y2="75" stroke="#64748b" strokeWidth="2" markerEnd="url(#arrow)"/>
          
          {/* Stage 2 */}
          <g transform="translate(185, 32)">
            <rect width="155" height="86" rx="6" fill="white" stroke="#ea580c" strokeWidth="1.5"/>
            <rect width="155" height="22" rx="6" fill="#ea580c"/>
            <text x="77" y="15" textAnchor="middle" fill="white" className="text-[9px] font-semibold">2. Related Assets</text>
            <text x="77" y="40" textAnchor="middle" className="fill-slate-600 text-[8px]">Get asset IDs</text>
            <text x="77" y="52" textAnchor="middle" className="fill-slate-600 text-[8px]">for each record</text>
            <rect x="27" y="60" width="100" height="18" rx="4" fill="#e8f4fd" stroke="#0078d4"/>
            <text x="77" y="73" textAnchor="middle" className="fill-slate-600 text-[7px]">Durable Orch</text>
          </g>
          
          {/* Arrow 2→3 */}
          <line x1="345" y1="75" x2="355" y2="75" stroke="#64748b" strokeWidth="2" markerEnd="url(#arrow)"/>
          
          {/* Stage 3 */}
          <g transform="translate(360, 32)">
            <rect width="155" height="86" rx="6" fill="white" stroke="#ea580c" strokeWidth="1.5"/>
            <rect width="155" height="22" rx="6" fill="#ea580c"/>
            <text x="77" y="15" textAnchor="middle" fill="white" className="text-[9px] font-semibold">3. Asset Details</text>
            <text x="77" y="40" textAnchor="middle" className="fill-slate-600 text-[8px]">Fetch metadata</text>
            <text x="77" y="52" textAnchor="middle" className="fill-slate-600 text-[8px]">& thumbnails</text>
            <rect x="27" y="60" width="100" height="18" rx="4" fill="#e8f4fd" stroke="#0078d4"/>
            <text x="77" y="73" textAnchor="middle" className="fill-slate-600 text-[7px]">Durable Orch</text>
          </g>
          
          {/* Arrow 3→4 */}
          <line x1="520" y1="75" x2="530" y2="75" stroke="#64748b" strokeWidth="2" markerEnd="url(#arrow)"/>
          
          {/* Stage 4 */}
          <g transform="translate(535, 32)">
            <rect width="145" height="86" rx="6" fill="white" stroke="#ea580c" strokeWidth="1.5"/>
            <rect width="145" height="22" rx="6" fill="#ea580c"/>
            <text x="72" y="15" textAnchor="middle" fill="white" className="text-[9px] font-semibold">4. Download Files</text>
            <text x="72" y="40" textAnchor="middle" className="fill-slate-600 text-[8px]">Download originals</text>
            <text x="72" y="52" textAnchor="middle" className="fill-slate-600 text-[8px]">to Blob Storage</text>
            <rect x="22" y="60" width="100" height="18" rx="4" fill="#e8f4fd" stroke="#0078d4"/>
            <text x="72" y="73" textAnchor="middle" className="fill-slate-600 text-[7px]">Durable Orch</text>
          </g>
        </g>

        {/* Arrow: Row 1 to Row 2 */}
        <line x1="720" y1="252" x2="720" y2="275" stroke="#64748b" strokeWidth="2" markerEnd="url(#arrow)"/>
        <line x1="720" y1="275" x2="40" y2="275" stroke="#64748b" strokeWidth="2"/>
        <line x1="40" y1="275" x2="40" y2="300" stroke="#64748b" strokeWidth="2" markerEnd="url(#arrow)"/>

        {/* ROW 2: PIPELINE STAGES - Row 2 (Stages 5-10) */}
        <g transform="translate(40, 295)">
          {/* Pipeline container - Row 2 */}
          <rect x="0" y="15" width="1110" height="100" rx="10" fill="#fff7ed" stroke="#ea580c" strokeWidth="2" strokeDasharray="6,3"/>
          
          {/* Stage 5 - Resource Type Batch */}
          <g transform="translate(10, 22)">
            <rect width="165" height="86" rx="6" fill="white" stroke="#0891b2" strokeWidth="1.5"/>
            <rect width="165" height="22" rx="6" fill="#0891b2"/>
            <text x="82" y="15" textAnchor="middle" fill="white" className="text-[9px] font-semibold">5. Resource Type Batch</text>
            <text x="82" y="40" textAnchor="middle" className="fill-slate-600 text-[8px]">Create batch jobs</text>
            <text x="82" y="52" textAnchor="middle" className="fill-slate-600 text-[8px]">for Resource Types</text>
            <rect x="32" y="60" width="100" height="18" rx="4" fill="#cffafe" stroke="#0891b2"/>
            <text x="82" y="73" textAnchor="middle" className="fill-slate-600 text-[7px]">OpenAI Batch</text>
          </g>
          
          {/* Arrow 5→6 */}
          <line x1="180" y1="65" x2="190" y2="65" stroke="#64748b" strokeWidth="2" markerEnd="url(#arrow)"/>
          
          {/* Stage 6 - Resource Type Processing */}
          <g transform="translate(195, 22)">
            <rect width="165" height="86" rx="6" fill="white" stroke="#0891b2" strokeWidth="1.5"/>
            <rect width="165" height="22" rx="6" fill="#0891b2"/>
            <text x="82" y="15" textAnchor="middle" fill="white" className="text-[9px] font-semibold">6. Resource Type Proc</text>
            <text x="82" y="40" textAnchor="middle" className="fill-slate-600 text-[8px]">Process batch results</text>
            <text x="82" y="52" textAnchor="middle" className="fill-slate-600 text-[8px]">(Timer triggered)</text>
            <rect x="32" y="60" width="100" height="18" rx="4" fill="#cffafe" stroke="#0891b2"/>
            <text x="82" y="73" textAnchor="middle" className="fill-slate-600 text-[7px]">Timer Poller</text>
          </g>
          
          {/* Arrow 6→7 */}
          <line x1="365" y1="65" x2="375" y2="65" stroke="#64748b" strokeWidth="2" markerEnd="url(#arrow)"/>
          
          {/* Stage 7 - OCR Batch */}
          <g transform="translate(380, 22)">
            <rect width="140" height="86" rx="6" fill="white" stroke="#7c3aed" strokeWidth="1.5"/>
            <rect width="140" height="22" rx="6" fill="#7c3aed"/>
            <text x="70" y="15" textAnchor="middle" fill="white" className="text-[9px] font-semibold">7. OCR Batch</text>
            <text x="70" y="40" textAnchor="middle" className="fill-slate-600 text-[8px]">Create batch jobs</text>
            <text x="70" y="52" textAnchor="middle" className="fill-slate-600 text-[8px]">for OCR</text>
            <rect x="20" y="60" width="100" height="18" rx="4" fill="#ede9fe" stroke="#7c3aed"/>
            <text x="70" y="73" textAnchor="middle" className="fill-slate-600 text-[7px]">GPT-4.1 Vision</text>
          </g>
          
          {/* Arrow 7→8 */}
          <line x1="525" y1="65" x2="535" y2="65" stroke="#64748b" strokeWidth="2" markerEnd="url(#arrow)"/>
          
          {/* Stage 8 - OCR Processing */}
          <g transform="translate(540, 22)">
            <rect width="140" height="86" rx="6" fill="white" stroke="#7c3aed" strokeWidth="1.5"/>
            <rect width="140" height="22" rx="6" fill="#7c3aed"/>
            <text x="70" y="15" textAnchor="middle" fill="white" className="text-[9px] font-semibold">8. OCR Processing</text>
            <text x="70" y="40" textAnchor="middle" className="fill-slate-600 text-[8px]">Extract text from</text>
            <text x="70" y="52" textAnchor="middle" className="fill-slate-600 text-[8px]">images (Timer)</text>
            <rect x="20" y="60" width="100" height="18" rx="4" fill="#ede9fe" stroke="#7c3aed"/>
            <text x="70" y="73" textAnchor="middle" className="fill-slate-600 text-[7px]">Timer Poller</text>
          </g>
          
          {/* Arrow 8→9 */}
          <line x1="685" y1="65" x2="695" y2="65" stroke="#64748b" strokeWidth="2" markerEnd="url(#arrow)"/>
          
          {/* Stage 9 - Metadata Batch */}
          <g transform="translate(700, 22)">
            <rect width="155" height="86" rx="6" fill="white" stroke="#7c3aed" strokeWidth="1.5"/>
            <rect width="155" height="22" rx="6" fill="#7c3aed"/>
            <text x="77" y="15" textAnchor="middle" fill="white" className="text-[9px] font-semibold">9. Metadata Batch</text>
            <text x="77" y="40" textAnchor="middle" className="fill-slate-600 text-[8px]">Create batch jobs</text>
            <text x="77" y="52" textAnchor="middle" className="fill-slate-600 text-[8px]">for metadata</text>
            <rect x="27" y="60" width="100" height="18" rx="4" fill="#ede9fe" stroke="#7c3aed"/>
            <text x="77" y="73" textAnchor="middle" className="fill-slate-600 text-[7px]">GPT-4.1</text>
          </g>
          
          {/* Arrow 9→10 */}
          <line x1="860" y1="65" x2="870" y2="65" stroke="#64748b" strokeWidth="2" markerEnd="url(#arrow)"/>
          
          {/* Stage 10 - Metadata Extraction */}
          <g transform="translate(875, 22)">
            <rect width="155" height="86" rx="6" fill="white" stroke="#7c3aed" strokeWidth="1.5"/>
            <rect width="155" height="22" rx="6" fill="#7c3aed"/>
            <text x="77" y="15" textAnchor="middle" fill="white" className="text-[9px] font-semibold">10. Metadata Extract</text>
            <text x="77" y="40" textAnchor="middle" className="fill-slate-600 text-[8px]">Extract structured</text>
            <text x="77" y="52" textAnchor="middle" className="fill-slate-600 text-[8px]">data (Timer)</text>
            <rect x="27" y="60" width="100" height="18" rx="4" fill="#ede9fe" stroke="#7c3aed"/>
            <text x="77" y="73" textAnchor="middle" className="fill-slate-600 text-[7px]">Timer Poller</text>
          </g>
        </g>

        {/* Arrows: Pipeline to Storage/AI */}
        <line x1="180" y1="410" x2="180" y2="430" stroke="#059669" strokeWidth="2" markerEnd="url(#arrow-green)"/>
        <line x1="460" y1="410" x2="460" y2="430" stroke="#059669" strokeWidth="2" markerEnd="url(#arrow-green)"/>
        <line x1="805" y1="410" x2="805" y2="430" stroke="#7c3aed" strokeWidth="2" markerEnd="url(#arrow-purple)"/>

        {/* ROW 3: STORAGE & AI */}
        <g transform="translate(40, 440)">
          <text x="0" y="15" className="fill-slate-800 text-[12px] font-bold">③ DATA STORAGE</text>
          
          {/* Cosmos DB */}
          <rect x="0" y="25" width="260" height="120" rx="8" fill="#ecfdf5" stroke="#059669" strokeWidth="2" filter="url(#shadow)"/>
          <text x="130" y="45" textAnchor="middle" className="fill-slate-800 text-[12px] font-semibold">🗃️ Azure Cosmos DB</text>
          <g transform="translate(10, 55)">
            <rect width="70" height="30" rx="4" fill="#d1fae5" stroke="#10b981"/>
            <text x="35" y="14" textAnchor="middle" className="fill-slate-600 text-[9px] font-medium">records</text>
            <text x="35" y="25" textAnchor="middle" className="fill-slate-500 text-[8px]">Documents</text>
          </g>
          <g transform="translate(90, 55)">
            <rect width="70" height="30" rx="4" fill="#d1fae5" stroke="#10b981"/>
            <text x="35" y="14" textAnchor="middle" className="fill-slate-600 text-[9px] font-medium">batchstatus</text>
            <text x="35" y="25" textAnchor="middle" className="fill-slate-500 text-[8px]">Jobs</text>
          </g>
          <g transform="translate(170, 55)">
            <rect width="70" height="30" rx="4" fill="#d1fae5" stroke="#10b981"/>
            <text x="35" y="14" textAnchor="middle" className="fill-slate-600 text-[9px] font-medium">statistics</text>
            <text x="35" y="25" textAnchor="middle" className="fill-slate-500 text-[8px]">Aggregates</text>
          </g>
          <g transform="translate(10, 95)">
            <rect width="70" height="30" rx="4" fill="#d1fae5" stroke="#10b981"/>
            <text x="35" y="14" textAnchor="middle" className="fill-slate-600 text-[9px] font-medium">audit</text>
            <text x="35" y="25" textAnchor="middle" className="fill-slate-500 text-[8px]">Change Log</text>
          </g>
          <g transform="translate(90, 95)">
            <rect width="70" height="30" rx="4" fill="#d1fae5" stroke="#10b981"/>
            <text x="35" y="14" textAnchor="middle" className="fill-slate-600 text-[9px] font-medium">epub</text>
            <text x="35" y="25" textAnchor="middle" className="fill-slate-500 text-[8px]">EPUB Docs</text>
          </g>
          
          {/* Blob Storage */}
          <g transform="translate(290, 0)">
            <rect x="0" y="25" width="260" height="120" rx="8" fill="#ecfdf5" stroke="#059669" strokeWidth="2" filter="url(#shadow)"/>
            <text x="130" y="45" textAnchor="middle" className="fill-slate-800 text-[12px] font-semibold">📦 Azure Blob Storage</text>
            <g transform="translate(10, 55)">
              <rect width="70" height="30" rx="4" fill="#d1fae5" stroke="#10b981"/>
              <text x="35" y="14" textAnchor="middle" className="fill-slate-600 text-[9px] font-medium">originals</text>
              <text x="35" y="25" textAnchor="middle" className="fill-slate-500 text-[8px]">Source Files</text>
            </g>
            <g transform="translate(90, 55)">
              <rect width="70" height="30" rx="4" fill="#d1fae5" stroke="#10b981"/>
              <text x="35" y="14" textAnchor="middle" className="fill-slate-600 text-[9px] font-medium">thumbnails</text>
              <text x="35" y="25" textAnchor="middle" className="fill-slate-500 text-[8px]">Previews</text>
            </g>
            <g transform="translate(170, 55)">
              <rect width="70" height="30" rx="4" fill="#d1fae5" stroke="#10b981"/>
              <text x="35" y="14" textAnchor="middle" className="fill-slate-600 text-[9px] font-medium">ocr-text</text>
              <text x="35" y="25" textAnchor="middle" className="fill-slate-500 text-[8px]">Extracted</text>
            </g>
            <g transform="translate(10, 95)">
              <rect width="70" height="30" rx="4" fill="#d1fae5" stroke="#10b981"/>
              <text x="35" y="14" textAnchor="middle" className="fill-slate-600 text-[9px] font-medium">batch-io</text>
              <text x="35" y="25" textAnchor="middle" className="fill-slate-500 text-[8px]">AI Batches</text>
            </g>
            <g transform="translate(90, 95)">
              <rect width="70" height="30" rx="4" fill="#d1fae5" stroke="#10b981"/>
              <text x="35" y="14" textAnchor="middle" className="fill-slate-600 text-[9px] font-medium">epub-files</text>
              <text x="35" y="25" textAnchor="middle" className="fill-slate-500 text-[8px]">EPUB Data</text>
            </g>
          </g>
        </g>

        {/* AI Services */}
        <g transform="translate(620, 440)">
          <text x="0" y="15" className="fill-slate-800 text-[12px] font-bold">④ AI SERVICES</text>
          <rect x="0" y="25" width="280" height="120" rx="8" fill="#faf5ff" stroke="#7c3aed" strokeWidth="2" filter="url(#shadow)"/>
          <text x="140" y="45" textAnchor="middle" className="fill-slate-800 text-[12px] font-semibold">🤖 Azure OpenAI</text>
          <rect x="15" y="55" width="250" height="32" rx="4" fill="#ede9fe" stroke="#8b5cf6"/>
          <text x="140" y="72" textAnchor="middle" className="fill-slate-700 text-[10px] font-medium">GPT-4.1 Vision (OCR)</text>
          <text x="140" y="84" textAnchor="middle" className="fill-slate-500 text-[8px]">Image → Text Extraction</text>
          <rect x="15" y="95" width="250" height="32" rx="4" fill="#ede9fe" stroke="#8b5cf6"/>
          <text x="140" y="112" textAnchor="middle" className="fill-slate-700 text-[10px] font-medium">GPT-4.1 (Metadata + Resource Types)</text>
          <text x="140" y="124" textAnchor="middle" className="fill-slate-500 text-[8px]">Entity & Structure Extraction</text>
        </g>

        {/* Timer Functions */}
        <g transform="translate(930, 440)">
          <text x="0" y="15" className="fill-slate-800 text-[12px] font-bold">⏱️ POLLERS</text>
          <rect x="0" y="25" width="180" height="120" rx="8" fill="#fff7ed" stroke="#ea580c" strokeWidth="2" filter="url(#shadow)"/>
          <text x="90" y="45" textAnchor="middle" className="fill-slate-800 text-[11px] font-semibold">Timer Functions</text>
          <text x="90" y="62" textAnchor="middle" className="fill-slate-500 text-[8px]">Resource Type Poller</text>
          <line x1="20" y1="70" x2="160" y2="70" stroke="#fed7aa"/>
          <text x="90" y="85" textAnchor="middle" className="fill-slate-500 text-[8px]">OCR Batch Poller</text>
          <line x1="20" y1="93" x2="160" y2="93" stroke="#fed7aa"/>
          <text x="90" y="108" textAnchor="middle" className="fill-slate-500 text-[8px]">Metadata Batch Poller</text>
        </g>

        {/* LEGEND */}
        <g transform="translate(40, 610)">
          <rect x="0" y="0" width="1070" height="95" rx="8" fill="#f8fafc" stroke="#e2e8f0"/>
          <text x="15" y="22" className="fill-slate-800 text-[12px] font-bold">LEGEND</text>
          
          <g transform="translate(15, 35)">
            <rect width="14" height="14" rx="3" fill="#fff7ed" stroke="#ea580c" strokeWidth="1.5"/>
            <text x="20" y="11" className="fill-slate-600 text-[10px]">Azure Function</text>
          </g>
          <g transform="translate(140, 35)">
            <rect width="14" height="14" rx="3" fill="#ecfdf5" stroke="#059669" strokeWidth="1.5"/>
            <text x="20" y="11" className="fill-slate-600 text-[10px]">Storage</text>
          </g>
          <g transform="translate(230, 35)">
            <rect width="14" height="14" rx="3" fill="#faf5ff" stroke="#7c3aed" strokeWidth="1.5"/>
            <text x="20" y="11" className="fill-slate-600 text-[10px]">AI Service</text>
          </g>
          <g transform="translate(340, 35)">
            <rect width="14" height="14" rx="3" fill="#fef3c7" stroke="#d97706" strokeWidth="1.5"/>
            <text x="20" y="11" className="fill-slate-600 text-[10px]">External</text>
          </g>
          <g transform="translate(440, 35)">
            <rect width="14" height="14" rx="3" fill="#cffafe" stroke="#0891b2" strokeWidth="1.5"/>
            <text x="20" y="11" className="fill-slate-600 text-[10px]">Resource Type</text>
          </g>
          
          <g transform="translate(15, 60)">
            <line x1="0" y1="7" x2="20" y2="7" stroke="#64748b" strokeWidth="2" markerEnd="url(#arrow)"/>
            <text x="28" y="11" className="fill-slate-600 text-[10px]">Data Flow</text>
          </g>
          
          <g transform="translate(500, 32)">
            <text x="0" y="0" className="fill-slate-700 text-[10px] font-semibold">Pipeline Flow (10 stages):</text>
            <text x="0" y="14" className="fill-slate-500 text-[9px]">1. Content Source Sync → 2. Related Assets → 3. Asset Details → 4. Download Files</text>
            <text x="0" y="28" className="fill-slate-500 text-[9px]">5. Resource Type Batch → 6. Resource Type Proc → 7. OCR Batch → 8. OCR Proc</text>
            <text x="0" y="42" className="fill-slate-500 text-[9px]">9. Metadata Batch → 10. Metadata Extract • All results stored in Cosmos DB & Blob</text>
          </g>
        </g>
        
        <text x="1090" y="712" textAnchor="end" className="fill-slate-400 text-[9px]">v2.0 • Archivist Data Pipeline (10 stages)</text>
      </svg>
    </div>
  )
}
