"""
Web zone labeler page (served at /label).

A self-contained HTML/JS page: it loads a still snapshot from the camera as a
background, lets the user click polygon vertices for the entry zone and the
speaking zone (mic_zone), then POSTs them to /api/zones which persists + live-
reloads them into the running system. No build step, no dependencies.
"""

ZONE_LABEL_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Zone Labeler — Room Monitor</title>
<style>
  :root { --entry:#ffa500; --mic:#ff4cff; --dir:#34d399; --bg:#11151c; --panel:#1b212b; --fg:#e6edf3; --muted:#8b98a8; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, Segoe UI, Roboto, sans-serif; background:var(--bg); color:var(--fg); }
  header { padding:12px 18px; background:var(--panel); border-bottom:1px solid #2a3340; display:flex; align-items:center; gap:16px; flex-wrap:wrap; }
  header h1 { font-size:16px; margin:0; font-weight:600; }
  .spacer { flex:1; }
  main { display:flex; gap:16px; padding:16px; align-items:flex-start; flex-wrap:wrap; }
  #stage { position:relative; line-height:0; border:1px solid #2a3340; border-radius:6px; overflow:hidden; background:#000; }
  #snap { display:block; max-width:78vw; max-height:80vh; user-select:none; -webkit-user-drag:none; }
  #canvas { position:absolute; left:0; top:0; cursor:crosshair; }
  aside { width:260px; background:var(--panel); border:1px solid #2a3340; border-radius:6px; padding:14px; }
  .grp { margin-bottom:16px; }
  .zone-btn { display:flex; align-items:center; gap:8px; width:100%; padding:9px 10px; margin:6px 0; border-radius:6px;
              border:1px solid #2a3340; background:#222a35; color:var(--fg); cursor:pointer; font-size:14px; text-align:left; }
  .zone-btn.active { outline:2px solid currentColor; }
  .zone-btn .sw { width:14px; height:14px; border-radius:3px; flex:none; }
  .zone-btn .cnt { margin-left:auto; color:var(--muted); font-size:12px; }
  button.act { width:100%; padding:9px; margin:5px 0; border-radius:6px; border:1px solid #2a3340; background:#222a35; color:var(--fg); cursor:pointer; font-size:13px; }
  button.act:hover { background:#2b3543; }
  button.primary { background:#2563eb; border-color:#2563eb; font-weight:600; }
  button.primary:hover { background:#1d4ed8; }
  .hint { color:var(--muted); font-size:12px; line-height:1.5; }
  #status { padding:8px 12px; font-size:13px; min-height:20px; }
  #status.ok { color:#56d364; } #status.err { color:#f85149; }
  kbd { background:#2a3340; border-radius:3px; padding:1px 5px; font-size:11px; }
</style>
</head>
<body>
<header>
  <h1>Zone Labeler</h1>
  <span class="hint">Click to add vertices · right-click to undo · zones apply live to the running system</span>
  <span class="spacer"></span>
  <span id="status"></span>
</header>
<main>
  <div id="stage">
    <img id="snap" alt="camera snapshot"/>
    <canvas id="canvas"></canvas>
  </div>
  <aside>
    <div class="grp">
      <div class="hint" style="margin-bottom:6px;">Editing zone:</div>
      <button class="zone-btn active" id="btn-entry" data-zone="entry_zone" style="color:var(--entry)">
        <span class="sw" style="background:var(--entry)"></span> Entry zone <span class="cnt" id="cnt-entry">0 pts</span>
      </button>
      <button class="zone-btn" id="btn-mic" data-zone="mic_zone" style="color:var(--mic)">
        <span class="sw" style="background:var(--mic)"></span> Speaking zone <span class="cnt" id="cnt-mic">0 pts</span>
      </button>
      <button class="zone-btn" id="btn-dir" data-zone="entry_direction" style="color:var(--dir)">
        <span class="sw" style="background:var(--dir)"></span> Entry direction <span class="cnt" id="cnt-dir">unset</span>
      </button>
    </div>
    <div class="grp">
      <div class="hint">
        <b style="color:var(--dir)">Entry direction</b>: click the <b>door-side</b> end,
        then the <b>room-side</b> end. The arrow points <b>into the room</b>; only
        people walking that way are welcomed (people leaving are ignored).
      </div>
    </div>
    <div class="grp">
      <button class="act" id="undo">Undo last point</button>
      <button class="act" id="clear">Clear this zone</button>
      <button class="act" id="refresh">Refresh snapshot</button>
    </div>
    <div class="grp">
      <button class="act primary" id="save">Save &amp; apply</button>
    </div>
    <div class="hint">
      Each zone needs at least <b>3</b> points. Saving writes
      <kbd>config/zones.json</kbd> and reloads zones into the live monitor
      immediately (no restart).
    </div>
  </aside>
</main>
<script>
const COLORS = { entry_zone: "#ffa500", mic_zone: "#ff4cff", entry_direction: "#34d399" };
const LABELS = { entry_zone: "entry", mic_zone: "speaking" };
const zones = { entry_zone: [], mic_zone: [] };   // normalized [x,y]
const arrow = { from: null, to: null };           // entry-direction arrow, normalized [x,y]
let active = "entry_zone";

const img = document.getElementById("snap");
const canvas = document.getElementById("canvas");
const ctx = canvas.getContext("2d");
const statusEl = document.getElementById("status");

function setStatus(msg, kind){ statusEl.textContent = msg; statusEl.className = kind || ""; }

function sizeCanvas(){
  canvas.width = img.clientWidth;
  canvas.height = img.clientHeight;
  draw();
}

function draw(){
  ctx.clearRect(0,0,canvas.width,canvas.height);
  for (const name of ["entry_zone","mic_zone"]){
    const pts = zones[name];
    if (!pts.length) continue;
    const col = COLORS[name];
    ctx.lineWidth = 2; ctx.strokeStyle = col;
    ctx.fillStyle = col + "33";
    ctx.beginPath();
    pts.forEach((p,i)=>{ const x=p[0]*canvas.width, y=p[1]*canvas.height;
      i===0?ctx.moveTo(x,y):ctx.lineTo(x,y); });
    if (pts.length>=3) ctx.closePath();
    if (pts.length>=3) ctx.fill();
    ctx.stroke();
    pts.forEach((p,i)=>{ const x=p[0]*canvas.width, y=p[1]*canvas.height;
      ctx.fillStyle=col; ctx.beginPath(); ctx.arc(x,y,5,0,7); ctx.fill();
      ctx.fillStyle="#000"; ctx.font="11px sans-serif"; ctx.fillText(i, x+7, y-6);
    });
    const lx=pts[0][0]*canvas.width, ly=pts[0][1]*canvas.height;
    ctx.fillStyle=col; ctx.font="bold 14px sans-serif";
    ctx.fillText(LABELS[name]+" zone", lx+8, ly-12);
  }
  drawArrow();
  document.getElementById("cnt-entry").textContent = zones.entry_zone.length + " pts";
  document.getElementById("cnt-mic").textContent   = zones.mic_zone.length + " pts";
  document.getElementById("cnt-dir").textContent   =
    (arrow.from && arrow.to) ? "set" : (arrow.from ? "1/2" : "unset");
}

// Render the entry-direction arrow (tail → head = "into the room").
function drawArrow(){
  if (!arrow.from) return;
  const col = COLORS.entry_direction;
  const ax = arrow.from[0]*canvas.width, ay = arrow.from[1]*canvas.height;
  ctx.fillStyle = col; ctx.strokeStyle = col;
  if (!arrow.to){
    // Only the tail placed so far — show it and prompt for the head.
    ctx.beginPath(); ctx.arc(ax, ay, 5, 0, 7); ctx.fill();
    ctx.font = "12px sans-serif";
    ctx.fillText("click room-side end…", ax+8, ay-8);
    return;
  }
  const bx = arrow.to[0]*canvas.width, by = arrow.to[1]*canvas.height;
  ctx.lineWidth = 3;
  ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
  // Arrowhead at the head (room-side) end.
  const ang = Math.atan2(by-ay, bx-ax), h = 15;
  ctx.beginPath();
  ctx.moveTo(bx, by);
  ctx.lineTo(bx - h*Math.cos(ang - Math.PI/6), by - h*Math.sin(ang - Math.PI/6));
  ctx.lineTo(bx - h*Math.cos(ang + Math.PI/6), by - h*Math.sin(ang + Math.PI/6));
  ctx.closePath(); ctx.fill();
  // Tail dot + label.
  ctx.beginPath(); ctx.arc(ax, ay, 4, 0, 7); ctx.fill();
  ctx.font = "bold 13px sans-serif";
  ctx.fillText("into room", (ax+bx)/2 + 8, (ay+by)/2 - 8);
}

canvas.addEventListener("click", (e)=>{
  const r = canvas.getBoundingClientRect();
  const nx = +Math.min(1, Math.max(0, (e.clientX-r.left)/r.width)).toFixed(4);
  const ny = +Math.min(1, Math.max(0, (e.clientY-r.top)/r.height)).toFixed(4);
  if (active === "entry_direction"){
    // First click sets the tail (door side); second sets the head (room side).
    // A third click starts a fresh arrow.
    if (!arrow.from || (arrow.from && arrow.to)){ arrow.from = [nx, ny]; arrow.to = null; }
    else { arrow.to = [nx, ny]; }
    draw(); return;
  }
  zones[active].push([nx, ny]);
  draw();
});
canvas.addEventListener("contextmenu",(e)=>{ e.preventDefault();
  if (active === "entry_direction"){ arrow.from = null; arrow.to = null; draw(); return; }
  zones[active].pop(); draw(); });

function selectZone(name){
  active = name;
  document.getElementById("btn-entry").classList.toggle("active", name==="entry_zone");
  document.getElementById("btn-mic").classList.toggle("active", name==="mic_zone");
  document.getElementById("btn-dir").classList.toggle("active", name==="entry_direction");
}
document.getElementById("btn-entry").onclick = ()=>selectZone("entry_zone");
document.getElementById("btn-mic").onclick   = ()=>selectZone("mic_zone");
document.getElementById("btn-dir").onclick   = ()=>selectZone("entry_direction");
document.getElementById("undo").onclick  = ()=>{
  if (active === "entry_direction"){ if (arrow.to) arrow.to = null; else arrow.from = null; }
  else { zones[active].pop(); }
  draw();
};
document.getElementById("clear").onclick = ()=>{
  if (active === "entry_direction"){ arrow.from = null; arrow.to = null; }
  else { zones[active] = []; }
  draw();
};
document.getElementById("refresh").onclick = ()=>loadSnapshot();

document.getElementById("save").onclick = async ()=>{
  const body = {};
  for (const name of ["entry_zone","mic_zone"])
    if (zones[name].length>=3) body[name]=zones[name];
  if (arrow.from && arrow.to) body.entry_direction = { from: arrow.from, to: arrow.to };
  if (!Object.keys(body).length){ setStatus("Need at least 3 points in a zone, or a 2-point arrow.","err"); return; }
  setStatus("Saving…");
  try{
    const res = await fetch("/api/zones",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const j = await res.json();
    if (!res.ok){ setStatus("Error: "+(j.error||res.status),"err"); return; }
    setStatus("Saved & applied: "+JSON.stringify(j.applied),"ok");
  }catch(err){ setStatus("Request failed: "+err,"err"); }
};

function loadSnapshot(){
  setStatus("Loading snapshot…");
  img.onload = ()=>{ sizeCanvas(); setStatus("Snapshot loaded.","ok"); };
  img.onerror = ()=>setStatus("Could not load snapshot (is the camera connected?)","err");
  img.src = "/api/snapshot?ts=" + Date.now();
}

async function loadExistingZones(){
  try{
    const res = await fetch("/api/zones"); const j = await res.json();
    for (const name of ["entry_zone","mic_zone"])
      if (Array.isArray(j[name])) zones[name] = j[name].map(p=>[+p[0],+p[1]]);
    const d = j.entry_direction;
    if (d && Array.isArray(d.from) && Array.isArray(d.to)){
      arrow.from = [+d.from[0], +d.from[1]];
      arrow.to   = [+d.to[0],   +d.to[1]];
    }
    draw();
  }catch(_){}
}

window.addEventListener("resize", sizeCanvas);
loadExistingZones();
loadSnapshot();
</script>
</body>
</html>
"""
