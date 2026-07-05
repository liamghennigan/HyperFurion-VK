// ═══ CHECKOUT — Stripe payment links for the hosted tier ══════════════════
// The hosted subscription goes live the moment these two placeholders become
// real Stripe Payment Link URLs. Until then the page keeps saying "launching
// soon" and shows no buy button — editing these two lines is the only switch.
// Nothing here touches the network: a click just navigates to Stripe's own
// hosted checkout page.
//
// Create each link in Stripe (Payments → Payment Links), one per tier, with:
//   • metadata   tier = basic   (the $5 link)   /   tier = pro   (the $10 link)
//   • after-payment redirect:
//     https://api.hyperfurion.com/welcome?session_id={CHECKOUT_SESSION_ID}
// The deployed relay does the rest: /stripe/webhook issues the hfk_ key on
// checkout.session.completed, and /welcome shows it exactly once.
import { $ } from "./env.js";

// TEMPORARILY DISABLED — subscription paused, showing "coming soon".
// To re-enable: restore the two Stripe Payment Link URLs below (the live
// values are in git history at commit b562a86). checkoutLive flips to true
// automatically and every buy surface returns.
export const CHECKOUT = {
  basic: "coming-soon",
  pro: "coming-soon",
};

// A tier counts as live only once its placeholder becomes a real https URL.
export const live = (u) => /^https:\/\/\S+$/.test(u);
export const checkoutLive = live(CHECKOUT.basic) || live(CHECKOUT.pro);

// Reveal the buy buttons on the Hosted plan card and retire "launching soon".
// With JS off — or before the links are set — the static markup stands as-is.
const row = $("buy-row"), soon = $("hosted-soon");
if (row && checkoutLive) {
  for (const [tier, price] of [["basic", "$5"], ["pro", "$10"]]) {
    if (!live(CHECKOUT[tier])) continue;
    const a = document.createElement("a");
    a.className = "buybtn";
    a.href = CHECKOUT[tier];
    a.rel = "noopener";
    a.dataset.tier = tier;
    a.textContent = "Subscribe · " + price + "/mo";
    row.appendChild(a);
  }
  row.hidden = false;
  if (soon) soon.hidden = true;
}
