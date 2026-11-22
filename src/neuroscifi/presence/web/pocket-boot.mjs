// Mounts the presence on Pocket's interaction stage (or in its header). The pairing key stays in mobile.js (window.pocketToken); "pocketpair"
// fires when a pairing is made or dropped. Pocket sees only its own family's activity (presence/routes.py).
import { Presence, expose } from './presence.mjs';
import { PocketChannel } from './channel.mjs';

const host = document.getElementById('presence');
if (host) {
  const token = () => window.pocketToken?.() || null;
  const channel = new PocketChannel({ token });
  const presence = new Presence(host, { channel });
  expose(presence);
  // The interaction stage (docs/ui/DESIGN_SYSTEM.md): the entity is the hero above Pocket's questions and sessions; the
  // header keeps the brand. Without a stage (an older page) it stays in the header.
  const stage = document.querySelector('[data-presence-dock]');
  if (stage) presence.mount(stage);
  // the small status indicator in the navigation (beside "Research"): the same engine and frame loop, body and eyes only
  const indicator = document.querySelector('[data-presence-indicator]');
  if (indicator) presence.addIndicator(indicator);
  document.addEventListener('pocketpair', () => { channel.stop(); channel.version = null; channel.start(); });
}
