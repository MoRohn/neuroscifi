// Mounts the presence in the owner console header. The session token stays in console.js; it exposes a getter
// (window.nsfConsoleToken) and fires "nsf:session" when a token arrives or is dropped.
import { Presence, expose } from './presence.mjs';
import { ConsoleChannel } from './channel.mjs';

const host = document.getElementById('presence');
if (host) {
  const token = () => window.nsfConsoleToken?.() ?? null;
  const channel = new ConsoleChannel({ token });
  const presence = new Presence(host, { channel, modelDoodles: true });
  expose(presence);
  addEventListener('nsf:session', () => { if (token()) { channel.stop(); channel.start(); } else presence.local.connection = 'locked'; });
}
