# Publishing HyperFurion VK to the AUR

The files in this directory are a ready-to-publish AUR package
(`hyperfurion-vk`). They install the wheel, the `uinput` udev rule + module
load, and the systemd *user* service — the same setup `install.sh` does, the
Arch-native way.

## One-time

```bash
# needs an AUR account with your SSH key uploaded
git clone ssh://aur@aur.archlinux.org/hyperfurion-vk.git
cp packaging/aur/{PKGBUILD,99-uinput-hyperfurion.rules,uinput-hyperfurion.conf,voice-keyboard-daemon.service,hyperfurion-vk.install} hyperfurion-vk/
cd hyperfurion-vk
```

## Each release

```bash
# 1. bump pkgver in PKGBUILD to the new tag (drop the leading v); pkgrel=1
# 2. fill real checksums
updpkgsums
# 3. build + smoke-test locally
makepkg -f
# 4. lint
namcap PKGBUILD ./*.pkg.tar.zst
# 5. regenerate metadata
makepkg --printsrcinfo > .SRCINFO
# 6. commit + push
git add PKGBUILD .SRCINFO ./*.rules ./*.conf ./*.service ./*.install
git commit -m "upgpkg: hyperfurion-vk 2.1.3-1"
git push
```

Test a full install from a clean Arch container before pushing:

```bash
makepkg -si
```

After that, `yay -S hyperfurion-vk` (or any AUR helper) installs it, and the
package page becomes a discovery surface in its own right.
