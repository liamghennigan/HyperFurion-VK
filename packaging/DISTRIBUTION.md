# Distribution & packaging

Channels for HyperFurion VK, in order of leverage. Linux:

1. **curl installer (default)** — `releases/latest/download/install-hyperfurion-vk.sh`.
   Downloads the release tarball and runs `install.sh` (venv + uinput + systemd
   user unit). This is what the landing page and README use.
2. **AUR** — [`packaging/aur/`](aur/README.md). Arch users are vocal early
   adopters, and the package handles the `uinput` udev rule + module load +
   systemd user service natively. Publishing it also creates a discovery page.
3. **`.deb` (Debian/Ubuntu)** — *TODO.* Depend on `python3`, `python3-venv`,
   `portaudio19`, `libsndfile1`, `libnotify-bin`; ship the same udev rule and a
   systemd user unit; `postinst` prints the `input`-group + login steps.
4. **AppImage** — *TODO.* A self-contained "download and run" is ideal for the
   launch/demo. It still has to request `/dev/uinput` access (add the user to
   `input`) on first run — bundle a small first-run helper for that.

## Windows

1. **PowerShell installer (default)** —
   `packaging/windows/install-hyperfurion-vk.ps1`, run as
   `irm https://raw.githubusercontent.com/liamghennigan/HyperFurion-VK/main/packaging/windows/install-hyperfurion-vk.ps1 | iex`
   (it installs the latest release) or attached to each release with its
   version pinned. Per-user, no admin rights: installs Python 3.12 if needed,
   a venv under `%LOCALAPPDATA%\HyperFurion-VK`, a Start menu entry,
   start-with-Windows, and a Settings › Apps entry with an uninstaller. CI
   (`windows-installer.yml`) installs, runs, upgrades, and uninstalls it on a
   real Windows runner. `irm | iex` scripts are not subject to SmartScreen.
2. **winget** — *TODO.* A manifest needs an installer binary (an EXE/MSI or
   a zip); the natural route is a frozen build (below) published per release.
3. **Scoop** — *TODO.* A bucket manifest can wrap the same frozen zip, with
   the tray app as a shortcut and `voice-keyboard` as a shim.
4. **Frozen build (PyInstaller / Nuitka)** — *TODO.* Removes the Python step
   and enables 2 and 3. Unsigned binaries trip SmartScreen until they earn
   reputation, so code signing should come with it.

## Why we deliberately do NOT ship a Flatpak

A voice keyboard's entire job is to inject keystrokes **into other apps** via
`/dev/uinput` and to read the focused window over **AT-SPI**. Flatpak's sandbox
exists precisely to stop one app from driving another. Even with `--device=all`,
reliable cross-app input injection + AT-SPI introspection + a persistent
background daemon fight the model, and a systemd **user** service is not how
Flatpaks are meant to run. A Flatpak here would be a broken promise, so we don't
publish one. The curl installer + AUR + `.deb` + AppImage cover Linux honestly.
