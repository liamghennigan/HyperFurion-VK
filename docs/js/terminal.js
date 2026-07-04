// ═══ TERMINAL — history, dictation line, and a real CLI ═══════════════════
import { tbody, synth, coarse, reduced } from "./env.js";
import { bus } from "./bus.js";
import { Config } from "./config.js";
import { AudioOut, Demo } from "./demo-relay.js";
import { Dictation } from "./dictation.js";
import { TTS } from "./tts.js";
import { Hints } from "./hints.js";
import { CHECKOUT, checkoutLive, live } from "./checkout.js";

export const Terminal = (() => {
  const history = [];
  let committed = "", interim = "";
  tbody.replaceChildren();
  const tlines = document.createElement("div");
  const tcur = document.createElement("div");
  tcur.className = "tline tcur";
  const prompt = document.createElement("span");
  prompt.className = "dim"; prompt.textContent = "$ ";
  const cSpan = document.createElement("span");
  const iSpan = document.createElement("span"); iSpan.className = "interim";
  const cli = document.createElement("input");
  cli.className = "cli"; cli.type = "text";
  cli.autocomplete = "off"; cli.autocapitalize = "off"; cli.spellcheck = false;
  cli.placeholder = "type a command — try --help";
  cli.setAttribute("aria-label", "terminal — type voice-keyboard commands, or dictate");
  const caret = document.createElement("span");
  caret.className = "caret"; caret.hidden = true;
  tcur.append(prompt, cSpan, iSpan, cli, caret);
  tbody.append(tlines, tcur);

  function render() {
    tlines.replaceChildren();
    for (const h of history.slice(-6)) {
      const d = document.createElement("div");
      d.className = "tline" + (h.cls ? " " + h.cls : "");
      d.textContent = h.text;
      tlines.appendChild(d);
    }
    cSpan.textContent = committed;
    iSpan.textContent = Config.cfg.interim ? interim : "";
  }
  function print(text, cls) { history.push({ text, cls }); render(); }
  function setLine(c, i) { committed = c; interim = i; render(); }
  function commitCurrent() {
    const text = (committed + interim).trim();
    committed = interim = "";
    if (text) history.push({ text: "$ " + text });
    render();
    return text;
  }
  function recMode(on) {
    tcur.classList.toggle("rec", on);
    caret.hidden = !on;
    cli.readOnly = on;
    if (on) { cli.value = ""; cli.blur(); }
  }

  // — the CLI —
  const HELP = [
    ["usage: voice-keyboard [command]", "dim"],
    ["  toggle (default) · start · stop · status · tts · version", "dim"],
    ["  page-only: help · clear · sponsor · subscribe", "dim"],
    ["  hosted demo (real xai): real · ask <q> · say <text> · demo", "dim"],
  ];
  function doAsk(q) {
    if (!q) { print("usage: ask <a question about the product>", "dim"); return; }
    const go = () => {
      print("thinking…", "dim");
      Demo.ask(q).then((a) => {
        a.split("\n").forEach((line) => { if (line.trim()) print(line.trim()); });
        print("· grok, via the hosted demo", "dim");
      }).catch((e) => print("hosted demo: " + e.message, "err"));
    };
    if (Demo.status && Demo.status.live) go();
    else Demo.check().then((st) => st.live ? go() :
      print("hosted demo offline (" + (st.reason || "unreachable") +
            ") — the README has answers: github.com/liamghennigan/HyperFurion-VK", "dim"));
  }
  function doSay(text) {
    const t = (text || "This is eve — the voice this keyboard ships with.").slice(0, 220);
    const browserVoice = () => {
      if (!synth) { print("no speech engine in this browser", "dim"); return; }
      synth.speak(new SpeechSynthesisUtterance(t));
      print("speaking with your browser's voice — type `real` first to hear the actual eve", "dim");
    };
    AudioOut.unlock();  // synchronously, while we're still inside the keystroke
    const go = () => Demo.tts(t).then((blob) => {
      print("▶ eve — xai grok tts, via the hosted demo", "dim");
      AudioOut.play(blob).catch(() =>
        print("(playback blocked — tap the page once and retry)", "dim"));
    }).catch((e) => { print("hosted demo: " + e.message, "err"); browserVoice(); });
    if (Demo.armed()) go();
    else if (Demo.want) Demo.check().then((st) => (st.live ? go() : browserVoice()));
    else browserVoice();
  }
  function run(raw) {
    const echoed = raw.trim();
    print("$ " + echoed);
    let cmd = echoed.replace(/^voice-keyboard\s*/, "").replace(/^vk\s+/, "").trim();
    if (echoed === "") return;
    if (cmd === "" ) cmd = "toggle";           // `voice-keyboard` alone toggles
    const verb = cmd.split(/\s+/)[0];
    const arg = cmd.slice(verb.length).trim();
    if (verb === "ask") { doAsk(arg); return; }
    if (verb === "say") { doSay(arg); return; }
    switch (cmd) {
      case "toggle": Dictation.toggle(); break;
      case "start": Dictation.start(); break;
      case "stop": Dictation.stop(); break;
      case "status": print(Dictation.recording ? "recording" : "idle", "dim"); break;
      case "tts": {
        const sel = getSelection();
        if (synth && sel && sel.toString().trim()) { TTS.speakSelection(); print("speaking selection…", "dim"); }
        else {
          print("tts reads the primary selection — select some text first, and keep it selected.", "dim");
          print(coarse
            ? "tip: on this page, select a sentence and tap the 🔊 chip that appears."
            : "tip: on this page, select a sentence and press ctrl+alt+t instead.", "dim");
        }
        break;
      }
      case "version": print("voice-keyboard 1.1.0", "dim"); break;
      case "sponsor": case "donate":
        print("free, MIT — if it earns its keystrokes:", "dim");
        print("https://github.com/sponsors/liamghennigan", "dim");
        break;
      case "subscribe":
        print("hosted tier — $5/mo: one hfk_ key, no provider accounts, hard quotas", "dim");
        print("convenience + supporting the project — you gain no abilities by paying;", "dim");
        print("everything is open source, free forever with your own key", "dim");
        if (checkoutLive) {
          if (live(CHECKOUT.basic)) print("$5/mo  basic — 20 h dictation + 250k chars: " + CHECKOUT.basic, "dim");
          if (live(CHECKOUT.pro)) print("$10/mo pro   — 60 h dictation + 1M chars:  " + CHECKOUT.pro, "dim");
          print("opening secure checkout on stripe…", "dim");
          const url = live(CHECKOUT.basic) ? CHECKOUT.basic : CHECKOUT.pro;
          window.open(url, "_blank", "noopener");
        } else {
          print("launching soon · early access via sponsors: github.com/sponsors/liamghennigan", "dim");
        }
        break;
      case "real": case "real on":
        Demo.want = true;
        print("checking the hosted demo…", "dim");
        Demo.check().then((st) => {
          if (st.live) {
            print("hosted demo live — the mic now streams to xai grok stt, the engine this product ships with", "dim");
            print("(talks to " + Demo.base.replace(/^https?:\/\//, "") +
                  " — nothing else on this page does · `real off` reverts)", "dim");
          } else {
            print("hosted demo offline (" + (st.reason || "unreachable") +
                  ") — your browser's engine stays in charge", "dim");
          }
          Hints.set();
        });
        break;
      case "real off":
        Demo.want = false;
        print("mic back on your browser's engine", "dim");
        break;
      case "demo": case "demo status":
        print("checking…", "dim");
        Demo.check().then((st) => {
          if (st.live) {
            const s = st.served_today || {};
            print("hosted demo: live · served today: " + (s.dictations | 0) + " dictations · " +
                  (s.tts | 0) + " voice lines · " + (s.asks | 0) + " questions", "dim");
            print("caps: " + st.caps.dictation_seconds + " s per dictation, budget-limited per day — real xai, honestly rationed", "dim");
          } else print("hosted demo: offline (" + (st.reason || "unreachable") + ")", "dim");
        });
        break;
      case "help": case "--help": case "-h": HELP.forEach(([t, c]) => print(t, c)); break;
      case "clear": history.length = 0; render(); break;
      default: print("vk: " + cmd.split(/\s/)[0] + ": command not found — try --help", "err");
    }
  }
  cli.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); const v = cli.value; cli.value = ""; run(v); }
  });
  tbody.addEventListener("click", () => {
    const sel = getSelection();
    if (!Dictation.recording && (!sel || sel.isCollapsed)) cli.focus({ preventScroll: true });
  });

  // boot: the status check types itself, like someone just ran it
  if (reduced) {
    print("$ voice-keyboard status");
    print("idle", "dim");
  } else {
    const bootCmd = "voice-keyboard status";
    let bi = 0;
    const bt = setInterval(() => {
      if (Dictation.recording) { clearInterval(bt); return; }  // the mic wins
      setLine(bootCmd.slice(0, ++bi), "");
      if (bi >= bootCmd.length) {
        clearInterval(bt);
        setTimeout(() => {
          if (Dictation.recording || committed !== bootCmd) return;
          commitCurrent();
          print("idle", "dim");
        }, 200);
      }
    }, 26);
  }
  render();
  bus.on("cfg:change", render);   // interim visibility follows the live config
  return { print, setLine, commitCurrent, recMode, render };
})();
