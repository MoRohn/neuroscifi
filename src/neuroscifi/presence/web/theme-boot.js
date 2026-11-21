// NeuroSciFi app theme, applied before first paint (classic script, no inline code: CSP script-src 'self').
// One preference for the console and Pocket: localStorage "nsf.theme" = "light" | "dark" | "system" (default).
// theme.mjs owns changes after load; this file only avoids a flash of the wrong theme.
(function () {
  var pref = null;
  try { pref = localStorage.getItem('nsf.theme'); } catch (e) { pref = null; }
  if (pref !== 'light' && pref !== 'dark') pref = 'system';
  var dark = pref === 'dark' || (pref === 'system' && !!window.matchMedia &&
    window.matchMedia('(prefers-color-scheme: dark)').matches);
  var root = document.documentElement;
  root.setAttribute('data-theme-pref', pref);
  root.setAttribute('data-theme', dark ? 'dark' : 'light');
}());
