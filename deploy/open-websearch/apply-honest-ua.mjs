#!/usr/bin/env node
// Replace the browser User-Agent strings that open-webSearch 2.2.0 sends with the Web Cortex's own, honest one.
//
// open-webSearch presents itself to search engines as desktop Chrome (and in one place as the Apifox client).
// NeuroSciFi does not impersonate browsers (docs/web-cortex/SECURITY_MODEL.md), so the pinned build is patched:
//   node apply-honest-ua.mjs [packageDir]        (default: this script's directory; `npm run patch-ua`)
// - refuses any version but PINNED_VERSION, and refuses when the four engines the Web Cortex uses no longer
//   contain the expected forged strings (the upstream code moved: review before patching again);
// - replaces every quoted "Mozilla/5.0 (...)" and Apifox User-Agent literal under build/ with USER_AGENT;
// - writes MARKER (node_modules/open-websearch/.neuroscifi-honest-ua.json) with the sha256 of each patched file.
//   providers/openwebsearch.py trusts the build only while those hashes match; `npm ci` removes the marker.
// Idempotent: a build whose marker still matches is left alone.
// Engines may answer an honest User-Agent differently (or refuse it); that is accepted, not worked around.
import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const USER_AGENT = "NeuroSciFi-WebCortex/0.7 (research agent; honours robots.txt)";
export const PINNED_VERSION = "2.2.0";
export const MARKER = ".neuroscifi-honest-ua.json";
// engine file -> forged strings it must contain at the pinned version (all are replaced)
export const TARGETS = {
  "build/engines/duckduckgo/searchDuckDuckGo.js": [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Safari/537.36",
    "Apifox/1.0.0 (https://apifox.com)",
  ],
  "build/engines/startpage/startpage.js": [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
  ],
  "build/engines/brave/brave.js": [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Safari/537.36",
  ],
  "build/engines/bing/bing.js": [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
  ],
};
const FORGED = /(['"`])(?:Mozilla\/5\.0 \([^'"`\n]*|Apifox\/1\.0\.0 \(https:\/\/apifox\.com\))\1/g;

const sha256 = (data) => createHash("sha256").update(data).digest("hex");

function jsFiles(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) out.push(...jsFiles(path));
    else if (name.endsWith(".js")) out.push(path);
  }
  return out.sort();
}

function markerValid(pkg) {
  const markerPath = join(pkg, MARKER);
  if (!existsSync(markerPath)) return false;
  try {
    const marker = JSON.parse(readFileSync(markerPath, "utf8"));
    if (marker.version !== PINNED_VERSION || marker.user_agent !== USER_AGENT) return false;
    if (!Object.keys(TARGETS).every((rel) => rel in marker.files)) return false;
    return Object.entries(marker.files).every(([rel, digest]) =>
      existsSync(join(pkg, rel)) && sha256(readFileSync(join(pkg, rel))) === digest);
  } catch {
    return false;
  }
}

export function apply(packageDir) {
  const pkg = join(resolve(packageDir), "node_modules", "open-websearch");
  const version = JSON.parse(readFileSync(join(pkg, "package.json"), "utf8")).version;
  if (version !== PINNED_VERSION) {
    throw new Error(`open-websearch ${version} installed; this patch is reviewed for ${PINNED_VERSION} only`);
  }
  if (markerValid(pkg)) return { changed: false, marker: join(pkg, MARKER) };
  for (const [rel, needles] of Object.entries(TARGETS)) {
    const text = readFileSync(join(pkg, rel), "utf8");
    const missing = needles.filter((needle) => !text.includes(needle));
    if (missing.length) {
      throw new Error(`${rel}: expected User-Agent string not found (${missing[0].slice(0, 60)}...); ` +
                      "the engine code differs from the reviewed 2.2.0 build");
    }
  }
  const files = {};
  for (const path of jsFiles(join(pkg, "build"))) {
    const text = readFileSync(path, "utf8");
    const patched = text.replace(FORGED, (_match, quote) => `${quote}${USER_AGENT}${quote}`);
    const rel = relative(pkg, path).split("\\").join("/");
    if (patched !== text) writeFileSync(path, patched);
    if (patched !== text || rel in TARGETS) files[rel] = sha256(patched);
  }
  const leftover = new RegExp(FORGED.source);
  for (const rel of Object.keys(TARGETS)) {
    if (leftover.test(readFileSync(join(pkg, rel), "utf8"))) throw new Error(`${rel}: forged User-Agent left`);
  }
  writeFileSync(join(pkg, MARKER), JSON.stringify({ version, user_agent: USER_AGENT, files }, null, 1) + "\n");
  return { changed: true, marker: join(pkg, MARKER), files: Object.keys(files) };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const result = apply(process.argv[2] || dirname(fileURLToPath(import.meta.url)));
    console.log(result.changed ? `patched ${result.files.length} files; marker ${result.marker}`
                               : `already patched; marker ${result.marker}`);
  } catch (error) {
    console.error(`apply-honest-ua: ${error.message}`);
    process.exit(1);
  }
}
