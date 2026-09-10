' =====================================================================
' Auto-start launcher for the Camera Platform stack.
'
' INSTALL: copy this file into the current user's Startup folder:
'   %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\
'   (open with Win+R -> shell:startup)
' It runs at every logon, hidden (no console window), and launches
' scripts/start_camera_stack.ps1 which brings up camera-stream +
' camera_platform and begins monitoring. No admin required.
'
' Before installing, replace <path-to-camera_platform> below with the
' absolute path to this repo's camera_platform/camera_platform directory
' on your machine.
' =====================================================================
CreateObject("WScript.Shell").Run "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ""<path-to-camera_platform>\scripts\start_camera_stack.ps1""", 0, False
