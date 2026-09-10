#Requires -Version 5.1
<#
.SYNOPSIS
    Obtains a Bearer token for Archivist API read-only post provision tests via Azure CLI.

.DESCRIPTION
    Uses `az account get-access-token` against the API app registration resource URI.
    Sets $env:ARCHIVIST_API_TOKEN and prints the token to stdout.

.PARAMETER Resource
    Entra resource URI (default: $env:ENTRA_API_RESOURCE or api://$env:ENTRA_CLIENT_ID).

.PARAMETER ClientId
    Entra application (client) ID when ENTRA_API_RESOURCE is not set.

.EXAMPLE
    $env:ENTRA_CLIENT_ID = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
    .\get-api-token.ps1
#>
[CmdletBinding()]
param(
    [string]$Resource = $env:ENTRA_API_RESOURCE,
    [string]$ClientId = $env:ENTRA_CLIENT_ID
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($Resource)) {
    if ([string]::IsNullOrWhiteSpace($ClientId)) {
        Write-Error @"
ENTRA_API_RESOURCE or ENTRA_CLIENT_ID must be set.

Resolve from azd:
  azd env get-values | Select-String 'ENTRA_CLIENT_ID'

Then:
  `$env:ENTRA_CLIENT_ID = '<client-id>'
  .\get-api-token.ps1
"@
        exit 1
    }
    $Resource = "api://$ClientId"
}

Write-Host "Requesting token for resource: $Resource" -ForegroundColor Cyan

try {
    az account show -o none 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Azure CLI is not logged in. Run 'az login' first."
        exit 1
    }
}
catch {
    Write-Error "Azure CLI (az) is not available. Install it and run 'az login'."
    exit 1
}

$token = az account get-access-token --resource $Resource --query accessToken -o tsv 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to obtain access token: $token"
    exit 1
}

if ([string]::IsNullOrWhiteSpace($token)) {
    Write-Error "Access token was empty."
    exit 1
}

$env:ARCHIVIST_API_TOKEN = $token
Write-Host "Token acquired (length $($token.Length)). ARCHIVIST_API_TOKEN is set." -ForegroundColor Green
Write-Output $token
