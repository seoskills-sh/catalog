#!/usr/bin/env node
/**
 * Puppeteer JS Rendering Auditor — reference implementation.
 * Diffs raw HTML vs JavaScript-rendered DOM for each URL.
 * Output: JSON on stdout per ../references/output.schema.json.
 *
 * Usage: node render_audit.js --urls https://a.com,https://b.com [--viewport mobile]
 * Requires: puppeteer (or PUPPETEER_EXECUTABLE_PATH). Isolate per-URL failures.
 */
const puppeteer = require("puppeteer"); // if absent, launch fails -> NO_CHROMIUM

const MOBILE = { width: 390, height: 844, isMobile: true };
const DESKTOP = { width: 1366, height: 900, isMobile: false };

function extract(html) {
  const text = html.replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
  const links = new Set([...html.matchAll(/<a\b[^>]*href=["']([^"'#]+)["']/gi)].map((m) => m[1]));
  const grab = (re) => { const m = html.match(re); return m ? m[1].trim() : null; };
  return {
    words: text ? text.split(" ").length : 0,
    links,
    title: grab(/<title[^>]*>([\s\S]*?)<\/title>/i),
    description: grab(/<meta[^>]+name=["']description["'][^>]+content=["']([^"']*)["']/i),
    canonical: grab(/<link[^>]+rel=["']canonical["'][^>]+href=["']([^"']*)["']/i),
    robots: grab(/<meta[^>]+name=["']robots["'][^>]+content=["']([^"']*)["']/i),
    jsonld: (html.match(/application\/ld\+json/gi) || []).length,
  };
}

function diff(url, status, raw, ren, renderStatus) {
  const added = [...ren.links].filter((l) => !raw.links.has(l));
  const removed = [...raw.links].filter((l) => !ren.links.has(l));
  const metaChanged = {};
  for (const k of ["title", "description", "canonical", "robots"]) {
    if (raw[k] !== ren[k]) metaChanged[k] = { raw: raw[k], rendered: ren[k] };
  }
  const textDelta = ren.words - raw.words;
  const jsonldAdded = ren.jsonld - raw.jsonld;
  let severity = "none";
  if (textDelta > 100 || added.length > 10 || "canonical" in metaChanged || "robots" in metaChanged) severity = "high";
  else if (textDelta > 0 || added.length > 0 || jsonldAdded > 0 || Object.keys(metaChanged).length) severity = "medium";
  return {
    url, status, render_status: renderStatus, severity,
    text_delta_words: textDelta, links_added: added.slice(0, 50), links_removed: removed.slice(0, 50),
    meta_changed: metaChanged, jsonld_added: jsonldAdded,
    note: severity === "none" ? "no client-side rendering detected" : undefined,
  };
}

async function auditOne(browser, url, opts) {
  let status = 0;
  let raw;
  try {
    const res = await fetch(url, { redirect: "follow", headers: { "user-agent": "seoskills-render-auditor/1.0" } });
    status = res.status;
    if (status >= 400) return { url, status, error: "NON_200" };
    raw = extract(await res.text());
  } catch (e) {
    return { url, status, error: "RAW_FETCH_FAILED", message: String(e).slice(0, 200) };
  }
  const page = await browser.newPage();
  let renderStatus = "ok";
  try {
    await page.setViewport(opts.viewport === "desktop" ? DESKTOP : MOBILE);
    if (opts.block) {
      await page.setRequestInterception(true);
      page.on("request", (r) => (["image", "font", "media"].includes(r.resourceType()) ? r.abort() : r.continue()));
    }
    try {
      await page.goto(url, { waitUntil: opts.waitUntil, timeout: opts.timeout });
    } catch { renderStatus = "timeout"; }
    const ren = extract(await page.content());
    return diff(url, status, raw, ren, renderStatus);
  } finally {
    await page.close().catch(() => {});
  }
}

async function pool(items, size, fn) {
  const out = [];
  for (let i = 0; i < items.length; i += size) out.push(...await Promise.all(items.slice(i, i + size).map(fn)));
  return out;
}

(async () => {
  const args = Object.fromEntries(process.argv.slice(2).reduce((a, v, i, arr) => (v.startsWith("--") ? [...a, [v.slice(2), arr[i + 1]]] : a), []));
  const urls = (args.urls || "").split(",").map((s) => s.trim()).filter(Boolean);
  if (!urls.length) { console.log(JSON.stringify({ status: "error", error: { code: "NO_URLS" } })); process.exit(1); }
  const opts = { viewport: args.viewport || "mobile", waitUntil: args.wait_until || "networkidle2", timeout: Number(args.timeout || 15000), block: args.block !== "false" };
  let browser;
  try {
    browser = await puppeteer.launch({ headless: "new", args: ["--no-sandbox", "--disable-gpu"] });
  } catch (e) {
    console.log(JSON.stringify({ status: "error", error: { code: "NO_CHROMIUM", message: String(e).slice(0, 200) } })); process.exit(1);
  }
  try {
    const results = await pool(urls, 3, (u) => auditOne(browser, u, opts));
    console.log(JSON.stringify({ status: "ok", audited: results.length, results }, null, 2));
  } finally { await browser.close().catch(() => {}); }
})();
