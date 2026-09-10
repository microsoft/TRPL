# Linux App Service requires a Linux antenv. Use CI / WSL / Git Bash with prepackage.sh.
$ErrorActionPreference = 'Stop'
Write-Error @"
content-export-api must prepackage on Linux (antenv with Linux wheels).
Run deploy from GitHub Actions (azure-private-runner) or: bash ./hooks/prepackage.sh
"@
exit 1
