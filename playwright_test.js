#!/usr/bin/env node
/**
 * Playwright Dashboard Test — Structured output for Claude Code
 * Usage: node playwright_test.js
 */

const { chromium } = require('C:/Users/Ivy/AppData/Roaming/npm/node_modules/playwright');

const URL = 'http://127.0.0.1:8765';

async function runTests() {
  let browser;
  try {
    browser = await chromium.launch({ args: ['--no-sandbox', '--disable-setuid-sandbox'] });
    const page = await browser.newPage();

    const consoleErrors = [];
    const failedRequests = [];
    page.on('console', msg => { if (msg.type() === 'error') consoleErrors.push(msg.text()); });
    page.on('pageerror', err => consoleErrors.push('PAGE ERROR: ' + err.message));
    page.on('response', r => { if (r.status() >= 400) failedRequests.push(r.status() + ' ' + r.url()); });

    await page.goto(URL, { timeout: 10000 });
    await page.waitForTimeout(2000);

    const results = {};

    // ── 1. Dashboard load ──────────────────────────────
    results.dashboard = {
      title: await page.title(),
      loaded: true,
    };

    // ── 2. Stats bar elements ──────────────────────────
    const ssCount = await page.textContent('#ssCount').catch(() => null);
    const evtCount = await page.textContent('#evtCount').catch(() => null);
    const vlmStatus = await page.textContent('#vlmStatus').catch(() => null);
    const vlmAuto = await page.evaluate(() => {
      const r = document.getElementById('vlmStatus');
      return r ? r.textContent : null;
    });
    results.statsBar = { ssCount, evtCount, vlmStatus };

    // ── 3. Button states ──────────────────────────────
    const btnVlmAuto = await page.evaluate(() => {
      const b = document.getElementById('btnVlmAuto');
      return b ? { text: b.textContent.trim(), display: window.getComputedStyle(b).display } : null;
    });
    const btnVlmProcess = await page.evaluate(() => {
      const b = document.getElementById('btnVlmProcess');
      return b ? { text: b.textContent.trim(), display: window.getComputedStyle(b).display } : null;
    });
    results.buttons = { btnVlmAuto, btnVlmProcess };

    // ── 4. API status ─────────────────────────────────
    const status = await page.evaluate(async () => {
      const r = await fetch('/api/status');
      return r.json();
    });
    results.apiStatus = status;

    // ── 5. VLM toggle ─────────────────────────────────
    const beforeVlmAuto = status.vlm_auto;
    await page.click('#btnVlmAuto');
    await page.waitForTimeout(1000);
    const afterToggle = await page.evaluate(async () => {
      const r = await fetch('/api/status');
      return r.json();
    });
    results.vlmToggle = {
      before: beforeVlmAuto,
      after: afterToggle.vlm_auto,
      toggleWorked: beforeVlmAuto !== afterToggle.vlm_auto,
    };

    // Toggle back
    await page.click('#btnVlmAuto');
    await page.waitForTimeout(500);

    // ── 6. VLM process in manual mode ────────────────
    const procResp = await page.evaluate(async () => {
      const r = await fetch('/api/vlm-process', { method: 'POST' });
      const d = await r.json();
      return { status: r.status, body: d };
    });
    results.vlmProcess = procResp;

    // ── 7. Logs API ────────────────────────────────────
    const logsResp = await page.evaluate(async () => {
      const r = await fetch('/api/logs');
      const d = await r.json();
      return { status: r.status, count: d.logs ? d.logs.length : -1, sample: d.logs ? d.logs.slice(-3) : [] };
    });
    results.logsApi = logsResp;

    // ── 8. Tab navigation ────────────────────────────
    // Click LLM tab
    await page.click('#tab-llm');
    await page.waitForTimeout(500);
    const llmTabActive = await page.evaluate(() => document.getElementById('tab-llm').classList.contains('active'));

    // Click Log tab
    await page.click('#tab-log');
    await page.waitForTimeout(500);
    const logTabActive = await page.evaluate(() => document.getElementById('tab-log').classList.contains('active'));

    // Click Timeline tab
    await page.click('#tab-timeline');
    await page.waitForTimeout(500);
    const timelineTabActive = await page.evaluate(() => document.getElementById('tab-timeline').classList.contains('active'));

    results.tabs = { llmTabActive, logTabActive, timelineTabActive };

    // ── 9. Timeline events ─────────────────────────────
    const eventsResp = await page.evaluate(async () => {
      const r = await fetch('/api/events/today');
      return { status: r.status, count: (await r.json()).length };
    });
    results.timeline = eventsResp;

    // ── 10. Console errors & failed requests ─────────
    results.consoleErrors = consoleErrors;
    results.failedRequests = failedRequests;

    // ── Summary ────────────────────────────────────────
    results.summary = {
      ok: consoleErrors.length === 0 && failedRequests.length === 0,
      consoleErrorsCount: consoleErrors.length,
      failedRequestsCount: failedRequests.length,
    };

    console.log(JSON.stringify(results, null, 2));
    await browser.close();
    process.exit(0);

  } catch (err) {
    console.error(JSON.stringify({ fatal: err.message, stack: err.stack }));
    if (browser) await browser.close();
    process.exit(1);
  }
}

runTests();
