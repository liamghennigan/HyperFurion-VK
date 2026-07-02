# HyperFurion VK — Windows installer (BETA). Run from a repo checkout in
# PowerShell:
#   git clone https://github.com/liamghennigan/HyperFurion-VK
#   cd HyperFurion-VK
#   powershell -ExecutionPolicy Bypass -File packaging\windows\install-windows.ps1
$ErrorActionPreference = "Stop"

Write-Host "=== HyperFurion VK Windows installer (beta) ===" -ForegroundColor Cyan
Write-Host ""

$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

# Python 3.11+
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { throw "Python 3.11+ is required (python.org or winget install Python.Python.3.12)" }

Write-Host "Installing voice-keyboard (user site)..."
python -m pip install --user $repo

$scripts = python -c "import site, os; print(os.path.join(site.USER_BASE, 'Scripts'))"
$daemon = Join-Path $scripts "voice-keyboard-daemon.exe"
if (-not (Test-Path $daemon)) { throw "daemon entry point not found at $daemon" }

# Starter config
$configDir = Join-Path $env:APPDATA "voice-keyboard"
if ($env:XDG_CONFIG_HOME) { $configDir = Join-Path $env:XDG_CONFIG_HOME "voice-keyboard" }
$configFile = Join-Path $configDir "config.toml"
if (-not (Test-Path $configFile)) {
    New-Item -ItemType Directory -Force -Path $configDir | Out-Null
    Copy-Item (Join-Path $repo "config.toml.example") $configFile
    Write-Host "Wrote starter config: $configFile (add your API key)"
}

# Start at login via a Startup-folder launcher (no admin needed)
$startup = [Environment]::GetFolderPath("Startup")
$launcher = Join-Path $startup "hyperfurion-vk-daemon.cmd"
"@echo off`nstart `"`" /min `"$daemon`"" | Set-Content -Path $launcher -Encoding ascii
Write-Host "Installed startup launcher: $launcher"

# Start it now
Start-Process -WindowStyle Hidden $daemon
Write-Host ""
Write-Host "=== Done (beta) ===" -ForegroundColor Cyan
Write-Host "Config:  $configFile"
Write-Host "Check:   `"$scripts\voice-keyboard.exe`" status"
Write-Host ""
Write-Host "Notes: injection uses SendInput (full Unicode); hotkeys use a"
Write-Host "low-level keyboard hook; IPC is loopback TCP (127.0.0.1:48765)."
Write-Host "Status feedback arrives as notification-center toasts."
