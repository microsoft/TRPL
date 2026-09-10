# Linux App Service requires a Linux antenv for ZERO_TRUST builds (Oryx disabled, route-all blocks PyPI).
# When ZERO_TRUST is not enabled, Oryx runs on App Service and prepackage is not required.
$ErrorActionPreference = 'Stop'

function Get-ZeroTrust {
    if ($env:ZERO_TRUST) { return $env:ZERO_TRUST.Trim().ToLower() }
    try {
        $val = (azd env get-values 2>$null | Select-String '^ZERO_TRUST="(.*)"').Matches[0].Groups[1].Value
        return $val.Trim().ToLower()
    } catch { return "" }
}

$zt = Get-ZeroTrust
if ($zt -ne "true" -and $zt -ne "1" -and $zt -ne "yes") {
    Write-Host "ZERO_TRUST not enabled — skipping antenv prepackage (App Service Oryx build will run)."
    exit 0
}

Write-Error @"
archivist-api must prepackage on Linux (antenv with Linux wheels) when ZERO_TRUST is enabled.
Run deploy from GitHub Actions (azure-private-runner) or: bash ./hooks/prepackage.sh
"@
exit 1
