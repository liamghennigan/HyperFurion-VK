// ═══ INSTALL — the right download first, no network ═══════════════════════
// The visitor's own platform leads: Linux and Windows get their download,
// a Mac gets the beta's install-from-a-checkout steps, with the Linux and
// Windows downloads still a click away.
import { $, os } from "./env.js";

const REL = "https://github.com/liamghennigan/HyperFurion-VK/releases/latest/download/";
const MAC = "https://github.com/liamghennigan/HyperFurion-VK#macos-beta";
const DL = {
  linux:   { label: "Download for Linux",   href: REL + "HyperFurion-VK-Setup.run", download: true,
             note: "HyperFurion-VK-Setup.run · run it as yourself, not with sudo; it asks for your password only for the system steps (audio libraries, keyboard access)" },
  windows: { label: "Download for Windows", href: REL + "HyperFurion-VK-Setup.cmd", download: true,
             note: "HyperFurion-VK-Setup.cmd · double-click it, no administrator needed; if Windows warns about a downloaded file: More info › Run anyway" },
  mac:     { label: "macOS beta — install from a checkout", href: MAC, download: false,
             note: "macOS is in beta: clone the repository and run packaging/macos/install-macos.sh, then allow Accessibility and Microphone for Python — the README has the steps" },
};
const CMDS = {
  linux: "curl -fsSL https://github.com/liamghennigan/HyperFurion-VK/releases/latest/download/install-hyperfurion-vk.sh | bash",
  win: "irm https://raw.githubusercontent.com/liamghennigan/HyperFurion-VK/main/packaging/windows/install-hyperfurion-vk.ps1 | iex",
};

(() => {
  const primary = $("dl-primary"), secondary = $("dl-secondary"), nav = $("nav-dl"), hero = $("hero-dl"), note = $("dl-note");
  const order = os === "windows" ? ["windows", "linux"] : os === "mac" ? ["mac", "linux", "windows"] : ["linux", "windows"];
  const set = (a, d) => {
    if (!a) return;
    a.href = d.href; a.textContent = d.label;
    if (d.download) a.setAttribute("download", ""); else a.removeAttribute("download");
  };
  set(primary, DL[order[0]]); set(secondary, DL[order[1]]);
  if (order[2] && secondary && !$("dl-third")) {
    // a Mac keeps both downloads: the third one sits beside the second
    const third = secondary.cloneNode(false);
    third.id = "dl-third";
    set(third, DL[order[2]]);
    secondary.insertAdjacentElement("afterend", third);
  }
  if (note) note.textContent = DL[order[0]].note;
  if (os === "mac") {
    set(hero, DL.mac);
    if (nav) { nav.textContent = "Download"; nav.href = "#install"; nav.removeAttribute("download"); }
  } else if (os === "mobile" || os === "other") {
    if (hero) { hero.textContent = "Get it for your desktop"; hero.href = "#install"; hero.removeAttribute("download"); }
    if (nav) { nav.textContent = "Download"; nav.href = "#install"; nav.removeAttribute("download"); }
  } else {
    set(hero, DL[order[0]]); set(nav, DL[order[0]]);
    if (nav) nav.textContent = innerWidth < 720 ? "Download" : DL[order[0]].label;
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
