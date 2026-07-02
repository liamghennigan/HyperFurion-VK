// ═══ CTA — the install command is the call to action ══════════════════════
// Every marked snippet gets a copy button (JS-only, so the no-JS document
// stays plain), and the first time a line types itself the funnel card
// appears once — no popups, no banners, nothing sticky.
import { $ } from "./env.js";
import { bus } from "./bus.js";

async function copy(text, btn, idle) {
  try {
    await navigator.clipboard.writeText(text);
    btn.textContent = "copied ✓";
    btn.classList.add("ok");
  } catch {
    btn.textContent = "select it + ctrl+c";
  }
  setTimeout(() => { btn.textContent = idle; btn.classList.remove("ok"); }, 2000);
}

for (const pre of document.querySelectorAll("pre[data-copy]")) {
  const wrap = document.createElement("div");
  wrap.className = "prewrap";
  pre.parentNode.insertBefore(wrap, pre);
  wrap.appendChild(pre);
  const b = document.createElement("button");
  b.type = "button";
  b.className = "copybtn";
  b.textContent = "copy";
  b.setAttribute("aria-label", "Copy this snippet");
  b.addEventListener("click", () =>
    copy((pre.querySelector("code") || pre).textContent, b, "copy"));
  wrap.appendChild(b);
}

const card = $("funnel-card"), funnelCopy = $("funnel-copy");
if (card && funnelCopy) {
  let shown = false;
  bus.on("type:text", () => {
    if (shown) return;
    shown = true;
    card.hidden = false;
  });
  funnelCopy.addEventListener("click", () =>
    copy($("install-cmd").textContent.trim(), funnelCopy, "copy the install command"));
}
