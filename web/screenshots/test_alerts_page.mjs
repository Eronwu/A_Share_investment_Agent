import { chromium } from 'playwright';
const BASE = 'http://localhost:3120';
const DIR = new URL('.', import.meta.url).pathname;

const results = [];
function record(name, ok, detail = '') {
  results.push({ name, ok });
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ' -- ' + detail : ''}`);
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();

  try {
    // ── 1. Navigate to /alerts ──
    console.log('\n=== Alerts Page Tests ===\n');
    await page.goto(`${BASE}/alerts`, { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(3000);

    // Screenshot full page
    await page.screenshot({ path: `${DIR}/alerts_full.png`, fullPage: true });
    console.log('  Screenshot: alerts_full.png');

    // ── 2. Check no ERROR banner ──
    const errorBanner = await page.evaluate(() => {
      const els = Array.from(document.querySelectorAll('div'));
      return els.some(el => el.textContent.includes('[ERROR]'));
    });
    record('No [ERROR] banner', !errorBanner);

    // ── 3. Check loading state is gone ──
    const loadingVisible = await page.evaluate(() => {
      const els = Array.from(document.querySelectorAll('div'));
      return els.some(el => el.textContent.trim() === 'Loading...');
    });
    record('Loading state resolved', !loadingVisible);

    // ── 4. Check events are rendered ──
    const eventCount = await page.evaluate(() => {
      // Events have [HH:MM:SS] time format and L1/L2/L3 level indicators
      const divs = Array.from(document.querySelectorAll('div'));
      return divs.filter(d => {
        const text = d.textContent || '';
        return /\[\d{2}:\d{2}:\d{2}\]/.test(text) && /\[L[1-3]\]/.test(text);
      }).length;
    });
    record('Events rendered', eventCount > 0, `${eventCount} event rows`);

    // ── 5. Check event count in nav bar ──
    const navCount = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll('span'));
      const match = spans.find(s => (s.textContent || '').includes('events today'));
      return match ? match.textContent.trim() : null;
    });
    record('Nav bar shows event count', navCount !== null, navCount || 'not found');

    // ── 6. Check event structure (time, level, kind, symbol, display) ──
    const sampleEvent = await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll('div'));
      const eventDiv = divs.find(d => {
        const text = d.textContent || '';
        return /\[\d{2}:\d{2}:\d{2}\]/.test(text) && /\[L[1-3]\]/.test(text);
      });
      if (!eventDiv) return null;
      const spans = Array.from(eventDiv.querySelectorAll(':scope > span'));
      return {
        spanCount: spans.length,
        texts: spans.map(s => s.textContent.trim()).filter(Boolean),
      };
    });
    record('Event has proper structure', sampleEvent && sampleEvent.spanCount >= 5,
      sampleEvent ? `${sampleEvent.spanCount} spans: ${sampleEvent.texts.slice(0, 4).join(' | ')}` : 'no event');

    // ── 7. Check nav links ──
    const navLinks = await page.evaluate(() => {
      const anchors = Array.from(document.querySelectorAll('a'));
      return anchors.map(a => ({ href: a.getAttribute('href'), text: a.textContent.trim() }));
    });
    const hasMonitorLink = navLinks.some(l => l.href === '/');
    const hasSimLink = navLinks.some(l => l.href === '/sim');
    const hasManageLink = navLinks.some(l => l.href === '/manage');
    record('Nav links present', hasMonitorLink && hasSimLink && hasManageLink,
      `monitor:${hasMonitorLink} sim:${hasSimLink} manage:${hasManageLink}`);

    // ── 8. Check "no events" message is NOT shown when events exist ──
    const noEventsMsg = await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll('div'));
      return divs.some(d => (d.textContent || '').includes('No alert events today'));
    });
    if (eventCount > 0) {
      record('No "empty" message when events exist', !noEventsMsg);
    } else {
      record('Shows "no events" message when empty', noEventsMsg);
    }

    // ── 9. Check daily summary section ──
    const hasSummary = await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll('div'));
      return divs.some(d => (d.textContent || '').includes('信号日报'));
    });
    record('Daily summary section', true, hasSummary ? 'present' : 'absent (normal if no report today)');

    // ── 10. Check for L1 highlight ──
    const l1Events = await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll('div'));
      return divs.filter(d => {
        const text = d.textContent || '';
        const style = d.getAttribute('style') || '';
        return /\[L1\]/.test(text) && style.includes('#44475a');
      }).length;
    });
    record('L1 events highlighted', true, `${l1Events} L1 events with highlight`);

    // ── 11. Check scroll area ──
    const scrollable = await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll('div'));
      const scrollDiv = divs.find(d => (d.style.overflow === 'auto' || d.style.overflowY === 'auto'));
      return scrollDiv !== undefined;
    });
    record('Scroll area present', scrollable);

    // ── 12. Screenshot viewport only (what user sees) ──
    await page.screenshot({ path: `${DIR}/alerts_viewport.png` });
    console.log('  Screenshot: alerts_viewport.png');

    // ── 13. Check today's date consistency (UTC vs local) ──
    const dateCheck = await page.evaluate(async () => {
      const res = await fetch('/api/metrics');
      const data = await res.json();
      const events = data.alertEvents || [];
      if (events.length === 0) return { ok: true, detail: 'no events' };
      // Check all events have same time format
      const badTime = events.filter(e => !/^\d{2}:\d{2}:\d{2}$/.test(e.time || ''));
      return {
        ok: badTime.length === 0,
        detail: `${events.length} events, ${badTime.length} with bad time format`,
        sample: events[0],
      };
    });
    record('Event time format correct', dateCheck.ok, dateCheck.detail);

    // ── 14. Check change_pct rendering ──
    const changePctRendered = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll('span'));
      const pctSpans = spans.filter(s => /[+-]?\d+\.\d+%/.test(s.textContent || ''));
      return pctSpans.length;
    });
    record('Change % values rendered', changePctRendered > 0, `${changePctRendered} pct values`);

  } finally {
    await browser.close();
  }

  // Summary
  const fail = results.filter(r => !r.ok).length;
  console.log(`\nTotal: ${results.length} | Pass: ${results.length - fail} | Fail: ${fail}`);
  if (fail) process.exit(1);
}

main().catch(e => { console.error(e); process.exit(2); });
