# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

# =====================================================================
# Camera Platform stack launcher (auto-start)
#   1. camera-stream  (Orbbec -> MJPEG on :5000)   [system Python]
#   2. camera_platform (perception, WS :8765, API :5001)  [venv Python]
#   3. POST /api/control/start to begin monitoring
#
# Idempotent: kills any existing instances first, so it doubles as a
# "restart the whole stack" script. Logs to ..\..\autostart_logs\.
#
# Auto-start install (per-user, no admin): a .vbs in the user's Startup folder
# runs this hidden at logon — see scripts/autostart_CameraPlatformStack.vbs and
# a scheduled task that runs this script at boot.
#
# Configure these paths for your machine via environment variables before
# running (or edit the defaults below to match your local layout):
#   CAMERA_STACK_SYSTEM_PYTHON  - system Python interpreter used for camera-stream
#   CAMERA_STACK_VENV_PYTHON    - venv Python interpreter used for camera_platform
#   CAMERA_STACK_STREAM_DIR     - camera-stream working directory
#   CAMERA_STACK_PLATFORM_DIR   - camera_platform working directory
#   CAMERA_STACK_LOG_DIR        - directory for launcher/service logs
#   CAMERA_SOURCE               - optional camera_platform input override
# =====================================================================
$ErrorActionPreference = "Continue"

$sysPy        = if ($env:CAMERA_STACK_SYSTEM_PYTHON) { $env:CAMERA_STACK_SYSTEM_PYTHON } else { "<path-to-system-python>\python.exe" }
$venvPy       = if ($env:CAMERA_STACK_VENV_PYTHON) { $env:CAMERA_STACK_VENV_PYTHON } else { "<path-to-camera_platform>\.venv\Scripts\python.exe" }
$camStreamDir = if ($env:CAMERA_STACK_STREAM_DIR) { $env:CAMERA_STACK_STREAM_DIR } else { "<path-to-camera-stream>" }
$platformDir  = if ($env:CAMERA_STACK_PLATFORM_DIR) { $env:CAMERA_STACK_PLATFORM_DIR } else { "<path-to-camera_platform>" }
$logDir       = if ($env:CAMERA_STACK_LOG_DIR) { $env:CAMERA_STACK_LOG_DIR } else { "<path-to-camera_platform>\autostart_logs" }

foreach ($pair in @(
    @{ Name = "CAMERA_STACK_SYSTEM_PYTHON"; Value = $sysPy },
    @{ Name = "CAMERA_STACK_VENV_PYTHON"; Value = $venvPy },
    @{ Name = "CAMERA_STACK_STREAM_DIR"; Value = $camStreamDir },
    @{ Name = "CAMERA_STACK_PLATFORM_DIR"; Value = $platformDir },
    @{ Name = "CAMERA_STACK_LOG_DIR"; Value = $logDir }
)) {
    if ($pair.Value -match '^<.*>') {
        Write-Error "$($pair.Name) is not set. Set it (or edit the placeholder default in this script) to match your machine's layout."
        exit 1
    }
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
function Log($m) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $m" | Tee-Object -Append -FilePath "$logDir\launcher.log" }

function Test-CameraSourceInDotEnv($path) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        return $false
    }

    foreach ($line in [System.IO.File]::ReadLines($path)) {
        if ($line -match '^\s*(?:export\s+)?CAMERA_SOURCE\s*=') {
            return $true
        }
    }
    return $false
}

Log "=== launcher start ==="

# --- 0. Kill any existing stack instances (clean restart) ---
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'app\.py' -or $_.CommandLine -match 'main\.py --api' } |
    ForEach-Object { Log "killing stale PID $($_.ProcessId)"; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2
# Belt-and-suspenders: ensure NOTHING is left holding :5000 / :5001 / :8765
foreach ($p in (Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match 'app\.py' -or $_.CommandLine -match 'main\.py --api' })) {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2

# --- 1. Start camera-stream (Orbbec feed) ---
# Camera is mounted upside-down — rotate every frame 180 deg at the source so the
# raw stream, perception, annotated feed and detection are all right-side-up.
# Set to 0 if the camera is ever physically turned back upright.
$env:CAMERA_ROTATE = "180"
Log "starting camera-stream (CAMERA_ROTATE=$($env:CAMERA_ROTATE))"
Start-Process -FilePath $sysPy -ArgumentList "app.py" -WorkingDirectory $camStreamDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput "$logDir\camera-stream.out.log" `
    -RedirectStandardError  "$logDir\camera-stream.err.log"

# --- 2. Wait for the Orbbec camera to connect (network cam; allow ~60s) ---
$connected = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    try {
        $s = (Invoke-WebRequest "http://127.0.0.1:5000/status" -UseBasicParsing -TimeoutSec 3).Content | ConvertFrom-Json
        if ($s.connected) { $connected = $true; Log "camera-stream connected: $($s.url)"; break }
    } catch {}
}
if (-not $connected) { Log "WARN: camera-stream not confirmed connected after 60s; starting platform anyway" }

# --- 3. Start camera_platform ---
# This launcher starts the loopback MJPEG stream above, so use it unless the
# caller or the platform .env explicitly selected another camera_platform source.
$cameraSourceInherited = $null -ne [Environment]::GetEnvironmentVariable("CAMERA_SOURCE", "Process")
$cameraSourceInDotEnv = Test-CameraSourceInDotEnv (Join-Path $platformDir ".env")
if (-not $cameraSourceInherited -and -not $cameraSourceInDotEnv) {
    $env:CAMERA_SOURCE = "http://127.0.0.1:5000/stream"
}
Log "starting camera_platform"
Start-Process -FilePath $venvPy -ArgumentList "main.py --api --host 0.0.0.0 --port 5001" -WorkingDirectory $platformDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput "$logDir\platform.out.log" `
    -RedirectStandardError  "$logDir\platform.err.log"

# --- 4. Wait for the API, then start monitoring ---
$apiUp = $false
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Seconds 2
    try {
        if ((Invoke-WebRequest "http://127.0.0.1:5001/api/health" -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200) { $apiUp = $true; break }
    } catch {}
}
if ($apiUp) {
    Start-Sleep -Seconds 3
    try {
        $r = (Invoke-WebRequest "http://127.0.0.1:5001/api/control/start" -Method POST -UseBasicParsing -TimeoutSec 10).Content
        Log "monitoring started: $r"
    } catch { Log "WARN: failed to POST control/start: $_" }
} else {
    Log "ERROR: platform API never came up on :5001"
}
Log "=== launcher done ==="
