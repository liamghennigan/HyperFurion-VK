// ═══ INSTALL — the right download first, no network ═══════════════════════
import { $, os } from "./env.js";

const REL = "https://github.com/liamghennigan/HyperFurion-VK/releases/latest/download/";
const DL = {
  linux:   { label: "Download for Linux",   href: REL + "HyperFurion-VK-Setup.run", note: "HyperFurion-VK-Setup.run · run it as yourself, no sudo for the app" },
  windows: { label: "Download for Windows", href: REL + "HyperFurion-VK-Setup.cmd", note: "HyperFurion-VK-Setup.cmd · double-click; if Windows warns: More info › Run anyway" },
};
const CMDS = {
  linux: "curl -fsSL https://github.com/liamghennigan/HyperFurion-VK/releases/latest/download/install-hyperfurion-vk.sh | bash",
  win: "irm https://raw.githubusercontent.com/liamghennigan/HyperFurion-VK/main/packaging/windows/install-hyperfurion-vk.ps1 | iex",
};

(() => {
  const primary = $("dl-primary"), secondary = $("dl-secondary"), nav = $("nav-dl"), hero = $("hero-dl"), note = $("dl-note");
  const first = os === "windows" ? "windows" : "linux";
  const second = first === "linux" ? "windows" : "linux";
  const set = (a, d) => { if (a) { a.href = d.href; a.textContent = d.label; a.setAttribute("download", ""); } };
  set(primary, DL[first]); set(secondary, DL[second]);
  if (note) note.textContent = DL[first].note;
  if (os === "mac") {
    if (hero) { hero.textContent = "macOS beta — install from a checkout"; hero.href = "https://github.com/liamghennigan/HyperFurion-VK#macos-beta"; hero.removeAttribute("download"); }
    if (nav) { nav.textContent = "Download"; nav.href = "#install"; }
  } else if (os === "mobile" || os === "other") {
    if (hero) { hero.textContent = "Get it for your desktop"; hero.href = "#install"; hero.removeAttribute("download"); }
    if (nav) { nav.textContent = "Download"; nav.href = "#install"; }
  } else {
    set(hero, DL[first]); set(nav, DL[first]);
    if (nav) nav.textContent = innerWidth < 720 ? "Download" : DL[first].label;
  }
  // copy buttons
  for (const b of document.querySelectorAll("[data-copy]")) {
    b.addEventListener("click", async () => {
      const text = CMDS[b.dataset.copy] || b.dataset.copy;
      try { await navigator.clipboard.writeText(text); b.textContent = "copied"; b.classList.add("ok"); }
      catch { b.textContent = "select it"; }
      setTimeout(() => { b.textContent = "copy"; b.classList.remove("ok"); }, 1800);
    });
  }
})();
