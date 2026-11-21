<#
.SYNOPSIS
Publishes the mobile app with EAS Update, so Expo Go on any phone, anywhere, opens it without this PC running.

.DESCRIPTION
The published app talks to the backend at -ApiUrl (the Render API). The address is remembered in
mobile/.env.render, so after the first time just run publish-app.cmd. Run it again after every change to the app.
Your laptop setup is untouched: mobile/.env.local still points the dev server at this PC.

Needs a one-time setup first (see README.md, Deploying to Render): an Expo account, then in mobile/:
npx eas-cli login, npx eas-cli init and npx eas-cli update:configure.

.PARAMETER ApiUrl
The backend's public address, for example https://paymentsapp-api.onrender.com.

.PARAMETER Message
A note shown with this update in the Expo dashboard.

.EXAMPLE
.\publish-app.cmd -ApiUrl https://paymentsapp-api.onrender.com
.\publish-app.cmd -Message "wallet history labels"
#>
param(
    [string]$ApiUrl,
    [string]$Message = ""
)

$ErrorActionPreference = "Stop"
$mobile = Join-Path $PSScriptRoot "mobile"
$saved = Join-Path $mobile ".env.render"

function Fail([string]$text) {
    Write-Host $text -ForegroundColor Red
    exit 1
}

if (-not $ApiUrl -and (Test-Path $saved)) {
    $line = Get-Content $saved | Where-Object { $_ -match "^EXPO_PUBLIC_API_URL=" } | Select-Object -First 1
    if ($line) { $ApiUrl = $line.Substring("EXPO_PUBLIC_API_URL=".Length) }
}
if (-not $ApiUrl) { Fail "Give the backend's address once: publish-app.cmd -ApiUrl https://paymentsapp-api.onrender.com" }
$ApiUrl = $ApiUrl.TrimEnd("/")
if ($ApiUrl -notmatch "^https://") { Fail "The backend address must start with https://" }
Set-Content -Path $saved -Value @(
    "# Backend address built into the app published with EAS Update (publish-app.ps1). Not a secret."
    "EXPO_PUBLIC_API_URL=$ApiUrl"
) -Encoding ascii

$config = Get-Content (Join-Path $mobile "app.json") -Raw | ConvertFrom-Json
if (-not $config.expo.updates.url -or -not $config.expo.extra.eas.projectId) {
    Fail ("EAS isn't set up for this app yet. In mobile/, run once: npx eas-cli login, npx eas-cli init, " +
        "npx eas-cli update:configure. Then run this again.")
}

# Expo never overwrites a variable already set, so this beats mobile/.env.local for the published app.
$env:EXPO_PUBLIC_API_URL = $ApiUrl
if (-not $Message) { $Message = "demo " + (Get-Date -Format "yyyy-MM-dd HH:mm") }

Write-Host "Checking the backend is up: $ApiUrl/health (a sleeping free service can take a minute)..."
try {
    Invoke-RestMethod -Uri "$ApiUrl/health" -TimeoutSec 90 | Out-Null
}
catch {
    Write-Host "The backend didn't answer. Publishing anyway; check Render if the app can't connect." -ForegroundColor Yellow
}

Push-Location $mobile
try {
    npx eas-cli update --branch demo --message $Message --non-interactive
    if ($LASTEXITCODE -ne 0) { Fail "Publishing failed; see the messages above." }
}
finally {
    Pop-Location
}
Write-Host ""
Write-Host "Published. On the phone, open Expo Go, sign in with the same Expo account, and open paymentsapp" -ForegroundColor Green
Write-Host "(or scan the QR code on the update's page in the Expo dashboard, linked above)."
