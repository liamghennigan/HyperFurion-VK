# Distribution & packaging

Channels for HyperFurion VK, in order of leverage for a Linux input tool:

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

## Why we deliberately do NOT ship a Flatpak

A voice keyboard's entire job is to inject keystrokes **into other apps** via
`/dev/uinput` and to read the focused window over **AT-SPI**. Flatpak's sandbox
exists precisely to stop one app from driving another. Even with `--device=all`,
reliable cross-app input injection + AT-SPI introspection + a persistent
background daemon fight the model, and a systemd **user** service is not how
Flatpaks are meant to run. A Flatpak here would be a broken promise, so we don't
publish one. The curl installer + AUR + `.deb` + AppImage cover Linux honestly.
