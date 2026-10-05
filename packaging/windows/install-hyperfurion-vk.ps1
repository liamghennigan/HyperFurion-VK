# HyperFurion VK - Windows installer.
#
# One line, in PowerShell (installs the latest release):
#   irm https://raw.githubusercontent.com/liamghennigan/HyperFurion-VK/main/packaging/windows/install-hyperfurion-vk.ps1 | iex
#
# From a checkout (installs that checkout):
#   powershell -ExecutionPolicy Bypass -File packaging\windows\install-hyperfurion-vk.ps1
#
# Options (file form):  -Version v2.2.0   -Source C:\path\to\checkout
#                       -NonInteractive   -NoLaunch   -NoAutostart
#                       -Provider xai -ApiKey xai-...   (unattended config;
#                       add -TtsProvider/-TtsApiKey when the speech-to-text
#                       provider has no voice: groq, deepgram, assemblyai)
# With irm | iex, use environment variables instead: HYPERFURION_VK_VERSION
# (a release tag, a branch, or a commit), HYPERFURION_VK_REPO,
# HYPERFURION_VK_NONINTERACTIVE=1.
#
# What it does (per user, no administrator rights needed):
#   - finds Python 3.11-3.13 (64-bit), or installs Python 3.12 for you
#   - installs HyperFurion VK into %LOCALAPPDATA%\HyperFurion-VK\venv
#   - adds the `voice-keyboard` command to your PATH
#   - adds "HyperFurion VK" to the Start menu, to Settings > Apps (for
#     uninstalling), and to startup (it lives in the notification area)
#   - helps you sign in or add a speech provider key, then starts it
# Your settings in %APPDATA%\voice-keyboard are never overwritten.

param(
    [string]$Version = "",
    [string]$Source = "",
    [switch]$NonInteractive,
    [switch]$NoLaunch,
    [switch]$NoAutostart,
    [string]$Provider = "",
    [string]$ApiKey = "",
    [string]$TtsProvider = "",
    [string]$TtsApiKey = ""
)

function Install-HyperFurionVK {
    param(
        [string]$Version,
        [string]$Source,
        [bool]$NonInteractive,
        [bool]$NoLaunch,
        [bool]$NoAutostart,
        [string]$Provider,
        [string]$ApiKey,
        [string]$TtsProvider,
        [string]$TtsApiKey
    )
    $ErrorActionPreference = "Stop"
    $ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest is 10x faster without it

    $DefaultVersion = "v2.2.0"   # stamped by release.yml
    $Repo = "liamghennigan/HyperFurion-VK"
    if ($env:HYPERFURION_VK_REPO) { $Repo = $env:HYPERFURION_VK_REPO }
    if (-not $Version -and $env:HYPERFURION_VK_VERSION) { $Version = $env:HYPERFURION_VK_VERSION }
    if ($env:HYPERFURION_VK_NONINTERACTIVE -eq "1" -or $env:CI) { $NonInteractive = $true }
    try {
        if (-not [Environment]::UserInteractive) { $NonInteractive = $true }
    } catch { }

    $AppName = "HyperFurion VK"
    $InstallRoot = Join-Path $env:LOCALAPPDATA "HyperFurion-VK"
    $Venv = Join-Path $InstallRoot "venv"
    $VenvPython = Join-Path $Venv "Scripts\python.exe"
    $VenvPythonW = Join-Path $Venv "Scripts\pythonw.exe"
    $BinDir = Join-Path $InstallRoot "bin"
    $Icon = Join-Path $InstallRoot "hyperfurion-vk.ico"
    $RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
    $UninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\HyperFurionVK"
    $Shortcut = Join-Path ([Environment]::GetFolderPath("Programs")) "$AppName.lnk"

    Write-Host ""
    Write-Host "=== $AppName for Windows ===" -ForegroundColor Cyan

    if ([Environment]::OSVersion.Version.Major -lt 10) {
        throw "$AppName needs Windows 10 or 11."
    }
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    } catch { }

    $upgrade = Test-Path $VenvPython
    if ($upgrade -or (Get-AppProcesses)) {
        Write-Step "Stopping the running copy"
        Stop-HyperFurionVK -VenvPython $VenvPython
    }

    # --- Python ---------------------------------------------------------
    Write-Step "Looking for Python 3.11-3.13 (64-bit)"
    $python = Find-Python
    if (-not $python) {
        Write-Step "No suitable Python found - installing Python 3.12 for your user"
        Install-Python
        $python = Find-Python
        if (-not $python) {
            throw "Python 3.12 was installed but could not be found. Open a new PowerShell window and run the installer again."
        }
    }
    Write-Host "    using $python"

    # --- virtual environment ----------------------------------------------
    if ((Test-Path $VenvPython) -and -not (Test-VenvHealthy $VenvPython)) {
        Write-Step "The existing environment is broken (Python was removed?) - recreating it"
        Remove-Item -Recurse -Force $Venv
    }
    if (-not (Test-Path $VenvPython)) {
        Write-Step "Creating the environment in $Venv"
        New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
        Invoke-Checked $python @("-m", "venv", $Venv) "creating the virtual environment"
    }

    # --- the package -------------------------------------------------------
    if ($Source) {
        $Source = (Resolve-Path $Source).Path
        if (-not (Test-Path (Join-Path $Source "pyproject.toml"))) {
            throw "-Source must be a HyperFurion VK checkout (no pyproject.toml in $Source)."
        }
        $spec = $Source
        Write-Step "Installing from $Source"
    } else {
        if (-not $Version) { $Version = Get-LatestTag -Repo $Repo -Fallback $DefaultVersion }
        $spec = "https://github.com/$Repo/archive/$Version.zip"
        Write-Step "Installing $Version from github.com/$Repo"
    }
    Invoke-Checked $VenvPython @("-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--upgrade", "pip") "upgrading pip"
    Invoke-Checked $VenvPython @("-m", "pip", "install", "--disable-pip-version-check", "--upgrade", $spec) "installing HyperFurion VK"
    # pip keeps an installed copy whose version number matches, but a
    # branch, a commit, or an edited checkout carries new code under the
    # same number: always lay the requested code down.
    Invoke-Checked $VenvPython @("-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--no-deps", "--force-reinstall", $spec) "refreshing HyperFurion VK"
    $check = Invoke-Native $VenvPython @("-c", "import voice_keyboard.windows.app, pyaudio, numpy")
    if ($check.Code -ne 0) {
        throw "The installed package is missing the Windows app or a dependency (is $Version older than v2.2.0?):`n$($check.Output)"
    }
    $installed = Get-MarkedLine (Invoke-Native $VenvPython @("-c", "from importlib.metadata import version; print('HFVK', version('voice-keyboard'))")).Output
    # Only now that the new app is in place: a failed install leaves an
    # early beta able to start at the next sign-in.
    Remove-BetaLeftovers

    # --- icon, command shim, PATH -------------------------------------------
    Write-Step "Adding the voice-keyboard command, Start menu entry and icon"
    $env:HFVK_ICON = $Icon
    Invoke-Checked $VenvPython @("-c", "import os; from voice_keyboard.windows.render import ico_bytes; open(os.environ['HFVK_ICON'], 'wb').write(ico_bytes())") "writing the icon"
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    # cmd reads .cmd files in the OEM code page, so a profile path with
    # non-ASCII characters can't be written into it literally; let cmd
    # expand %LOCALAPPDATA% itself.
    Set-Content -Path (Join-Path $BinDir "voice-keyboard.cmd") -Encoding ASCII -Value "@echo off`r`n`"%LOCALAPPDATA%\HyperFurion-VK\venv\Scripts\voice-keyboard.exe`" %*"
    Add-UserPath $BinDir
    if (($env:Path -split ";") -notcontains $BinDir) { $env:Path = "$BinDir;$env:Path" }

    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($Shortcut)
    $link.TargetPath = $VenvPythonW
    $link.Arguments = "-m voice_keyboard.windows"
    $link.WorkingDirectory = $InstallRoot
    $link.IconLocation = "$Icon,0"
    $link.Description = "Type with your voice in any app"
    $link.Save()

    $runCommand = "`"$VenvPythonW`" -m voice_keyboard.windows"
    $hadRun = $null -ne (Get-ItemProperty -Path $RunKey -Name $AppName -ErrorAction SilentlyContinue)
    if ($NoAutostart) {
        Remove-ItemProperty -Path $RunKey -Name $AppName -ErrorAction SilentlyContinue
    } elseif (-not $upgrade -or $hadRun) {
        # A fresh install starts with Windows; an upgrade keeps whatever the
        # tray's "Start with Windows" toggle was set to.
        New-ItemProperty -Path $RunKey -Name $AppName -Value $runCommand -PropertyType String -Force | Out-Null
    }

    # --- uninstaller + Settings > Apps entry ---------------------------------
    $uninstaller = Join-Path $InstallRoot "uninstall.ps1"
    Set-Content -Path $uninstaller -Encoding ASCII -Value (Get-UninstallerScript)
    $sizeKb = [int]((Get-ChildItem -Recurse -Force -File $InstallRoot -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum / 1KB)
    $ps = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    New-Item -Path $UninstallKey -Force | Out-Null
    $values = @{
        DisplayName = $AppName
        DisplayVersion = $installed
        Publisher = "HyperFurion"
        DisplayIcon = $Icon
        InstallLocation = $InstallRoot
        URLInfoAbout = "https://github.com/$Repo"
        HelpLink = "https://github.com/$Repo#windows"
        UninstallString = "`"$ps`" -NoProfile -ExecutionPolicy Bypass -File `"$uninstaller`" -Pause"
        QuietUninstallString = "`"$ps`" -NoProfile -ExecutionPolicy Bypass -File `"$uninstaller`""
    }
    foreach ($name in $values.Keys) {
        New-ItemProperty -Path $UninstallKey -Name $name -Value $values[$name] -PropertyType String -Force | Out-Null
    }
    foreach ($name in @("NoModify", "NoRepair")) {
        New-ItemProperty -Path $UninstallKey -Name $name -Value 1 -PropertyType DWord -Force | Out-Null
    }
    New-ItemProperty -Path $UninstallKey -Name "EstimatedSize" -Value $sizeKb -PropertyType DWord -Force | Out-Null

    # --- settings ----------------------------------------------------------------
    # Ask the app where its settings live (it honors XDG_CONFIG_HOME, and an
    # early beta's settings are moved into place first).
    $migration = Invoke-Native $VenvPython @("-c", "from voice_keyboard.paths import migrate_windows_beta as m; moved, failed = m(); print(chr(10).join(moved + failed))")
    if ($migration.Code -eq 0 -and $migration.Output.Trim()) {
        $migration.Output.Trim() -split "`n" | ForEach-Object { Write-Host "    $_" }
    }
    $ConfigFile = Get-PythonPath $VenvPython "paths.config_dir() / 'config.toml'"
    if (Test-Path -LiteralPath $ConfigFile) {
        Write-Step "Keeping your settings in $ConfigFile"
    } elseif ($Provider) {
        Write-Step "Writing settings for $Provider"
        Write-InitialConfig -VenvPython $VenvPython -Stt $Provider -SttKey $ApiKey -Tts $TtsProvider -TtsKey $TtsApiKey
    } elseif (-not $NonInteractive) {
        Invoke-SetupPrompt -VenvPython $VenvPython
    } else {
        Write-Host "    No settings yet: the tray icon will be amber until you sign in or add a key."
    }

    # --- launch ------------------------------------------------------------------
    if (-not $NoLaunch) {
        Write-Step "Starting $AppName"
        Start-Process -FilePath $VenvPythonW -ArgumentList "-m", "voice_keyboard.windows" -WorkingDirectory $InstallRoot
    }

    Write-Host ""
    Write-Host "=== Done: $AppName $installed ===" -ForegroundColor Green
    Write-Host "  Dictate:        Ctrl+Alt+V  (tap to start/stop, or hold to talk)"
    Write-Host "  Ask Kai:        hold Right Ctrl, or click the orb"
    Write-Host "  Read aloud:     select text, press Ctrl+Alt+R"
    Write-Host "  Settings:       right-click the tray icon (notification area)"
    Write-Host "  Settings file:  $ConfigFile"
    Write-Host "  Command line:   voice-keyboard status   (in a NEW terminal)"
    Write-Host "  Uninstall:      Settings > Apps > $AppName"
}

function Write-Step([string]$Message) {
    Write-Host "--> $Message" -ForegroundColor Cyan
}

function Invoke-Native {
    # Windows PowerShell turns a native command's stderr into terminating
    # errors under ErrorActionPreference=Stop; run with Continue and judge
    # by the exit code instead.
    param([string]$Exe, [string[]]$Arguments)
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $Exe @Arguments 2>&1 | ForEach-Object { "$_" }
        return @{ Code = $LASTEXITCODE; Output = ($output -join "`n") }
    } finally {
        $ErrorActionPreference = $old
    }
}

function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments, [string]$What)
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Exe @Arguments
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $old
    }
    if ($code -ne 0) { throw "Failed while $What (exit code $code)." }
}

function Get-MarkedLine([string]$Output) {
    # Native stderr is merged into the output, in no fixed order; the value
    # asked for is the line Python tagged HFVK.
    foreach ($line in ($Output -split "`n")) {
        $line = $line.Trim()
        if ($line.StartsWith("HFVK ")) { return $line.Substring(5) }
    }
    return $null
}

function ConvertFrom-HexUtf8([string]$Hex) {
    # Paths come back from Python hex-encoded: console output is decoded
    # with the OEM code page, which mangles a profile folder like "Jose"
    # with an accent.
    if (-not $Hex) { return $null }
    $Hex = $Hex.Trim()
    if ($Hex -notmatch '^([0-9a-fA-F]{2})+$') { return $null }
    $bytes = New-Object byte[] ($Hex.Length / 2)
    for ($i = 0; $i -lt $bytes.Length; $i++) {
        $bytes[$i] = [Convert]::ToByte($Hex.Substring($i * 2, 2), 16)
    }
    return [Text.Encoding]::UTF8.GetString($bytes)
}

function Get-PythonPath([string]$Python, [string]$Expression) {
    # A path computed by the installed app (voice_keyboard.paths in scope).
    $r = Invoke-Native $Python @("-c", "from voice_keyboard import paths; print('HFVK', str($Expression).encode('utf-8').hex())")
    $path = ConvertFrom-HexUtf8 (Get-MarkedLine $r.Output)
    if ($r.Code -ne 0 -or -not $path) { throw "Could not ask the app where its files live:`n$($r.Output)" }
    return $path
}

function Test-PythonCandidate([string]$Exe) {
    # A 64-bit x64 CPython 3.11-3.13 (also fine under x64 emulation on
    # ARM64): the audio stack (PyAudio) ships wheels for exactly that.
    # sysconfig names the build; platform.machine() names the CPU, which
    # reads ARM64 for an x64 Python on an ARM64 PC.
    if (-not $Exe -or -not (Test-Path -LiteralPath $Exe)) { return $null }
    if ($Exe -like "*\WindowsApps\*") { return $null }   # the Microsoft Store alias stub
    $probe = Invoke-Native $Exe @("-c", "import sys, sysconfig; print('HFVK', sys.executable.encode('utf-8').hex(), '%d.%d' % sys.version_info[:2], sysconfig.get_platform())")
    if ($probe.Code -ne 0) { return $null }
    $fields = @((Get-MarkedLine $probe.Output) -split " ")
    if ($fields.Count -ne 3) { return $null }
    $ok = (@("3.11", "3.12", "3.13") -contains $fields[1]) -and ($fields[2] -eq "win-amd64")
    if ($ok) { return ConvertFrom-HexUtf8 $fields[0] }
    return $null
}

function Find-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in @("3.12", "3.13", "3.11")) {
            $r = Invoke-Native "py" @("-$v-64", "-c", "import sys; print('HFVK', sys.executable.encode('utf-8').hex())")
            if ($r.Code -eq 0) {
                $found = Test-PythonCandidate (ConvertFrom-HexUtf8 (Get-MarkedLine $r.Output))
                if ($found) { return $found }
            }
        }
    }
    foreach ($v in @("312", "313", "311")) {
        $found = Test-PythonCandidate (Join-Path $env:LOCALAPPDATA "Programs\Python\Python$v\python.exe")
        if ($found) { return $found }
        $found = Test-PythonCandidate (Join-Path $env:ProgramFiles "Python$v\python.exe")
        if ($found) { return $found }
    }
    foreach ($cmd in @(Get-Command python, python3 -All -ErrorAction SilentlyContinue)) {
        $found = Test-PythonCandidate $cmd.Source
        if ($found) { return $found }
    }
    return $null
}

function Install-Python {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        # x64 even on an ARM64 PC: the audio stack has no ARM64 wheels.
        $r = Invoke-Native $winget.Source @("install", "--id", "Python.Python.3.12", "--exact", "--scope", "user", "--architecture", "x64", "--silent", "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity")
        if ($r.Code -eq 0 -and (Find-Python)) { return }
        Write-Host "    winget could not install Python ($($r.Code)); using the python.org installer"
    }
    $url = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
    $setup = Join-Path $env:TEMP "python-3.12.10-amd64.exe"
    Invoke-WebRequest -Uri $url -OutFile $setup -UseBasicParsing
    $target = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312"
    $p = Start-Process -FilePath $setup -Wait -PassThru -ArgumentList @(
        "/quiet", "InstallAllUsers=0", "PrependPath=0", "Include_test=0",
        "Include_launcher=1", "InstallLauncherAllUsers=0", "TargetDir=`"$target`""
    )
    Remove-Item $setup -ErrorAction SilentlyContinue
    if ($p.ExitCode -ne 0) { throw "The Python installer failed (exit code $($p.ExitCode))." }
}

function Test-VenvHealthy([string]$VenvPython) {
    return (Invoke-Native $VenvPython @("-c", "import sys")).Code -eq 0
}

function Get-LatestTag([string]$Repo, [string]$Fallback) {
    try {
        $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" -UseBasicParsing -TimeoutSec 20
        if ($release.tag_name) { return $release.tag_name }
    } catch {
        Write-Host "    could not ask GitHub for the latest release; installing $Fallback"
    }
    return $Fallback
}

function Get-AppProcesses {
    # The app, a console daemon, and an early beta's voice-keyboard-daemon.exe
    # - this user's, in any of their sessions (they share this install), and
    # never another user's.
    $me = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe' OR Name = 'voice-keyboard-daemon.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -eq "voice-keyboard-daemon.exe" -or $_.CommandLine -like "*voice_keyboard.windows*" -or $_.CommandLine -like "*voice-keyboard-daemon*" } |
        Where-Object {
            $owner = Invoke-CimMethod -InputObject $_ -MethodName GetOwnerSid -ErrorAction SilentlyContinue
            $owner -and $owner.Sid -eq $me
        }
}

function Stop-HyperFurionVK([string]$VenvPython) {
    if (Test-Path -LiteralPath $VenvPython) {
        Invoke-Native $VenvPython @("-m", "voice_keyboard.client", "quit") | Out-Null
    }
    for ($i = 0; $i -lt 30; $i++) {
        if (-not (Get-AppProcesses)) { return }
        Start-Sleep -Milliseconds 500
    }
    Get-AppProcesses | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 500
}

function Remove-BetaLeftovers {
    # Early betas started voice-keyboard-daemon.exe from the Startup folder;
    # left there it would run alongside the app at the next sign-in.
    # Best effort: nothing here may fail the install.
    $launcher = Join-Path ([Environment]::GetFolderPath("Startup")) "hyperfurion-vk-daemon.cmd"
    if (-not (Test-Path -LiteralPath $launcher)) { return }
    $betaExe = $null
    try {
        $text = [string](Get-Content -LiteralPath $launcher -Raw -ErrorAction SilentlyContinue)
        $match = [regex]::Match($text, '"([^"]*voice-keyboard-daemon\.exe)"')
        if ($match.Success) { $betaExe = $match.Groups[1].Value }
    } catch { }
    Write-Step "Removing the beta's startup launcher"
    Remove-Item -LiteralPath $launcher -Force -ErrorAction SilentlyContinue
    if ($betaExe) {
        # Its commands stay installed (pip's user Scripts folder); ours now
        # come first on PATH, but say how to remove them for good.
        $scripts = Split-Path -Parent $betaExe
        $version = ""
        if ($scripts -match 'Python(\d)(\d+)\\Scripts$') { $version = " -$($Matches[1]).$($Matches[2])" }
        Write-Host "    The beta's own commands are still in $scripts."
        Write-Host "    To remove them: py$version -m pip uninstall voice-keyboard"
    }
}

function Send-EnvironmentChange {
    # Tell Explorer (and new terminals) that the user PATH changed.
    try {
        if (-not ("HFVK.NativeMethods" -as [type])) {
            Add-Type -Namespace HFVK -Name NativeMethods -MemberDefinition @"
[System.Runtime.InteropServices.DllImport("user32.dll", SetLastError = true, CharSet = System.Runtime.InteropServices.CharSet.Unicode)]
public static extern System.IntPtr SendMessageTimeout(System.IntPtr hWnd, uint Msg, System.UIntPtr wParam, string lParam, uint fuFlags, uint uTimeout, out System.UIntPtr lpdwResult);
"@
        }
        $result = [UIntPtr]::Zero
        [HFVK.NativeMethods]::SendMessageTimeout([IntPtr]0xffff, 0x1A, [UIntPtr]::Zero, "Environment", 2, 5000, [ref]$result) | Out-Null
    } catch { }
}

function Get-RawUserPath {
    # Read without expanding %VARIABLES%, so writing it back keeps them.
    $key = Get-Item -Path "HKCU:\Environment"
    return [string]$key.GetValue("Path", "", [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
}

function Add-UserPath([string]$Dir) {
    # First in the user PATH, so another voice-keyboard (an early beta's,
    # from pip's Scripts folder) can't shadow ours.
    $current = Get-RawUserPath
    $parts = @($current -split ";" | Where-Object { $_ -ne "" })
    $others = @($parts | Where-Object { $_.TrimEnd("\") -ine $Dir.TrimEnd("\") })
    if ($parts.Count -eq $others.Count + 1 -and $parts[0].TrimEnd("\") -ieq $Dir.TrimEnd("\")) { return }
    $updated = @($Dir) + $others
    New-ItemProperty -Path "HKCU:\Environment" -Name "Path" -Value ($updated -join ";") -PropertyType ExpandString -Force | Out-Null
    Send-EnvironmentChange
}

function Read-Secret([string]$Prompt) {
    $secure = Read-Host -Prompt $Prompt -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr).Trim()
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

function Write-InitialConfig {
    param([string]$VenvPython, [string]$Stt, [string]$Tts = "", [string]$SttKey = "", [string]$TtsKey = "", [string]$BaseUrl = "")
    if (-not $Tts) {
        $Tts = $Stt
        if (@("xai", "openai", "hyperfurion", "elevenlabs") -notcontains $Stt) { $Tts = "xai" }
    }
    $env:HFVK_STT = $Stt
    $env:HFVK_TTS = $Tts
    $env:HFVK_STT_KEY = $SttKey
    $env:HFVK_TTS_KEY = $TtsKey
    $env:HFVK_BASE_URL = $BaseUrl
    try {
        Invoke-Checked $VenvPython @("-c", "from voice_keyboard.windows.app import write_config_from_env as w; raise SystemExit(w())") "writing your settings"
    } finally {
        foreach ($name in @("HFVK_STT", "HFVK_TTS", "HFVK_STT_KEY", "HFVK_TTS_KEY", "HFVK_BASE_URL")) {
            Remove-Item "Env:\$name" -ErrorAction SilentlyContinue
        }
    }
}

function Invoke-SetupPrompt([string]$VenvPython) {
    Write-Host ""
    Write-Host "How should your speech be transcribed?" -ForegroundColor Cyan
    Write-Host "  1) HyperFurion hosted service - sign in with your subscription email"
    Write-Host "  2) My own API key (xAI, OpenAI, Groq, Deepgram, AssemblyAI)"
    Write-Host "  3) A local OpenAI-compatible server (fully offline)"
    Write-Host "  4) Skip for now (set it up later from the tray icon)"
    $choice = (Read-Host "Choose 1-4 [4]").Trim()
    switch ($choice) {
        "1" {
            try {
                Invoke-Checked $VenvPython @("-m", "voice_keyboard.client", "login") "signing in"
            } catch {
                Write-Host "    Sign-in did not finish. Run 'voice-keyboard login' in a new terminal, or use the tray icon." -ForegroundColor Yellow
            }
        }
        "2" {
            $stt = (Read-Host "Speech-to-text provider: xai, openai, groq, deepgram, assemblyai [xai]").Trim().ToLower()
            if (-not $stt) { $stt = "xai" }
            if (@("xai", "openai", "groq", "deepgram", "assemblyai") -notcontains $stt) { throw "Unknown provider '$stt'." }
            $sttKey = Read-Secret "$stt API key (input hidden)"
            $tts = $stt
            $ttsKey = ""
            if (@("xai", "openai") -notcontains $stt) {
                Write-Host "$stt does not do text-to-speech (used for read-aloud and Kai's voice)."
                $tts = (Read-Host "Text-to-speech provider: xai, openai, elevenlabs [xai]").Trim().ToLower()
                if (-not $tts) { $tts = "xai" }
                if (@("xai", "openai", "elevenlabs") -notcontains $tts) { throw "Unknown provider '$tts'." }
                $ttsKey = Read-Secret "$tts API key (input hidden)"
            }
            Write-InitialConfig -VenvPython $VenvPython -Stt $stt -Tts $tts -SttKey $sttKey -TtsKey $ttsKey
        }
        "3" {
            $url = (Read-Host "Server base URL [http://127.0.0.1:8000/v1]").Trim()
            if (-not $url) { $url = "http://127.0.0.1:8000/v1" }
            Write-InitialConfig -VenvPython $VenvPython -Stt "openai" -Tts "openai" -BaseUrl $url
        }
        default {
            Write-Host "    Skipped: right-click the amber tray icon to sign in or open the settings file."
        }
    }
}

function Get-UninstallerScript {
    return @'
# HyperFurion VK - uninstaller. Written by the installer; also run from
# Settings > Apps. -Purge also deletes your settings and dictation history.
param([switch]$Purge, [switch]$Pause)

$ErrorActionPreference = "Continue"
$AppName = "HyperFurion VK"
$InstallRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$BinDir = Join-Path $InstallRoot "bin"
Set-Location $env:TEMP

Write-Host "Uninstalling $AppName..." -ForegroundColor Cyan
$python = Join-Path $InstallRoot "venv\Scripts\python.exe"
if (Test-Path $python) { & $python -m voice_keyboard.client quit 2>$null | Out-Null }
# This user's copies, in any of their sessions (they share this install).
$me = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
for ($i = 0; $i -lt 20; $i++) {
    $procs = Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*voice_keyboard.windows*" -or $_.CommandLine -like "*voice-keyboard-daemon*" } |
        Where-Object {
            $owner = Invoke-CimMethod -InputObject $_ -MethodName GetOwnerSid -ErrorAction SilentlyContinue
            $owner -and $owner.Sid -eq $me
        }
    if (-not $procs) { break }
    if ($i -eq 19) { $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } }
    Start-Sleep -Milliseconds 500
}

Remove-Item (Join-Path ([Environment]::GetFolderPath("Programs")) "$AppName.lnk") -ErrorAction SilentlyContinue
$run = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$value = (Get-ItemProperty -Path $run -Name $AppName -ErrorAction SilentlyContinue).$AppName
if ($value -and $value -like "*$InstallRoot*") { Remove-ItemProperty -Path $run -Name $AppName -ErrorAction SilentlyContinue }
Remove-Item "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\HyperFurionVK" -Recurse -ErrorAction SilentlyContinue

$envKey = Get-Item -Path "HKCU:\Environment"
$path = [string]$envKey.GetValue("Path", "", [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
$kept = @($path -split ";" | Where-Object { $_ -ne "" -and $_.TrimEnd("\") -ine $BinDir.TrimEnd("\") })
if ($kept.Count -ne @($path -split ";" | Where-Object { $_ -ne "" }).Count) {
    New-ItemProperty -Path "HKCU:\Environment" -Name "Path" -Value ($kept -join ";") -PropertyType ExpandString -Force | Out-Null
    [Environment]::SetEnvironmentVariable("HFVK_PATH_REFRESH", "1", "User")
    [Environment]::SetEnvironmentVariable("HFVK_PATH_REFRESH", $null, "User")
}

if ($Purge) {
    $data = @(
        (Join-Path $env:APPDATA "voice-keyboard"),
        (Join-Path $env:LOCALAPPDATA "voice-keyboard"),
        # Where an early beta kept them: left behind, a reinstall would
        # bring them back.
        (Join-Path $HOME ".config\voice-keyboard"),
        (Join-Path $HOME ".local\state\voice-keyboard")
    )
    if ($env:XDG_CONFIG_HOME) { $data += Join-Path $env:XDG_CONFIG_HOME "voice-keyboard" }
    if ($env:XDG_STATE_HOME) { $data += Join-Path $env:XDG_STATE_HOME "voice-keyboard" }
    foreach ($dir in $data) { Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue }
}
Remove-Item $InstallRoot -Recurse -Force -ErrorAction SilentlyContinue
if (Test-Path $InstallRoot) {
    # Something still held a file (this script, a lingering process): retry
    # from a detached process once we have exited.
    Start-Process -FilePath "cmd.exe" -WindowStyle Hidden -ArgumentList "/c", "ping -n 4 127.0.0.1 >nul & rmdir /s /q `"$InstallRoot`""
}

Write-Host "$AppName is uninstalled." -ForegroundColor Green
if (-not $Purge) {
    Write-Host "Your settings are kept in $(Join-Path $env:APPDATA 'voice-keyboard') (uninstall with -Purge to remove them)."
}
if ($Pause) { Read-Host "Press Enter to close" | Out-Null }
'@
}

try {
    # [bool] casts: a switch that was never bound ($null) must read as off,
    # not fail parameter binding.
    Install-HyperFurionVK -Version $Version -Source $Source -NonInteractive:([bool]$NonInteractive) `
        -NoLaunch:([bool]$NoLaunch) -NoAutostart:([bool]$NoAutostart) -Provider $Provider -ApiKey $ApiKey `
        -TtsProvider $TtsProvider -TtsApiKey $TtsApiKey
    $global:LASTEXITCODE = 0   # probes along the way (py -3.x) may have left it non-zero
} catch {
    Write-Host ""
    Write-Host "Installation failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Help: https://github.com/liamghennigan/HyperFurion-VK#windows"
    # Run as a file (-File, CI): report failure through the exit code. Under
    # irm | iex there is no script file, and exit would close the window.
    if ($PSCommandPath) { exit 1 }
}
