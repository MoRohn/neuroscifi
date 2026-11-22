// Theme adapter: the ONE app theme system shared by the console and Pocket (docs/presence/THEME_INTEGRATION.md).
// Preference light | dark | system, persisted in localStorage "nsf.theme", applied as <html data-theme> (resolved)
// and data-theme-pref. Colours live in theme.css as CSS custom properties, so a switch changes only variables:
// no element is re-created, no animation restarts and the presence state is untouched.

export const THEME_KEY = 'nsf.theme';
export const THEME_PREFS = Object.freeze(['system', 'light', 'dark']);

// The palette (spec): light and dark tokens; dark accents are contrast-adjusted (slightly lighter, no neon).
export const TOKENS = Object.freeze({
  light: Object.freeze({ bg: '#FFFFFF', ink: '#101010', secondary: '#666666', purple: '#B45CFF', blue: '#4A9CFF',
    mint: '#3DDDB2', yellow: '#FFC928', pink: '#F45CA7' }),
  dark: Object.freeze({ bg: '#050505', ink: '#F7F7F7', secondary: '#A8A8A8', purple: '#C27DFF', blue: '#6BAFFF',
    mint: '#52E3BE', yellow: '#FFD24A', pink: '#F77DB9' }),
});

const media = typeof matchMedia === 'function' ? matchMedia('(prefers-color-scheme: dark)') : null;
const listeners = new Set();

function read() {
  try {
    const value = localStorage.getItem(THEME_KEY);
    return THEME_PREFS.includes(value) ? value : 'system';
  } catch { return 'system'; }
}

export function themePref() { return document.documentElement.getAttribute('data-theme-pref') || read(); }
export function resolve(pref = themePref()) {
  return pref === 'dark' || (pref === 'system' && media?.matches) ? 'dark' : 'light';
}
export function currentTheme() { return document.documentElement.getAttribute('data-theme') || resolve(); }

function apply(pref) {
  const root = document.documentElement;
  const theme = resolve(pref);
  const changed = root.getAttribute('data-theme') !== theme || root.getAttribute('data-theme-pref') !== pref;
  root.setAttribute('data-theme-pref', pref);
  root.setAttribute('data-theme', theme);
  chrome(theme);
  if (changed) for (const fn of listeners) fn(theme, pref);
}

// The browser chrome follows the app theme (same values as theme-boot.js and ui.css --bg).
export const CHROME = Object.freeze({ light: '#F6F4F3', dark: '#121011' });
function chrome(theme) {
  document.querySelector('meta[name="color-scheme"]')?.setAttribute('content', theme);
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', CHROME[theme]);
}

export function setThemePref(pref) {
  if (!THEME_PREFS.includes(pref)) throw new Error('theme must be system, light or dark');
  try { localStorage.setItem(THEME_KEY, pref); } catch { /* private mode: still applied for this page */ }
  apply(pref);
}

export function onThemeChange(fn) { listeners.add(fn); return () => listeners.delete(fn); }

let wired = false;
export function initTheme() {
  if (wired) return;
  wired = true;
  apply(read());
  media?.addEventListener?.('change', () => apply(themePref()));
  addEventListener('storage', (event) => { if (event.key === THEME_KEY) apply(read()); });  // other tabs
}

// WCAG relative luminance and contrast ratio, used by the geometry validator's contrast rule.
function channel(c) { const v = c / 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }
export function luminance(hex) {
  const n = parseInt(hex.slice(1), 16);
  return 0.2126 * channel((n >> 16) & 255) + 0.7152 * channel((n >> 8) & 255) + 0.0722 * channel(n & 255);
}
export function contrast(a, b) {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}
