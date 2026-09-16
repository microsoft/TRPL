# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

#Requires -Version 5.1
<#
.SYNOPSIS
    Read-only GET post provision tests against the deployed Archivist API.

.DESCRIPTION
    Exercises GET endpoints used by archivist-app. Never sends POST/PATCH/PUT/DELETE.
    Role-dependent endpoints may return 401/403 without failing the run.

.PARAMETER ApiUrl
    API base URL without trailing slash (default: $env:ARCHIVIST_API_URL).

.PARAMETER Token
    Bearer token (default: $env:ARCHIVIST_API_TOKEN).

.EXAMPLE
    $env:ARCHIVIST_API_URL = "https://<archivist-api-host>"
    .\get-api-token.ps1
    .\post-provision-test-api-readonly.ps1
#>
[CmdletBinding()]
param(
    [string]$ApiUrl = $env:ARCHIVIST_API_URL,
    [string]$Token = $env:ARCHIVIST_API_TOKEN
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($ApiUrl)) {
    Write-Error "ARCHIVIST_API_URL must be set (e.g. https://<archivist-api-host>)."
    exit 1
}

$ApiUrl = $ApiUrl.TrimEnd('/')
$BasePath = "$ApiUrl/api/v1"

$results = [System.Collections.Generic.List[object]]::new()
$hasServerError = $false
$roleHint = 'unknown'

function Assert-GetOnly {
    param([string]$Method)
    if ($Method -ne 'GET') {
        throw "Read-only guard: only GET is allowed (got $Method)."
    }
}

function Invoke-ReadOnlyGet {
    param(
        [string]$Name,
        [string]$Path,
        [bool]$Required = $true,
        [bool]$RoleDependent = $false
    )

    Assert-GetOnly -Method 'GET'

    $url = if ($Path.StartsWith('http')) { $Path } else { "$BasePath$Path" }
    $headers = @{ Accept = 'application/json' }
    if (-not [string]::IsNullOrWhiteSpace($Token)) {
        $headers['Authorization'] = "Bearer $Token"
    }

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $status = 0
    $note = ''

    try {
        $response = Invoke-WebRequest -Uri $url -Method GET -Headers $headers -UseBasicParsing -TimeoutSec 60
        $status = [int]$response.StatusCode
    }
    catch {
        if ($_.Exception.Response) {
            $status = [int]$_.Exception.Response.StatusCode.value__
            $note = $_.Exception.Message
        }
        else {
            $status = 0
            $note = $_.Exception.Message
            if ($Required) { $script:hasServerError = $true }
        }
    }
    finally {
        $sw.Stop()
    }

    $pass = $false
    if ($status -ge 200 -and $status -lt 300) {
        $pass = $true
    }
    elseif ($status -eq 403 -and $note -match 'Ip Forbidden|Forbidden') {
        # Zero-trust / AGW IP allowlist — endpoint exists but this runner is off-network.
        $pass = $true
        $note = 'network restricted (ZERO_TRUST / IP allowlist)'
    }
    elseif ($RoleDependent -and ($status -eq 401 -or $status -eq 403)) {
        $pass = $true
        $note = if ($note) { "$note (expected for role)" } else { 'expected for role' }
    }
    elseif ($status -ge 500) {
        $script:hasServerError = $true
    }
    elseif (-not $Required) {
        $pass = $true
    }

    $results.Add([pscustomobject]@{
        Name     = $Name
        Path     = $Path
        Status   = $status
        Ms       = $sw.ElapsedMilliseconds
        Pass     = $pass
        Note     = $note
    }) | Out-Null

    return $response
}

Write-Host "`nArchivist API read-only post provision test — $BasePath" -ForegroundColor Cyan
Write-Host "Token: $(if ($Token) { 'present' } else { 'MISSING (some endpoints may return 401)' })`n"

# Core
$authResponse = Invoke-ReadOnlyGet -Name 'health' -Path '/health'
$meResponse = Invoke-ReadOnlyGet -Name 'auth/me' -Path '/auth/me' -Required $false

if ($meResponse -and $meResponse.Content) {
    try {
        $me = $meResponse.Content | ConvertFrom-Json
        if ($me.user) {
            $roles = @()
            if ($me.user.isAdmin) { $roles += 'Admin' }
            if ($me.user.isDataFoundations) { $roles += 'DataFoundations' }
            if ($me.user.isArchivist) { $roles += 'Archivist' }
            $roleHint = if ($roles.Count) { ($roles -join ', ') } else { 'no roles' }
        }
    }
    catch { }
}

Write-Host "Authenticated roles: $roleHint`n" -ForegroundColor DarkGray

# Browse
$browseEndpoints = @(
    @{ Name = 'dashboard/statistics'; Path = '/dashboard/statistics' },
    @{ Name = 'repositories'; Path = '/repositories?page_number=1&page_size=5' },
    @{ Name = 'repositories/statistics'; Path = '/repositories/statistics' },
    @{ Name = 'collections/summaries'; Path = '/collections/summaries' },
    @{ Name = 'filters/repositories'; Path = '/filters/repositories' },
    @{ Name = 'filters/collections'; Path = '/filters/collections' },
    @{ Name = 'filters/resource-types'; Path = '/filters/resource-types' },
    @{ Name = 'filters/sources'; Path = '/filters/sources' },
    @{ Name = 'filters/creators'; Path = '/filters/creators' },
    @{ Name = 'filters/recipients'; Path = '/filters/recipients' },
    @{ Name = 'documents'; Path = '/documents?limit=1' }
)

foreach ($ep in $browseEndpoints) {
    Invoke-ReadOnlyGet -Name $ep.Name -Path $ep.Path | Out-Null
}

# Discovery chain from live data
$repoName = $null
$collName = $null
$docId = $null

try {
    $headers = @{ Accept = 'application/json' }
    if ($Token) { $headers['Authorization'] = "Bearer $Token" }
    $repoResp = Invoke-RestMethod -Uri "$BasePath/repositories?page_number=1&page_size=1" -Headers $headers -Method GET
    if ($repoResp.repositories -and $repoResp.repositories.Count -gt 0) {
        $repoName = $repoResp.repositories[0].repository
    }
}
catch { }

if ($repoName) {
    $encodedRepo = [uri]::EscapeDataString($repoName)
    Invoke-ReadOnlyGet -Name 'repo/collections' -Path "/repositories/$encodedRepo/collections?page_number=1&page_size=5" -Required $false | Out-Null
    Invoke-ReadOnlyGet -Name 'collections/details (repo)' -Path "/collections/details?repository=$encodedRepo" -Required $false | Out-Null

    try {
        $headers = @{ Accept = 'application/json' }
        if ($Token) { $headers['Authorization'] = "Bearer $Token" }
        $collResp = Invoke-RestMethod -Uri "$BasePath/repositories/$encodedRepo/collections?page_number=1&page_size=1" -Headers $headers -Method GET
        if ($collResp.collections -and $collResp.collections.Count -gt 0) {
            $collName = $collResp.collections[0].collection
        }
    }
    catch { }

    if ($collName) {
        $encodedColl = [uri]::EscapeDataString($collName)
        Invoke-ReadOnlyGet -Name 'collections/details' -Path "/collections/details?repository=$encodedRepo&collection=$encodedColl" -Required $false | Out-Null
        Invoke-ReadOnlyGet -Name 'ocr-report' -Path "/repositories/$encodedRepo/collections/$encodedColl/ocr-report" -Required $false -RoleDependent $true | Out-Null
    }
}

try {
    $headers = @{ Accept = 'application/json' }
    if ($Token) { $headers['Authorization'] = "Bearer $Token" }
    $docResp = Invoke-RestMethod -Uri "$BasePath/documents?limit=1" -Headers $headers -Method GET
    if ($docResp.documents -and $docResp.documents.Count -gt 0) {
        $docId = $docResp.documents[0].id
    }
}
catch { }

if ($docId) {
    Invoke-ReadOnlyGet -Name 'document by id' -Path "/documents/$docId" -Required $false | Out-Null
}

# Tools (role-dependent — 401/403 acceptable)
$toolEndpoints = @(
    @{ Name = 'pipeline/stages'; Path = '/pipeline/stages' },
    @{ Name = 'pipeline/statistics'; Path = '/pipeline/statistics' },
    @{ Name = 'pipeline/jobs'; Path = '/pipeline/jobs' },
    @{ Name = 'pipeline/periodic-runs'; Path = '/pipeline/periodic-runs' },
    @{ Name = 'pipeline/periodic-schedules'; Path = '/pipeline/periodic-schedules' },
    @{ Name = 'ingestion/failed-count'; Path = '/ingestion/failed-count' },
    @{ Name = 'ingestion/publishers'; Path = '/ingestion/publishers' },
    @{ Name = 'ingestion/publish-batches'; Path = '/ingestion/publish-batches' },
    @{ Name = 'epub/documents'; Path = '/epub/documents?page=1&page_size=5' },
    @{ Name = 'correction-requests'; Path = '/correction-requests?limit=5' },
    @{ Name = 'field-mappings'; Path = '/field-mappings' },
    @{ Name = 'metadata-fields'; Path = '/metadata-fields' },
    @{ Name = 'statistics/status'; Path = '/statistics/status' }
)

foreach ($ep in $toolEndpoints) {
    Invoke-ReadOnlyGet -Name $ep.Name -Path $ep.Path -Required $false -RoleDependent $true | Out-Null
}

# Report
$results | Format-Table Name, Status, Ms, Pass, Note -AutoSize

$failed = @($results | Where-Object { -not $_.Pass })
Write-Host "`nSummary: $($results.Count) checks, $($failed.Count) failed, roles: $roleHint" -ForegroundColor $(if ($failed.Count -eq 0 -and -not $hasServerError) { 'Green' } else { 'Yellow' })

if ($failed.Count -gt 0) {
    Write-Host "Failed:" -ForegroundColor Red
    $failed | ForEach-Object { Write-Host "  $($_.Name) — HTTP $($_.Status) $($_.Note)" -ForegroundColor Red }
}

if ($hasServerError) {
    exit 1
}

if ($failed.Count -gt 0) {
    exit 1
}

exit 0
