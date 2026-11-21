<#
.SYNOPSIS
Starts everything for local development in one Windows Terminal window, one tab each:
backend API, worker, admin website and the Expo dev server for the mobile app. It also opens the mock bank's
website in the browser first, so the hosted bank (which sleeps when idle) is awake by the time the app is ready.

.DESCRIPTION
Run it from the repo root with dev.cmd (or double-click dev.cmd). Each tab stays interactive, so you can still
press a or r in the Expo tab. Close the window, or Ctrl+C in a tab, to stop.

It also points the mobile app at this PC: for a phone it writes the PC's current Wi-Fi address into
mobile/.env.local (the address can change from day to day), and for the emulator it writes 10.0.2.2.

.PARAMETER Device
phone (default): the app runs in Expo Go on a phone on the same Wi-Fi. The backend listens on the network.
emulator: the app runs in the Android emulator. The backend listens on this PC only.

.PARAMETER Skip
Parts not to start, for example -Skip admin,worker. Names: backend, worker, admin, mobile, bank.

.PARAMETER BankUrl
The mock bank website to open. Defaults to the hosted mock bank.

.PARAMETER DryRun
Show what would start, without starting anything.

.EXAMPLE
.\dev.cmd
.\dev.cmd -Device emulator
.\dev.cmd -Skip mobile
#>
param(
    [ValidateSet("phone", "emulator")]
    [string]$Device = "phone",
    [string[]]$Skip = @(),
    [string]$BankUrl = "https://mockbankforpayments.onrender.com/",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

# Through dev.cmd a list like admin,worker arrives as one string, so split it here.
$Skip = @($Skip | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim().ToLower() } | Where-Object { $_ })
$unknown = $Skip | Where-Object { $_ -notin @("backend", "worker", "admin", "mobile", "bank") }
if ($unknown) {
    Write-Host "Unknown -Skip name: $($unknown -join ', '). Use backend, worker, admin, mobile or bank." -ForegroundColor Red
    exit 1
}

function Fail([string]$message) {
    Write-Host $message -ForegroundColor Red
    exit 1
}

# ---- Checks --------------------------------------------------------------------------------------------------

if (-not (Get-Command wt -ErrorAction SilentlyContinue)) {
    Fail "Windows Terminal (wt) isn't installed. Install it from the Microsoft Store, then run this again."
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Fail "uv isn't on PATH. See README.md, Backend." }
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { Fail "Node.js (npm) isn't on PATH." }
if (-not (Test-Path "$root\backend\.env")) { Fail "backend\.env is missing. Copy backend\.env.example and fill it in." }
foreach ($app in @("admin-web", "mobile")) {
    if (-not (Test-Path "$root\$app\node_modules")) {
        Write-Host "Installing $app dependencies (first run only)..." -ForegroundColor Yellow
        Push-Location "$root\$app"
        try { npm install } finally { Pop-Location }
    }
}

function Test-PortInUse([int]$port) {
    $null -ne (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
}

# ---- Wake the mock bank -------------------------------------------------------------------------------------------

# Opened first: the free-tier bank takes about a minute to wake, and it can do that while everything else starts.
# The page is also where test customers approve consents and collect requests with their UPI PIN.
if (-not ($Skip -contains "bank")) {
    if ($DryRun) { Write-Host "Would open: $BankUrl" }
    else { Start-Process $BankUrl }
}

# ---- Where the mobile app finds the backend --------------------------------------------------------------------

if ($Device -eq "phone") {
    $ip = Get-NetIPConfiguration |
        Where-Object { $_.IPv4DefaultGateway -ne $null -and $_.NetAdapter.Status -eq "Up" } |
        ForEach-Object { $_.IPv4Address.IPAddress } |
        Select-Object -First 1
    if (-not $ip) { Fail "No network connection found. Connect this PC to the same Wi-Fi as the phone." }
    $apiUrl = "http://${ip}:8001"
    $backendHost = "0.0.0.0"  # reachable from the phone; development only
}
else {
    $apiUrl = "http://10.0.2.2:8001"  # the emulator's name for this PC
    $backendHost = "127.0.0.1"
}

$envFile = "$root\mobile\.env.local"
$header = @(
    "# Backend URL as the app sees it. Written by dev.ps1 (-Device $Device); 10.0.2.2 is the emulator's name for this PC."
    "# Not a secret: EXPO_PUBLIC_ values are built into the app."
)
$others = @()
if (Test-Path $envFile) {
    $others = Get-Content $envFile | Where-Object { $_ -notmatch "^\s*EXPO_PUBLIC_API_URL=" -and $_ -notmatch "^\s*#" -and $_.Trim() -ne "" }
}
Set-Content -Path $envFile -Value ($header + "EXPO_PUBLIC_API_URL=$apiUrl" + $others) -Encoding ascii

# ---- Tabs ------------------------------------------------------------------------------------------------------

$tabs = @()
function Add-Tab([string]$name, [string]$title, [string]$dir, [string]$command) {
    if ($Skip -contains $name) { return }
    # Encoded, so quotes and semicolons in the command survive the trip through wt's own argument parsing.
    $body = "`$Host.UI.RawUI.WindowTitle = '$title'; Write-Host '$title' -ForegroundColor Cyan; $command"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($body))
    $script:tabs += "new-tab --title `"$title`" --suppressApplicationTitle -d `"$dir`" powershell -NoExit -EncodedCommand $encoded"
}

if ((Test-PortInUse 8001) -and -not ($Skip -contains "backend")) {
    Write-Host "Port 8001 is already in use, so the backend tab is skipped. Stop the other backend to restart it." -ForegroundColor Yellow
    $Skip += "backend"
}
if ((Test-PortInUse 3000) -and -not ($Skip -contains "admin")) {
    Write-Host "Port 3000 is already in use, so the admin website tab is skipped." -ForegroundColor Yellow
    $Skip += "admin"
}

Add-Tab "backend" "Backend :8001" "$root\backend" "uv run uvicorn paymentsapp.main:app --host $backendHost --port 8001 --reload"
Add-Tab "worker" "Worker" "$root\backend" "uv run python -m paymentsapp.worker"
Add-Tab "admin" "Admin :3000" "$root\admin-web" "npm run dev"
$expo = if ($Device -eq "emulator") { "npx expo start --android" } else { "npx expo start" }
Add-Tab "mobile" "Expo" "$root\mobile" $expo

if ($tabs.Count -eq 0) { Fail "Nothing to start." }

$wtArgs = "-w new " + ($tabs -join " ; ")
if ($DryRun) {
    Write-Host "Would run: wt $wtArgs"
    exit 0
}
Start-Process wt -ArgumentList $wtArgs

Write-Host ""
Write-Host "Started $($tabs.Count) tab(s) in a new Windows Terminal window." -ForegroundColor Green
Write-Host "  Mobile app talks to: $apiUrl"
Write-Host "  Admin website:       http://localhost:3000   (sign-in codes are in its Dev inbox)"
if (-not ($Skip -contains "bank")) {
    Write-Host "  Mock bank:           $BankUrl   (opened in your browser; give it a minute to wake up)"
}
if ($Device -eq "phone") {
    Write-Host "  On the phone: open Expo Go and scan the QR code in the Expo tab."
}
else {
    Write-Host "  Start the Pixel_8a emulator first; Expo opens the app on it."
}
