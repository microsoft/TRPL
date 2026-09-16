# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
WebSocket event monitor page (served at /monitor).

A read-only live view of the events camera_platform pushes over the WebSocket
event stream. It polls /api/events/recent (an in-memory tap on the publisher) —
it does NOT connect to the consume-once WS stack, so watching here never steals
events from the real downstream consumer.
"""

WS_MONITOR_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>WS Event Monitor — Camera Platform</title>
<style>
  :root { --bg:#0d1117; --panel:#161b22; --line:#21262d; --fg:#e6edf3; --muted:#8b949e; --green:#3fb950; --red:#f85149; --amber:#d29922; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; background:var(--bg); color:var(--fg); font-size:13px; }
  header { padding:10px 16px; background:var(--panel); border-bottom:1px solid var(--line); display:flex; align-items:center; gap:18px; flex-wrap:wrap; position:sticky; top:0; z-index:5; }
  header h1 { font-size:15px; margin:0; font-weight:600; }
  .stat { display:flex; flex-direction:column; line-height:1.25; }
  .stat .v { font-size:16px; font-weight:700; }
  .stat .k { font-size:10px; color:var(--muted); text-transform:uppercase; letter-spacing:.5px; }
  .dot { width:9px; height:9px; border-radius:50%; display:inline-block; margin-right:5px; }
  .on { background:var(--green); box-shadow:0 0 6px var(--green); } .off { background:var(--red); }
  .spacer { flex:1; }
  button, select { background:#21262d; color:var(--fg); border:1px solid var(--line); border-radius:6px; padding:6px 10px; font:inherit; cursor:pointer; }
  button:hover { background:#2d333b; }
  button.armed { outline:2px solid var(--amber); }
  #wrap { padding:0 12px 24px; }
  table { width:100%; border-collapse:collapse; }
  th { position:sticky; top:52px; text-align:left; color:var(--muted); font-weight:600; font-size:11px; text-transform:uppercase; letter-spacing:.5px; background:var(--bg); padding:8px 8px; border-bottom:1px solid var(--line); }
  td { padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
  td.t { white-space:nowrap; color:var(--muted); }
  td.pid { text-align:right; color:var(--muted); width:48px; }
  .chip { display:inline-block; padding:2px 8px; border-radius:10px; font-weight:700; font-size:11px; white-space:nowrap; }
  td.payload { color:#aab4c0; word-break:break-word; }
  td.payload pre { margin:0; white-space:pre-wrap; font:inherit; }
  tr.new { animation:flash 1s ease-out; }
  @keyframes flash { from { background:#1f6feb33; } to { background:transparent; } }
  .empty { color:var(--muted); padding:24px; text-align:center; }
</style>
</head>
<body>
<header>
  <h1>📡 WS Event Monitor</h1>
  <div class="stat"><span class="v" id="s-total">0</span><span class="k">shown</span></div>
  <div class="stat"><span class="v" id="s-published">0</span><span class="k">published</span></div>
  <div class="stat"><span class="v" id="s-buffered">0</span><span class="k">buffered</span></div>
  <div class="stat"><span class="v"><span class="dot off" id="cdot"></span><span id="s-clients">0</span></span><span class="k">ws clients</span></div>
  <div class="stat"><span class="v" id="s-rate">0</span><span class="k">/min</span></div>
  <div class="spacer"></div>
  <label class="k">filter</label>
  <select id="filter"><option value="">all types</option></select>
  <button id="pause">⏸ Pause</button>
  <button id="clear">Clear</button>
</header>
<div id="wrap">
  <table>
    <thead><tr><th style="width:96px">time</th><th style="width:200px">event_type</th><th class="pid">id</th><th>payload</th></tr></thead>
    <tbody id="rows"></tbody>
  </table>
  <div class="empty" id="empty">waiting for events… (stand in front of the camera to generate some)</div>
</div>
<script>
const COLORS = {
  hand_raised:'#f85149', hand_lowered:'#bc8cff',
  person_entered:'#388bfd', person_left:'#6e7681', person_moved:'#1f6feb', pose_changed:'#58a6ff',
  PERSON_ENTERED_ROOM:'#388bfd', MIC_ZONE_ENGAGED:'#3fb950', MIC_ZONE_LEFT:'#d29922',
  HAND_RAISE_RESPONSE:'#f85149', COLD_ROOM_INVITE:'#db61a2', BATCH_INVITE:'#db61a2',
  ROOM_DEMOGRAPHICS:'#bc8cff',
  VISITOR_HESITATING:'#d29922', VISITOR_IDLE_IN_ROOM:'#d29922',
};
function colorFor(t){ return COLORS[t] || '#8b949e'; }

let lastSeq = 0, paused = false, shown = 0;
const seenTypes = new Set();
const rows = document.getElementById('rows');
const filterSel = document.getElementById('filter');
const recent = [];  // timestamps for rate calc

function pidOf(p){
  if (p == null) return '';
  if (typeof p.person_id === 'number') return p.person_id;
  if (Array.isArray(p.person_ids)) return p.person_ids.join(',');
  if (Array.isArray(p.idle_person_ids)) return p.idle_person_ids.join(',');
  return '';
}
function fmtTime(iso){ try { return new Date(iso).toLocaleTimeString(); } catch(_){ return iso; } }

function addEvent(evt){
  const type = evt.event_type || '?';
  if (!seenTypes.has(type)){ seenTypes.add(type);
    const o=document.createElement('option'); o.value=type; o.textContent=type; filterSel.appendChild(o); }
  recent.push(Date.now());
  const tr = document.createElement('tr');
  tr.className='new'; tr.dataset.type=type;
  if (filterSel.value && filterSel.value!==type) tr.style.display='none';
  const payload = evt.payload ?? {};
  tr.innerHTML =
    `<td class="t">${fmtTime(evt.timestamp)}</td>`+
    `<td><span class="chip" style="background:${colorFor(type)}22;color:${colorFor(type)}">${type}</span></td>`+
    `<td class="pid">${pidOf(payload)}</td>`+
    `<td class="payload"><pre>${escapeHtml(JSON.stringify(payload))}</pre></td>`;
  rows.insertBefore(tr, rows.firstChild);
  shown++;
  while (rows.children.length > 500) rows.removeChild(rows.lastChild);
  document.getElementById('empty').style.display='none';
}
function escapeHtml(s){ return s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }

async function poll(){
  try {
    const r = await fetch(`/api/events/recent?after=${lastSeq}`);
    const j = await r.json();
    const st = j.stats || {};
    document.getElementById('s-published').textContent = st.published ?? 0;
    document.getElementById('s-buffered').textContent = st.buffered ?? 0;
    document.getElementById('s-clients').textContent = st.ws_clients ?? 0;
    const dot = document.getElementById('cdot');
    dot.className = 'dot ' + ((st.ws_clients>0)?'on':'off');
    if (typeof j.latest_seq === 'number') lastSeq = Math.max(lastSeq, j.latest_seq);
    if (!paused && j.events){ for (const e of j.events) addEvent(e); }
    document.getElementById('s-total').textContent = shown;
    const cutoff = Date.now()-60000; while(recent.length && recent[0]<cutoff) recent.shift();
    document.getElementById('s-rate').textContent = recent.length;
  } catch(e){ /* keep polling */ }
}
filterSel.onchange = ()=>{ const f=filterSel.value;
  for (const tr of rows.children) tr.style.display = (!f||tr.dataset.type===f)?'':'none'; };
document.getElementById('pause').onclick = (e)=>{ paused=!paused;
  e.target.textContent = paused?'▶ Resume':'⏸ Pause'; e.target.classList.toggle('armed',paused); };
document.getElementById('clear').onclick = ()=>{ rows.innerHTML=''; shown=0;
  document.getElementById('s-total').textContent=0; document.getElementById('empty').style.display='block'; };

poll();
setInterval(poll, 1000);
</script>
</body>
</html>
"""
