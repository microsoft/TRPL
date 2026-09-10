#Requires -Version 5.1
<#
.SYNOPSIS
    Read-only route reachability checks against the Archivist SPA (Application Gateway URL).

.DESCRIPTION
    Issues GET requests without following redirects. Treats HTTP 200 (index.html)
    or 302/401 (Entra login redirect) as reachable. Does not authenticate.

.PARAMETER AppUrl
    Application Gateway public URL (default: $env:ARCHIVIST_APP_URL or $env:APP_GATEWAY_PUBLIC_URL).

.EXAMPLE
    $env:ARCHIVIST_APP_URL = "http://<agw-public-ip>"
    .\post-provision-test-routes.ps1
#>
[CmdletBinding()]
param(
    [string]$AppUrl = $(if ($env:ARCHIVIST_APP_URL) { $env:ARCHIVIST_APP_URL } else { $env:APP_GATEWAY_PUBLIC_URL })
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($AppUrl)) {
    Write-Error @"
ARCHIVIST_APP_URL or APP_GATEWAY_PUBLIC_URL must be set.

Resolve from azd:
  azd env get-values | Select-String 'APP_GATEWAY_PUBLIC_URL'
"@
    exit 1
}

$AppUrl = $AppUrl.TrimEnd('/')

$routes = @(
    '/',
    '/repositories',
    '/collections',
    '/home',
    '/help/architecture',
    '/admin/statistics',
    '/correction-requests',
    '/tools/epub-processor',
    '/tools/data-pipeline',
    '/tools/data-ingestion',
    '/admin/field-mappings'
)

$results = [System.Collections.Generic.List[object]]::new()
$hasHardFailure = $false

Write-Host "`nArchivist SPA route post provision test — $AppUrl" -ForegroundColor Cyan
Write-Host "Read-only GET; redirects to Entra login are acceptable.`n"

foreach ($route in $routes) {
    $url = "$AppUrl$route"
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $status = 0
    $note = ''
    $pass = $false

    try {
        $response = Invoke-WebRequest -Uri $url -Method GET -MaximumRedirection 0 -UseBasicParsing -TimeoutSec 30
        $status = [int]$response.StatusCode
        if ($status -eq 200) {
            $pass = $true
            $note = 'SPA served'
        }
    }
    catch {
        if ($_.Exception.Response) {
            $status = [int]$_.Exception.Response.StatusCode.value__
            if ($status -in 301, 302, 303, 307, 308, 401, 403) {
                $pass = $true
                if ($status -eq 403) {
                    $note = 'forbidden (AGW/WAF or IP allowlist — host is reachable)'
                }
                else {
                    $note = 'auth redirect (expected without session)'
                }
            }
            else {
                $note = $_.Exception.Message
                if ($status -ge 500 -or $status -eq 404) {
                    $script:hasHardFailure = $true
                }
            }
        }
        else {
            $note = $_.Exception.Message
            $script:hasHardFailure = $true
        }
    }
    finally {
        $sw.Stop()
    }

    $results.Add([pscustomobject]@{
        Route  = $route
        Status = $status
        Ms     = $sw.ElapsedMilliseconds
        Pass   = $pass
        Note   = $note
    }) | Out-Null
}

$results | Format-Table Route, Status, Ms, Pass, Note -AutoSize

$failed = @($results | Where-Object { -not $_.Pass })
Write-Host "`nSummary: $($results.Count) routes, $($failed.Count) hard failures" -ForegroundColor $(if ($failed.Count -eq 0) { 'Green' } else { 'Red' })

if ($failed.Count -gt 0) {
    $failed | ForEach-Object { Write-Host "  $($_.Route) — HTTP $($_.Status) $($_.Note)" -ForegroundColor Red }
    exit 1
}

exit 0
