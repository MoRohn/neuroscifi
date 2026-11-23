// The phone setup page (pocket/setup_page.py, ADR-043). It shows the steps for this phone's system, offers the CA
// certificate while the Spark's setup window is open, and checks every few seconds whether this phone already trusts
// the Spark: a no-cors request to the Spark's HTTPS port succeeds only once the certificate is trusted. Nothing here
// handles a pairing code; that comes in the second QR code, straight to the HTTPS app.
const $ = (id) => document.getElementById(id);
const ua = navigator.userAgent;
const ios = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
const os = ios ? 'ios' : /Android/.test(ua) ? 'android' : 'other';
document.documentElement.dataset.os = os;
for (const el of document.querySelectorAll('[data-os]')) el.hidden = !el.dataset.os.split(' ').includes(os);

const info = await fetch('/setup.json', { cache: 'no-store' }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
const app = info ? `https://${location.hostname}:${info.https_port}/` : null;
if (!info?.open || !info?.certificate) {
  $('closed').hidden = false;
  $('download').setAttribute('aria-disabled', 'true');
  $('download').removeAttribute('href');
}

async function trusted() {
  if (!app) return false;
  try { await fetch(`${app}icon.svg`, { mode: 'no-cors', cache: 'no-store' }); return true; } catch { return false; }
}

let timer = 0;
async function check() {
  clearTimeout(timer);
  if (await trusted()) {
    $('step-check').dataset.trusted = 'true';
    $('trust').textContent = 'This phone trusts your Spark.';
    $('next').hidden = false;
    $('closed').hidden = true;
    for (const step of document.querySelectorAll('.step:not(.final)')) step.classList.add('done');
    return;
  }
  $('trust').textContent = 'Not yet trusted. Follow the steps above, then come back here.';
  if (!document.hidden) timer = setTimeout(check, 2500);
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) check(); });
check();
