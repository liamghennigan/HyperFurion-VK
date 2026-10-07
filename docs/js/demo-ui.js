// ═══ DEMO UI — the hosted relay, as a visible opt-in ══════════════════════
// The page never touches the network on its own, and opening this sheet
// doesn't either: the sheet says what each control sends and where, and
// nothing leaves until a button in it is pressed. "check the relay" asks
// api.hyperfurion.com whether the hosted demo is up (one GET with nothing
// you said or typed in it); only a live answer unlocks the switch, the
// question box and the voice button. The switch sends ONE dictation and
// turns itself off. The footer counts each request this sheet sends
// (measure.js).
import { $ } from "./env.js";
import { bus } from "./bus.js";
import { Demo, AudioOut } from "./demo-relay.js";
import { Window } from "./window.js";

export const DemoUI = (() => {
  const btn = $("relay"), sheet = $("relay-sheet");
  if (!btn || !sheet) return {};
  const status = $("relay-status"), arm = $("relay-arm"), ask = $("relay-ask"), askOut = $("relay-answer"), hear = $("relay-hear"), close = $("relay-close");
  const askInput = ask ? ask.querySelector("input") : null, askBtn = ask ? ask.querySelector("button, [type=submit]") : null;
  // the one control that may talk to the relay before anything is unlocked
  const check = $("relay-check") || (() => {
    const b = document.createElement("button");
    b.type = "button"; b.id = "relay-check"; b.className = "btn small"; b.textContent = "check the relay";
    status.insertAdjacentElement("afterend", b);
    return b;
  })();
  if (askInput && /kai/i.test(askInput.placeholder)) {
    // it is not Kai: it is xAI's Grok, answering from a short fact sheet on the relay
    askInput.placeholder = "ask a question about HyperFurion VK…";
    askInput.setAttribute("aria-label", "Ask a question about HyperFurion VK (answered by xAI's Grok via the relay)");
  }
  if (hear && /real voice/i.test(hear.textContent)) hear.textContent = "hear it in xAI's voice";
  if (hear) hear.title = "Sends the answer above (or a sample sentence) to the relay, which has xAI's “eve” voice read it.";
  const askNote = "Your question goes to the relay and on to xAI's Grok, which answers from a short fact sheet about the app.";
  if (askOut && !askOut.textContent.trim()) askOut.textContent = askNote;
  let answer = "";

  function unlock(on) {
    if (arm) arm.disabled = !on;
    if (askInput) askInput.disabled = !on;   // the form's own disabled does nothing; its controls' does
    if (askBtn) askBtn.disabled = !on;
    if (hear) hear.disabled = !on;
  }
  function open() {
    if (sheet.showModal) sheet.showModal(); else sheet.setAttribute("open", "");
    if (!Demo.status) {
      status.textContent = "Not checked: nothing has been sent. “check the relay” asks api.hyperfurion.com whether the hosted demo is up today — one request, with nothing you said or typed in it (like any request, it shows the server your IP address).";
      unlock(false);
      check.focus();
    }
  }
  function shut() { if (sheet.close) sheet.close(); else sheet.removeAttribute("open"); }
  const plural = (k, one, many = one + "s") => k + " " + (k === 1 ? one : many);
  async function refresh() {
    status.textContent = "checking the relay…";
    check.disabled = true;
    bus.emit("relay:asked", { what: "status" });
    const s = await Demo.check();
    check.disabled = false;
    bus.emit("relay:request", {});
    if (!s || !s.live) {
      status.textContent = "The hosted demo is unavailable right now (" + ((s && s.reason) || "unreachable") +
        "). Nothing else was sent; the speech model in this tab keeps working.";
      unlock(false);
      return;
    }
    // only what the relay said: its served-today counts and its caps
    const t = s.served_today || {}, caps = s.caps || {};
    const served = [t.dictations, t.tts, t.asks].some((x) => typeof x === "number")
      ? " So far today it has served " + plural(t.dictations || 0, "dictation") + ", " + plural(t.tts || 0, "voice line") +
        " and " + plural(t.asks || 0, "question") + ", for everyone together." : "";
    const limits = [];
    if (caps.dictation_seconds) limits.push(Math.round(caps.dictation_seconds) + " s per dictation");
    if (caps.tts_chars) limits.push(caps.tts_chars + " characters per voice line");
    if (caps.ask_chars) limits.push(caps.ask_chars + " characters per question");
    const extra = [s.model ? "engine: " + s.model : "", s.budget_usd != null ? "daily budget: $" + s.budget_usd : ""].filter(Boolean);
    status.textContent = "Live." + served + (limits.length ? " Limits: " + limits.join(", ") + "." : "") +
      (extra.length ? " " + extra.join(" · ") + "." : "") +
      " To enforce a daily limit, the relay counts each IP address's requests per day; it keeps no audio and no text.";
    unlock(true);
  }
  check.addEventListener("click", refresh);
  if (arm) arm.addEventListener("change", () => {
    Demo.want = arm.checked;
    Window.log(arm.checked ? "your next dictation goes through xAI via the relay · one dictation, then this switches off" : "back to the speech model in this tab", "dim");
  });
  // one dictation per opt-in: the switch turns itself off as that dictation starts
  bus.on("rec:start", ({ engine } = {}) => {
    if (engine !== "relay") return;
    Demo.want = false;
    if (arm) arm.checked = false;
  });
  if (ask) ask.addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = askInput ? askInput.value.trim() : "";
    if (!q || (askInput && askInput.disabled)) return;
    askOut.textContent = "asking xAI's Grok via the relay…";
    AudioOut.unlock();
    bus.emit("relay:asked", { what: "ask" });
    try {
      answer = await Demo.ask(q);
      askOut.textContent = "Grok (xAI), from the relay's short fact sheet — it can be wrong: " + answer;
    } catch (err) { answer = ""; askOut.textContent = "relay: " + (err.message || "error"); }
    bus.emit("relay:request", {});
  });
  if (hear) hear.addEventListener("click", async () => {
    const text = answer || "This is xAI's eve voice, the one HyperFurion VK reads selected text aloud with by default.";
    AudioOut.unlock();
    hear.disabled = true;
    bus.emit("relay:asked", { what: "tts" });
    try { bus.emit("tts:start"); await AudioOut.play(await Demo.tts(text)); }
    catch (err) { Window.log("relay voice: " + (err.message || "error"), "err"); }
    finally { bus.emit("tts:end"); bus.emit("relay:request", {}); hear.disabled = !(Demo.status && Demo.status.live); }
  });
  btn.addEventListener("click", open);
  if (close) close.addEventListener("click", shut);
  sheet.addEventListener("click", (e) => { if (e.target === sheet) shut(); });
  return { open, shut };
})();
