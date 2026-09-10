#Requires -Version 5.1
<#
.SYNOPSIS
    Runs all read-only production post provision tests for archivist-app.

.DESCRIPTION
    1. Obtains API token via Azure CLI (optional if ARCHIVIST_API_TOKEN already set)
    2. API GET post provision tests
    3. AGW SPA route post provision tests

    No write or delete operations are performed.

.EXAMPLE
    $env:ARCHIVIST_APP_URL = "<APP_GATEWAY_PUBLIC_URL>"
    $env:ARCHIVIST_API_URL  = "https://<archivist-api-host>"
    $env:ENTRA_CLIENT_ID    = "<from azd>"
    .\run-all-post-provision-test.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipToken,
    [switch]$SkipApi,
    [switch]$SkipRoutes
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "=== Archivist App Read-Only Post Provision Test Suite ===" -ForegroundColor Cyan

& "$here\resolve-test-env.ps1"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "App URL: $(if ($env:ARCHIVIST_APP_URL) { $env:ARCHIVIST_APP_URL } elseif ($env:APP_GATEWAY_PUBLIC_URL) { $env:APP_GATEWAY_PUBLIC_URL } else { '(not set)' })"
Write-Host "API URL: $(if ($env:ARCHIVIST_API_URL) { $env:ARCHIVIST_API_URL } else { '(not set)' })`n"

$exitCode = 0

if (-not $SkipToken -and [string]::IsNullOrWhiteSpace($env:ARCHIVIST_API_TOKEN)) {
    Write-Host "--- Acquiring API token ---" -ForegroundColor Yellow
    & "$here\get-api-token.ps1"
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Token acquisition failed; API post provision test may return 401 for protected endpoints."
    }
}
elseif ($env:ARCHIVIST_API_TOKEN) {
    Write-Host "Using existing ARCHIVIST_API_TOKEN." -ForegroundColor DarkGray
}

if (-not $SkipApi) {
    Write-Host "`n--- API read-only post provision test ---" -ForegroundColor Yellow
    & "$here\post-provision-test-api-readonly.ps1"
    if ($LASTEXITCODE -ne 0) { $exitCode = 1 }
}

if (-not $SkipRoutes) {
    Write-Host "`n--- AGW route post provision test ---" -ForegroundColor Yellow
    & "$here\post-provision-test-routes.ps1"
    if ($LASTEXITCODE -ne 0) { $exitCode = 1 }
}

if ($exitCode -eq 0) {
    Write-Host "`nAll automated post provision test checks passed." -ForegroundColor Green
    Write-Host "Next: run the manual browser matrix in testing/README.md (persona-based)." -ForegroundColor DarkGray
}
else {
    Write-Host "`nOne or more post provision test checks failed." -ForegroundColor Red
}

exit $exitCode
