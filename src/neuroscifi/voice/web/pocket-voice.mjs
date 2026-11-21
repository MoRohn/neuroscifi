// Pocket: show the Voice panel to a paired device when its Spark offers voice (the bridge has the individual's
// database). The microphone needs a secure context: Pocket over the LAN is HTTPS only (the bridge refuses LAN
// without a certificate); on plain http the panel explains that capture is unavailable and text still works.
import { mountPocket } from "./voice-ui.mjs";

const section = document.getElementById("voice");
const nav = document.getElementById("navvoice");
let mounted = null;

async function available() {
  const token = window.pocketToken?.();
  if (!token) return false;
  const res = await fetch("/api/voice_status", { method: "POST", cache: "no-store", credentials: "omit",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ probe: false }) }).catch(() => null);
  // unreachable: tell the pairing (mobile.js re-checks with backoff and re-announces the pairing when it answers)
  if (!res) { if (window.pocketToken?.() === token) window.pocketReach?.(false); return false; }
  if (!res.ok) return false;
  const body = await res.json().catch(() => ({}));
  return body.voice !== false;
}

document.addEventListener("pocketpair", async (e) => {
  if (!section || !nav) return;
  const on = e.detail.paired && await available();
  section.hidden = nav.hidden = !on;
  if (!on) {
    if (mounted) { await mounted.client.stop().catch(() => {}); mounted.stop(); mounted = null; }
    return;
  }
  if (!mounted) {
    mounted = mountPocket(document.getElementById("voicemount"), () => window.pocketToken?.() || "");
    if (!window.isSecureContext) {
      document.getElementById("voicehelp").textContent =
        "This page is not on HTTPS, so the browser does not allow the microphone. Typing works.";
    }
  }
});
