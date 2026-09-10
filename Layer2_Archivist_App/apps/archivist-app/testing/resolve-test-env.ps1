#Requires -Version 5.1
<#
.SYNOPSIS
    Resolves ARCHIVIST_APP_URL, ARCHIVIST_API_URL, and ENTRA_CLIENT_ID for post provision tests.

.DESCRIPTION
    Resolution order (first non-empty wins):
    - Explicit process env vars (ARCHIVIST_* / APP_GATEWAY_PUBLIC_URL)
    - azd env get-values (APP_GATEWAY_PUBLIC_URL, ENTRA_CLIENT_ID, AZURE_RESOURCE_GROUP, naming codes)
    - Azure Resource Manager: list *-api web app in AZURE_RESOURCE_GROUP

    Sets $env:ARCHIVIST_APP_URL, $env:ARCHIVIST_API_URL, and optionally ENTRA_CLIENT_ID.

.EXAMPLE
    .\resolve-test-env.ps1
    .\run-all-post-provision-test.ps1
#>
[CmdletBinding()]
param(
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'

function Write-Resolve([string]$Message, [string]$Color = 'DarkGray') {
    if (-not $Quiet) {
        Write-Host $Message -ForegroundColor $Color
    }
}

function Get-AzdEnvValue([string]$Key) {
    if (-not (Get-Command azd -ErrorAction SilentlyContinue)) {
        return ''
    }
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $raw = & azd env get-values 2>&1
    $ErrorActionPreference = $prevEap
    $line = @($raw) | Where-Object { $_ -is [string] -and $_ -match "^${Key}=" } | Select-Object -First 1
    if (-not $line) { return '' }
    return ($line -replace "^${Key}=", '').Trim().Trim('"')
}

function Resolve-ApiUrlFromAzure([string]$ResourceGroup) {
    if ([string]::IsNullOrWhiteSpace($ResourceGroup)) { return '' }
    if (-not (Get-Command az -ErrorAction SilentlyContinue)) { return '' }

    try {
        az account show -o none 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) { return '' }
    }
    catch {
        return ''
    }

    $apps = az webapp list -g $ResourceGroup --query "[?ends_with(name, '-api')].defaultHostName" -o tsv 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($apps)) {
        return ''
    }

    $hostName = (@($apps) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -First 1)
    if ([string]::IsNullOrWhiteSpace($hostName)) { return '' }
    return "https://$hostName"
}

function Resolve-ApiUrlFromNaming {
    param(
        [string]$Org,
        [string]$Loc,
        [string]$Env,
        [string]$Project,
        [string]$Index
    )

    if (@($Org, $Loc, $Env, $Project, $Index) | Where-Object { [string]::IsNullOrWhiteSpace($_) }) {
        return ''
    }

    $indexPart = $Index
    if ($Index -match '^\d+$' -and $Index.Length -eq 1) {
        $indexPart = $Index.PadLeft(2, '0')
    }

    return "https://$Org-$Loc-$Env-as-$Project-$indexPart-api.azurewebsites.net"
}

# --- App URL ---
if ([string]::IsNullOrWhiteSpace($env:ARCHIVIST_APP_URL)) {
    if (-not [string]::IsNullOrWhiteSpace($env:APP_GATEWAY_PUBLIC_URL)) {
        $env:ARCHIVIST_APP_URL = $env:APP_GATEWAY_PUBLIC_URL.TrimEnd('/')
        Write-Resolve "ARCHIVIST_APP_URL <= APP_GATEWAY_PUBLIC_URL"
    }
    else {
        $agw = Get-AzdEnvValue 'APP_GATEWAY_PUBLIC_URL'
        if (-not [string]::IsNullOrWhiteSpace($agw)) {
            $env:ARCHIVIST_APP_URL = $agw.TrimEnd('/')
            $env:APP_GATEWAY_PUBLIC_URL = $env:ARCHIVIST_APP_URL
            Write-Resolve "ARCHIVIST_APP_URL <= azd APP_GATEWAY_PUBLIC_URL"
        }
    }
}

# --- API URL ---
# Zero-trust: browser/API traffic uses the same AGW host with path /api/v1 (not public *.azurewebsites.net).
$zeroTrustRaw = (Get-AzdEnvValue 'ZERO_TRUST').ToLower().Trim()
$zeroTrust = $zeroTrustRaw -in @('true', '1', 'yes')

$apiUrlManual = $env:ARCHIVIST_API_URL_MANUAL -eq 'true'

if ($zeroTrust -and -not $apiUrlManual -and -not [string]::IsNullOrWhiteSpace($env:ARCHIVIST_APP_URL)) {
    $env:ARCHIVIST_API_URL = $env:ARCHIVIST_APP_URL.TrimEnd('/')
    Write-Resolve "ARCHIVIST_API_URL <= AGW (ZERO_TRUST; API at /api/v1 on app host)"
}
elseif ([string]::IsNullOrWhiteSpace($env:ARCHIVIST_API_URL)) {
    $rg = if ($env:AZURE_RESOURCE_GROUP) { $env:AZURE_RESOURCE_GROUP } else { Get-AzdEnvValue 'AZURE_RESOURCE_GROUP' }
    if (-not [string]::IsNullOrWhiteSpace($rg)) {
        $env:AZURE_RESOURCE_GROUP = $rg
    }

    $fromAzure = Resolve-ApiUrlFromAzure -ResourceGroup $rg
    if (-not [string]::IsNullOrWhiteSpace($fromAzure)) {
        $env:ARCHIVIST_API_URL = $fromAzure
        Write-Resolve "ARCHIVIST_API_URL <= Azure webapp list ($rg)"
    }
    else {
        $org = Get-AzdEnvValue 'ORGANIZATION_CODE'
        $loc = Get-AzdEnvValue 'LOCATION_CODE'
        $envCode = Get-AzdEnvValue 'ENVIRONMENT_CODE'
        $project = Get-AzdEnvValue 'PROJECT_CODE'
        $index = Get-AzdEnvValue 'INDEX_NUMBER'
        $fromNaming = Resolve-ApiUrlFromNaming -Org $org -Loc $loc -Env $envCode -Project $project -Index $index
        if (-not [string]::IsNullOrWhiteSpace($fromNaming)) {
            $env:ARCHIVIST_API_URL = $fromNaming
            Write-Resolve "ARCHIVIST_API_URL <= naming convention ($fromNaming)"
        }
    }
}

# --- Entra client ID (optional for token) ---
if ([string]::IsNullOrWhiteSpace($env:ENTRA_CLIENT_ID)) {
    $clientId = Get-AzdEnvValue 'ENTRA_CLIENT_ID'
    if (-not [string]::IsNullOrWhiteSpace($clientId)) {
        $env:ENTRA_CLIENT_ID = $clientId
        Write-Resolve "ENTRA_CLIENT_ID <= azd"
    }
}

if (-not $Quiet) {
    Write-Host "`nResolved test environment:" -ForegroundColor Cyan
    Write-Host "  ARCHIVIST_APP_URL = $(if ($env:ARCHIVIST_APP_URL) { $env:ARCHIVIST_APP_URL } else { '(missing)' })"
    Write-Host "  ARCHIVIST_API_URL  = $(if ($env:ARCHIVIST_API_URL) { $env:ARCHIVIST_API_URL } else { '(missing)' })"
    Write-Host "  ENTRA_CLIENT_ID    = $(if ($env:ENTRA_CLIENT_ID) { $env:ENTRA_CLIENT_ID } else { '(missing — token step may warn)' })"
}

if ([string]::IsNullOrWhiteSpace($env:ARCHIVIST_APP_URL) -and [string]::IsNullOrWhiteSpace($env:ARCHIVIST_API_URL)) {
    Write-Error "Could not resolve ARCHIVIST_APP_URL or ARCHIVIST_API_URL. Run 'azd env select <env>' or set env vars manually."
    exit 1
}

exit 0
