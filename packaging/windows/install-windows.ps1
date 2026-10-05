# Kept so older links keep working: the Windows installer is now
# install-hyperfurion-vk.ps1 (same folder). This installs THIS checkout.
#   powershell -ExecutionPolicy Bypass -File packaging\windows\install-windows.ps1
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
# Stays 1 if the installer can't even start (a bad argument); it sets 0 on
# success and exits 1 on failure itself.
$global:LASTEXITCODE = 1
& (Join-Path $PSScriptRoot "install-hyperfurion-vk.ps1") -Source $repo @args
exit $LASTEXITCODE
