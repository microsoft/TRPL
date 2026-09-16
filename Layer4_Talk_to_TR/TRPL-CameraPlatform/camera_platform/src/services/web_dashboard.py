# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Web Dashboard service plugin
Adds Web UI interface to the API
"""
from flask import render_template_string, jsonify


DASHBOARD_HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Room Monitor</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: "Segoe UI", monospace;
            background: #0d0d0d;
            color: #ddd;
            height: 100vh;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }

        /* ── Top bar ── */
        #topbar {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 8px 16px;
            background: #161616;
            border-bottom: 1px solid #2a2a2a;
            flex-shrink: 0;
        }
        #topbar h1 { font-size: 1em; color: #7aa2f7; letter-spacing: 1px; white-space: nowrap; }
        #status-dot {
            width: 10px; height: 10px; border-radius: 50%;
            background: #444; flex-shrink: 0;
        }
        #status-dot.running { background: #9ece6a; box-shadow: 0 0 6px #9ece6a; }
        #status-dot.stopped { background: #444; }
        #status-text { font-size: 0.8em; color: #666; white-space: nowrap; }

        /* Upload area */
        #upload-area {
            margin-left: auto;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        #file-label {
            padding: 5px 14px;
            background: #1e2030;
            border: 1px solid #3b4261;
            border-radius: 6px;
            font-size: 0.82em;
            color: #a9b1d6;
            cursor: pointer;
            white-space: nowrap;
        }
        #file-label:hover { border-color: #7aa2f7; color: #7aa2f7; }
        #file-input { display: none; }
        #upload-status { font-size: 0.78em; color: #565f89; max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

        #btn-start, #btn-camera {
            padding: 6px 22px;
            background: #7aa2f7;
            color: #0d0d0d;
            border: none;
            border-radius: 6px;
            font-size: 0.9em;
            font-weight: bold;
            cursor: pointer;
            letter-spacing: 0.5px;
            white-space: nowrap;
        }
        #btn-camera { background: #9ece6a; }
        #btn-start:hover:not(:disabled), #btn-camera:hover:not(:disabled) { background: #a3bfff; }
        #btn-start:disabled, #btn-camera:disabled { background: #333; color: #555; cursor: default; }

        /* ── Main layout ── */
        #main {
            display: flex;
            flex: 1;
            overflow: hidden;
            gap: 0;
        }

        /* ── Video panel ── */
        #video-panel {
            flex: 0 0 auto;
            width: 62%;
            display: flex;
            align-items: center;
            justify-content: center;
            background: #000;
            border-right: 1px solid #2a2a2a;
            position: relative;
        }
        #video-panel img {
            max-width: 100%;
            max-height: 100%;
            object-fit: contain;
            display: block;
        }
        #video-placeholder {
            color: #333;
            font-size: 0.9em;
            text-align: center;
            line-height: 2;
        }

        /* ── Right column ── */
        #right-col {
            flex: 1;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }

        /* ── Stats strip ── */
        #stats-strip {
            display: flex;
            gap: 0;
            border-bottom: 1px solid #2a2a2a;
            flex-shrink: 0;
        }
        .stat-cell {
            flex: 1;
            padding: 8px 10px;
            text-align: center;
            border-right: 1px solid #2a2a2a;
        }
        .stat-cell:last-child { border-right: none; }
        .stat-label { font-size: 0.65em; color: #555; text-transform: uppercase; letter-spacing: 1px; }
        .stat-val { font-size: 1.4em; font-weight: bold; color: #7aa2f7; margin-top: 2px; }
        .stat-val.alert { color: #f7768e; }
        .stat-val.ok { color: #9ece6a; }

        /* ── Log panel ── */
        #log-panel {
            flex: 1;
            overflow-y: auto;
            padding: 8px 6px;
            font-family: monospace;
            font-size: 0.76em;
            line-height: 1.6;
        }
        #log-panel::-webkit-scrollbar { width: 4px; }
        #log-panel::-webkit-scrollbar-thumb { background: #333; }

        .log-row { padding: 1px 4px; border-radius: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .log-row:hover { background: #1a1a1a; white-space: normal; word-break: break-all; }

        .tag-EVT  { color: #565f89; }
        .tag-ORCH { color: #9ece6a; font-weight: bold; }
        .tag-VLM  { color: #e0af68; }

        .ts { color: #3b4261; margin-right: 4px; }
        .tag { margin-right: 6px; }
        .msg-EVT  { color: #a9b1d6; }
        .msg-ORCH { color: #c3e88d; }
        .msg-VLM  { color: #ffc777; }

        /* ── Event timeline ── */
        #event-panel {
            border-top: 1px solid #2a2a2a;
            height: 140px;
            overflow-y: auto;
            padding: 6px 8px;
            flex-shrink: 0;
        }
        #event-panel h3 { font-size: 0.7em; color: #444; letter-spacing: 1px; text-transform: uppercase; margin-bottom: 4px; }
        .ev { display: flex; gap: 8px; font-size: 0.72em; padding: 2px 0; border-bottom: 1px solid #1a1a1a; }
        .ev-time { color: #3b4261; min-width: 56px; }
        .ev-type { min-width: 90px; font-weight: bold; }
        .ev-type.hand_raised { color: #f7768e; }
        .ev-type.person_entered { color: #9ece6a; }
        .ev-type.person_left { color: #e0af68; }
        .ev-type.hand_lowered { color: #7aa2f7; }
        .ev-detail { color: #565f89; }
    </style>
</head>
<body>
    <div id="topbar">
        <div id="status-dot" class="stopped"></div>
        <h1>ROOM MONITOR</h1>
        <span id="status-text">Stopped</span>
        <div id="upload-area">
            <label id="file-label" for="file-input">📁 Choose Video</label>
            <input id="file-input" type="file" accept=".mp4,.mov,.avi,.mkv,.webm" onchange="onFileChosen(this)">
            <span id="upload-status">No file selected</span>
            <button id="btn-start" onclick="startMonitor()">▶ Start</button>
            <button id="btn-camera" onclick="useConfiguredCamera()">📷 Use Camera</button>
            <a id="btn-zones" href="/label" target="_blank"
               style="margin-left:10px;padding:6px 12px;border-radius:6px;background:#2b3543;color:#e6edf3;text-decoration:none;font-size:13px;">📐 Label Zones</a>
            <a id="btn-monitor" href="/monitor" target="_blank"
               style="margin-left:8px;padding:6px 12px;border-radius:6px;background:#2b3543;color:#e6edf3;text-decoration:none;font-size:13px;">📡 WS Monitor</a>
        </div>
    </div>

    <div id="main">
        <!-- Video -->
        <div id="video-panel">
            <div id="video-placeholder">
                <div style="font-size:2em;margin-bottom:8px">📷</div>
                Press Start to begin
            </div>
            <img id="video-img" src="" style="display:none" alt="live feed">
        </div>

        <!-- Right column -->
        <div id="right-col">
            <!-- Stats -->
            <div id="stats-strip">
                <div class="stat-cell">
                    <div class="stat-label">Persons</div>
                    <div class="stat-val" id="s-persons">-</div>
                </div>
                <div class="stat-cell">
                    <div class="stat-label">Hands Up</div>
                    <div class="stat-val alert" id="s-raising">-</div>
                </div>
                <div class="stat-cell">
                    <div class="stat-label">Events/60s</div>
                    <div class="stat-val" id="s-events">-</div>
                </div>
                <div class="stat-cell">
                    <div class="stat-label">FPS</div>
                    <div class="stat-val ok" id="s-fps">-</div>
                </div>
            </div>

            <!-- Scrolling log -->
            <div id="log-panel"></div>

            <!-- Recent events -->
            <div id="event-panel">
                <h3>Event Timeline</h3>
                <div id="event-list"></div>
            </div>
        </div>
    </div>

    <script>
        const API = window.location.origin + "/api";
        let running = false;
        let logEntries = [];
        let lastLogCount = 0;

        const EVENT_LABELS = {
            hand_raised: "Hand Raised", hand_lowered: "Hand Lowered",
            person_entered: "Entered", person_left: "Left",
            person_moved: "Moved", pose_changed: "Pose",
        };

        function fmtTime(ts) {
            return new Date(ts * 1000).toLocaleTimeString("en-US", {hour12: false});
        }

        let uploadedFile = null;

        function onFileChosen(input) {
            const f = input.files[0];
            if (!f) return;
            uploadedFile = f;
            document.getElementById("upload-status").textContent = f.name;
            document.getElementById("btn-start").disabled = false;
        }

        async function startMonitor() {
            const btn = document.getElementById("btn-start");
            btn.disabled = true;
            btn.textContent = "Starting…";

            try {
                // If the user picked a file, upload it as the camera source first.
                // Otherwise use whatever is configured in src/utils/config.py
                // (e.g. live camera, RealSense, MJPEG URL).
                if (uploadedFile) {
                    btn.textContent = "Uploading…";
                    const form = new FormData();
                    form.append("video", uploadedFile);
                    const ur = await fetch(API + "/control/upload", {method: "POST", body: form});
                    const ud = await ur.json();
                    if (!ur.ok) {
                        alert("Upload failed: " + (ud.error || ur.status));
                        btn.disabled = false;
                        btn.textContent = "▶ Start";
                        return;
                    }
                    btn.textContent = "Starting…";
                }

                const sr = await fetch(API + "/control/start", {method: "POST"});
                const sd = await sr.json();
                if (!sr.ok) {
                    showStartError(sd.error || `Camera failed to start (${sr.status})`);
                    return;
                }
                if (sd.status === "started" || sd.status === "already_running") {
                    setRunning(true);
                }
            } catch(e) {
                showStartError("Camera start request failed: " + e.message);
            }
        }

        async function useConfiguredCamera() {
            const btn = document.getElementById("btn-camera");
            btn.disabled = true;
            btn.textContent = "Switching…";
            try {
                const r = await fetch(API + "/control/use-configured-source", {method: "POST"});
                const d = await r.json();
                if (!r.ok) {
                    showStartError(d.error || `Camera failed to start (${r.status})`);
                    return;
                }
                uploadedFile = null;
                document.getElementById("file-input").value = "";
                document.getElementById("upload-status").textContent = "Using configured camera";
                setRunning(true);
            } catch(e) {
                showStartError("Camera switch request failed: " + e.message);
            } finally {
                btn.disabled = false;
                btn.textContent = "📷 Use Camera";
            }
        }

        function showStartError(message) {
            setRunning(false);
            const ph = document.getElementById("video-placeholder");
            ph.textContent = message;
            ph.style.display = "block";
        }

        function setRunning(on) {
            running = on;
            const dot = document.getElementById("status-dot");
            const txt = document.getElementById("status-text");
            const btn = document.getElementById("btn-start");
            dot.className = on ? "running" : "stopped";
            txt.textContent = on ? "Running" : "Stopped";
            btn.disabled = on;
            btn.textContent = on ? "● Running" : "▶ Start";

            const img = document.getElementById("video-img");
            const ph  = document.getElementById("video-placeholder");
            if (on) {
                img.src = "/video_feed";
                img.style.display = "block";
                ph.style.display  = "none";
            } else {
                img.src = "";
                img.style.display = "none";
                ph.style.display = "block";
            }
        }

        async function pollStatus() {
            try {
                const r = await fetch(API + "/control/status");
                const d = await r.json();
                if (d.running !== running) setRunning(d.running);
                if (!d.running && d.error) showStartError(d.error);
            } catch(e) {}
        }

        async function pollLog() {
            try {
                const r = await fetch(API + "/display_log");
                const entries = await r.json();
                if (entries.length !== lastLogCount) {
                    lastLogCount = entries.length;
                    const panel = document.getElementById("log-panel");
                    const atBottom = panel.scrollHeight - panel.scrollTop - panel.clientHeight < 60;
                    panel.innerHTML = entries.map(e => {
                        const tag = e.tag;
                        return `<div class="log-row">
                            <span class="ts">${e.ts}</span>
                            <span class="tag tag-${tag}">[${tag}]</span>
                            <span class="msg-${tag}">${escHtml(e.msg)}</span>
                        </div>`;
                    }).join("");
                    if (atBottom) panel.scrollTop = panel.scrollHeight;
                }
            } catch(e) {}
        }

        async function pollStats() {
            try {
                const [sr, er, cr] = await Promise.all([
                    fetch(API + "/room/summary"),
                    fetch(API + "/events?time_window=60"),
                    fetch(API + "/cameras"),
                ]);
                if (!sr.ok) return;
                const s = await sr.json();
                const ev = await er.json();
                const cam = await cr.json();

                document.getElementById("s-persons").textContent = s.total_persons_in_room ?? "-";
                document.getElementById("s-raising").textContent = s.persons_raising_hands ?? "-";
                document.getElementById("s-events").textContent  = ev.count ?? "-";

                // FPS from first camera
                const fps = cam.cameras?.[0]?.fps;
                document.getElementById("s-fps").textContent = fps != null ? fps.toFixed(1) : "-";

                // Events list (newest first)
                const events = (ev.events || []).slice().reverse();
                document.getElementById("event-list").innerHTML = events.slice(0, 30).map(e => {
                    const label = EVENT_LABELS[e.event_type] || e.event_type;
                    let detail = e.person_id >= 0 ? "P"+e.person_id : "";
                    if (e.data?.side)   detail += " ["+e.data.side+"]";
                    if (e.data?.reason) detail += " ("+e.data.reason+")";
                    return `<div class="ev">
                        <span class="ev-time">${fmtTime(e.timestamp)}</span>
                        <span class="ev-type ${e.event_type||''}">${label}</span>
                        <span class="ev-detail">${escHtml(detail)}</span>
                    </div>`;
                }).join("");
            } catch(e) {}
        }

        function escHtml(s) {
            return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
        }

        // Poll at different rates
        pollStatus();
        setInterval(pollStatus, 3000);
        setInterval(pollLog,    800);
        setInterval(pollStats,  2000);
    </script>
</body>
</html>
'''


def add_web_dashboard(app):
    """Add web dashboard to Flask app"""

    @app.route("/", methods=["GET"])
    def index():
        return render_template_string(DASHBOARD_HTML)

    @app.route("/dashboard", methods=["GET"])
    def dashboard():
        return render_template_string(DASHBOARD_HTML)

    @app.route("/api", methods=["GET"])
    def api_info():
        return jsonify({
            "name": "Room Monitoring System API",
            "version": "1.0.0",
            "endpoints": {
                "dashboard": "/",
                "health": "/api/health",
                "room_summary": "/api/room/summary",
                "persons": "/api/room/persons",
                "cameras": "/api/cameras",
                "events": "/api/events",
                "control_start": "/api/control/start (POST)",
                "control_status": "/api/control/status (GET)",
                "display_log": "/api/display_log (GET)",
                "video_feed": "/video_feed",
                "webhooks_list": "/api/webhooks (GET)",
                "webhooks_register": "/api/webhooks (POST)",
                "webhooks_delete": "/api/webhooks/<name> (DELETE)",
            }
        })
