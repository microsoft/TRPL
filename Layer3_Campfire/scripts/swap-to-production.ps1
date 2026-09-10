<#
.SYNOPSIS
    Swap dev slot to production for Reading Room services.
    
.DESCRIPTION
    Swaps the dev deployment slot to production for frontend, backend, or both.
    Run this from your local machine (requires az login).
    
    This is the manual alternative to the GitHub Actions "Swap to Production" workflow,
    which requires AZURE_CREDENTIALS (service principal) to be configured.

.PARAMETER Target
    Which service to swap: frontend, backend, or both

.PARAMETER ResourceGroup
    Azure resource group containing the app services (default: $env:READINGROOM_RESOURCE_GROUP).

.PARAMETER FrontendApp
    Frontend App Service name (default: $env:READINGROOM_FRONTEND_APP).

.PARAMETER BackendApp
    Backend App Service name (default: $env:READINGROOM_BACKEND_APP).

.EXAMPLE
    $env:READINGROOM_RESOURCE_GROUP = "<your-resource-group>"
    $env:READINGROOM_FRONTEND_APP = "<your-frontend-app-name>"
    $env:READINGROOM_BACKEND_APP = "<your-backend-app-name>"
    .\scripts\swap-to-production.ps1 -Target frontend
    .\scripts\swap-to-production.ps1 -Target backend
    .\scripts\swap-to-production.ps1 -Target both
#>

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("frontend", "backend", "both")]
    [string]$Target,

    [string]$ResourceGroup = $env:READINGROOM_RESOURCE_GROUP,
    [string]$FrontendApp = $env:READINGROOM_FRONTEND_APP,
    [string]$BackendApp = $env:READINGROOM_BACKEND_APP
)

if ([string]::IsNullOrWhiteSpace($ResourceGroup)) {
    Write-Error "READINGROOM_RESOURCE_GROUP must be set (or pass -ResourceGroup)."
    exit 1
}
if ($Target -in @("frontend", "both") -and [string]::IsNullOrWhiteSpace($FrontendApp)) {
    Write-Error "READINGROOM_FRONTEND_APP must be set (or pass -FrontendApp) to swap the frontend."
    exit 1
}
if ($Target -in @("backend", "both") -and [string]::IsNullOrWhiteSpace($BackendApp)) {
    Write-Error "READINGROOM_BACKEND_APP must be set (or pass -BackendApp) to swap the backend."
    exit 1
}
$SlotName = "dev"

# Verify Azure login
Write-Host "`n🔐 Checking Azure login..." -ForegroundColor Cyan
$account = az account show --query "{name:name, id:id}" -o json 2>$null | ConvertFrom-Json
if (-not $account) {
    Write-Host "❌ Not logged in. Run 'az login' first." -ForegroundColor Red
    exit 1
}
Write-Host "  ✅ Logged in to: $($account.name)" -ForegroundColor Green

# Pre-swap health checks
Write-Host "`n🔍 Pre-swap health checks..." -ForegroundColor Cyan

$failed = $false

if ($Target -eq "frontend" -or $Target -eq "both") {
    $url = "https://$FrontendApp-$SlotName.azurewebsites.net/api/health"
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 10
        if ($response.StatusCode -eq 200) {
            Write-Host "  ✅ Frontend dev slot: healthy" -ForegroundColor Green
            $body = $response.Content | ConvertFrom-Json
            Write-Host "     Commit: $($body.version.commit)" -ForegroundColor Gray
        }
    } catch {
        Write-Host "  ❌ Frontend dev slot: NOT healthy ($url)" -ForegroundColor Red
        $failed = $true
    }
}

if ($Target -eq "backend" -or $Target -eq "both") {
    $url = "https://$BackendApp-$SlotName.azurewebsites.net/healthz"
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 10
        if ($response.StatusCode -eq 200) {
            Write-Host "  ✅ Backend dev slot: healthy" -ForegroundColor Green
            $body = $response.Content | ConvertFrom-Json
            Write-Host "     Commit: $($body.version.commit)" -ForegroundColor Gray
        }
    } catch {
        Write-Host "  ❌ Backend dev slot: NOT healthy ($url)" -ForegroundColor Red
        $failed = $true
    }
}

if ($failed) {
    Write-Host "`n❌ Aborting: one or more dev slots are not healthy." -ForegroundColor Red
    Write-Host "   Deploy to the dev slot first, then retry." -ForegroundColor Yellow
    exit 1
}

# Confirm
Write-Host "`n⚠️  About to swap $Target dev → production" -ForegroundColor Yellow
$confirm = Read-Host "   Proceed? (y/N)"
if ($confirm -ne "y") {
    Write-Host "   Cancelled." -ForegroundColor Gray
    exit 0
}

# Swap
if ($Target -eq "frontend" -or $Target -eq "both") {
    Write-Host "`n🔄 Swapping $FrontendApp : dev → production..." -ForegroundColor Cyan
    az webapp deployment slot swap --name $FrontendApp --resource-group $ResourceGroup --slot $SlotName --target-slot production
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  ✅ Frontend swap complete" -ForegroundColor Green
    } else {
        Write-Host "  ❌ Frontend swap failed!" -ForegroundColor Red
        exit 1
    }
}

if ($Target -eq "backend" -or $Target -eq "both") {
    Write-Host "`n🔄 Swapping $BackendApp : dev → production..." -ForegroundColor Cyan
    az webapp deployment slot swap --name $BackendApp --resource-group $ResourceGroup --slot $SlotName --target-slot production
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  ✅ Backend swap complete" -ForegroundColor Green
    } else {
        Write-Host "  ❌ Backend swap failed!" -ForegroundColor Red
        exit 1
    }
}

# Post-swap verification
Write-Host "`n⏳ Waiting 15s for swap to settle..." -ForegroundColor Gray
Start-Sleep -Seconds 15

Write-Host "`n🔍 Post-swap verification..." -ForegroundColor Cyan

if ($Target -eq "frontend" -or $Target -eq "both") {
    $url = "https://$FrontendApp.azurewebsites.net/api/health"
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 10
        Write-Host "  ✅ Frontend production: healthy (HTTP $($response.StatusCode))" -ForegroundColor Green
        $body = $response.Content | ConvertFrom-Json
        Write-Host "     Commit: $($body.version.commit)" -ForegroundColor Gray
    } catch {
        Write-Host "  ⚠️  Frontend production: may still be starting ($url)" -ForegroundColor Yellow
    }
}

if ($Target -eq "backend" -or $Target -eq "both") {
    $url = "https://$BackendApp.azurewebsites.net/healthz"
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 10
        Write-Host "  ✅ Backend production: healthy (HTTP $($response.StatusCode))" -ForegroundColor Green
        $body = $response.Content | ConvertFrom-Json
        Write-Host "     Commit: $($body.version.commit)" -ForegroundColor Gray
    } catch {
        Write-Host "  ⚠️  Backend production: may still be starting ($url)" -ForegroundColor Yellow
    }
}

Write-Host "`n============================================" -ForegroundColor Cyan
Write-Host "📊 SWAP COMPLETE" -ForegroundColor Green
Write-Host "   Target: $Target"
Write-Host "   Frontend prod: https://$FrontendApp.azurewebsites.net"
Write-Host "   Backend prod:  https://$BackendApp.azurewebsites.net"
Write-Host ""
Write-Host "💡 To rollback: run this script again (old version is now in dev slot)" -ForegroundColor Yellow
Write-Host "============================================" -ForegroundColor Cyan
