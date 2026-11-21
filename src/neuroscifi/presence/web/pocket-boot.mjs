// Mounts the presence in Pocket's header. The pairing key stays in mobile.js (window.pocketToken); "pocketpair"
// fires when a pairing is made or dropped. Pocket sees only its own family's activity (presence/routes.py).
import { Presence, expose } from './presence.mjs';
import { PocketChannel } from './channel.mjs';

const host = document.getElementById('presence');
if (host) {
  const token = () => window.pocketToken?.() || null;
  const channel = new PocketChannel({ token });
  const presence = new Presence(host, { channel });
  expose(presence);
  // the small status indicator in the navigation (beside "Research"): the same engine and frame loop, body and eyes only
  const indicator = document.querySelector('[data-presence-indicator]');
  if (indicator) presence.addIndicator(indicator);
  document.addEventListener('pocketpair', () => { channel.stop(); channel.version = null; channel.start(); });
}
