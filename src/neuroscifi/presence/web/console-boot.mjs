// Mounts the presence in the owner console header (and moves it to the research/voice view dock while one is open). The session token stays in console.js; it exposes a getter
// (window.nsfConsoleToken) and fires "nsf:session" when a token arrives or is dropped.
import { Presence, expose } from './presence.mjs';
import { ConsoleChannel } from './channel.mjs';

const host = document.getElementById('presence');
if (host) {
  const token = () => window.nsfConsoleToken?.() ?? null;
  const channel = new ConsoleChannel({ token });
  const presence = new Presence(host, { channel, modelDoodles: true });
  expose(presence);
  // The research and voice views carry a normal-size dock (data-presence-dock): while one of them is the current view
  // the presence sits there, beside the conversation; on every other view it is back in the header (compact).
  const docks = [...document.querySelectorAll('[data-presence-dock]')];
  const place = () => {
    const view = document.documentElement.dataset.view;
    presence.mount(docks.find((d) => d.closest('.view')?.id === view) ?? host);
  };
  new MutationObserver(place).observe(document.documentElement, { attributes: true, attributeFilter: ['data-view'] });
  place();
  addEventListener('nsf:session', () => { if (token()) { channel.stop(); channel.start(); } else presence.local.connection = 'locked'; });
}
