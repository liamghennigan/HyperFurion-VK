// ═══ DEMO UI — the hosted relay, as a visible opt-in ══════════════════════
// The page never touches the network on its own. This sheet is the only
// door: it says exactly what would be sent and where, and nothing leaves
// until a button in it is pressed. The footer counts what you asked for.
import { $ } from "./env.js";
import { bus } from "./bus.js";
import { Demo, AudioOut } from "./demo-relay.js";
import { Window } from "./window.js";

export const DemoUI = (() => {
  const btn = $("relay"), sheet = $("relay-sheet");
  if (!btn || !sheet) return {};
  const status = $("relay-status"), arm = $("relay-arm"), ask = $("relay-ask"), askOut = $("relay-answer"), hear = $("relay-hear"), close = $("relay-close");
  let checked = false;

  function open() {
    if (sheet.showModal) sheet.showModal(); else sheet.setAttribute("open", "");
    if (!checked) refresh();
  }
  function shut() { if (sheet.close) sheet.close(); else sheet.removeAttribute("open"); }
  async function refresh() {
    status.textContent = "checking the relay…";
    const s = await Demo.check();
    checked = true;
    bus.emit("relay:request", {});
    if (!s || !s.live) {
      status.textContent = "relay unavailable right now (" + ((s && s.reason) || "unreachable") + ") — the browser engine keeps working";
      arm.disabled = true; ask.disabled = true; hear.disabled = true;
      return;
    }
    const served = s.served_today != null ? " · " + s.served_today + " served today" : "";
    status.textContent = "live · " + (s.model || "xAI grok") + " · $" + (s.budget_usd != null ? s.budget_usd : "1") + "/day cap, shared by everyone" + served;
    arm.disabled = false; ask.disabled = false; hear.disabled = false;
  }
  arm.addEventListener("change", () => {
    Demo.want = arm.checked;
    Window.log(arm.checked ? "next dictation goes through xAI via the relay — opt-in, labeled" : "back to your browser's engine", "dim");
  });
  ask.addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = ask.querySelector("input").value.trim();
    if (!q) return;
    askOut.textContent = "asking…";
    AudioOut.unlock();
    try { askOut.textContent = await Demo.ask(q); bus.emit("relay:request", {}); }
    catch (err) { askOut.textContent = "relay: " + (err.message || "error"); }
  });
  hear.addEventListener("click", async () => {
    const text = askOut.textContent && !/^(asking…|relay:)/.test(askOut.textContent)
      ? askOut.textContent : "Your voice is the keyboard. The machine may draft. Only a hand may send.";
    AudioOut.unlock();
    hear.disabled = true;
    try { bus.emit("tts:start"); await AudioOut.play(await Demo.tts(text)); bus.emit("relay:request", {}); }
    catch (err) { Window.log("relay tts: " + (err.message || "error"), "err"); }
    finally { bus.emit("tts:end"); hear.disabled = false; }
  });
  btn.addEventListener("click", open);
  close.addEventListener("click", shut);
  sheet.addEventListener("click", (e) => { if (e.target === sheet) shut(); });
  return { open, shut };
})();
