# Kept so older links keep working: the Windows installer is now
# install-hyperfurion-vk.ps1 (same folder). This installs THIS checkout.
#   powershell -ExecutionPolicy Bypass -File packaging\windows\install-windows.ps1
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
& (Join-Path $PSScriptRoot "install-hyperfurion-vk.ps1") -Source $repo @args
