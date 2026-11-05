import { test } from '@argo-video/cli';
import {
  createHumanCursor,
  demoType,
  zoomTo,
} from '@argo-video/cli';
import type { Page } from '@playwright/test';
import type { NarrationTimeline } from '@argo-video/cli';

const LEAD = { leadInMs: 100, leadOutMs: 180 };

let sceneStartedAt = 0;

async function waitScene(
  page: Page,
  narration: NarrationTimeline,
  scene: string,
  extraMs = 0,
): Promise<void> {
  const full = narration.sceneDuration(scene, LEAD);
  const elapsed = Date.now() - sceneStartedAt;
  const remaining = full - elapsed + extraMs;
  if (remaining > 0) await page.waitForTimeout(remaining);
}

/** Continuous in-page scroll so a held UI frame still has paint motion. */
async function glideScroll(page: Page, distance: number, durationMs: number): Promise<void> {
  await page.evaluate(
    ({ dy, ms }) =>
      new Promise<void>((resolve) => {
        const from = window.scrollY;
        const started = performance.now();
        const step = (now: number) => {
          const p = Math.min(1, (now - started) / ms);
          const eased = p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2;
          window.scrollTo(0, from + dy * eased);
          if (p < 1) requestAnimationFrame(step);
          else resolve();
        };
        requestAnimationFrame(step);
      }),
    { dy: distance, ms: durationMs },
  );
}

async function installHook(page: Page): Promise<void> {
  await page.evaluate(() => {
    document.getElementById('argo-hook')?.remove();
    const band = document.createElement('div');
    band.id = 'argo-hook';
    band.style.cssText = [
      'position:fixed',
      'left:0',
      'right:0',
      'bottom:0',
      'z-index:2147483646',
      'padding:22px 64px 24px',
      'background:#142235',
      'border-top:3px solid #2f6b5f',
      'box-shadow:0 -18px 60px rgba(20,34,53,0.22)',
      'font-family:ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif',
      'color:#f3f4f1',
      'opacity:0',
      'transition:opacity 220ms ease-out',
    ].join(';');
    band.innerHTML = `
      <div style="display:flex;align-items:center;gap:10px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:#9fc3b8">
        <span style="width:8px;height:8px;border-radius:50%;background:#4ade80;display:inline-block"></span>
        Forge Agent + MCP &middot; synthetic demo
      </div>
      <div style="margin-top:10px;font-size:46px;line-height:1.08;letter-spacing:-.025em;font-weight:650;max-width:1700px">
        A freight underwriting agent that cannot approve itself.
      </div>
      <div style="margin-top:9px;font-size:19px;line-height:1.35;color:#c8d6d1">
        Human approval &middot; scoped MCP tools &middot; replay-safe audit
      </div>`;
    document.body.appendChild(band);
    requestAnimationFrame(() => {
      band.style.opacity = '1';
    });
  });
  await page.waitForTimeout(280);
}

async function removeHook(page: Page): Promise<void> {
  await page.evaluate(() => {
    const band = document.getElementById('argo-hook');
    if (!band) return;
    band.style.opacity = '0';
    window.setTimeout(() => band.remove(), 280);
  });
  await page.waitForTimeout(320);
}

async function addStrip(
  page: Page,
  where: 'fields' | 'decision',
  text: string,
  tone: 'ok' | 'high' = 'ok',
): Promise<void> {
  await page.evaluate(({ target, label, tone }) => {
    document.querySelectorAll('.argo-strip').forEach((node) => node.remove());
    let host: Element | null = null;
    if (target === 'fields') {
      const heading = Array.from(document.querySelectorAll('h2')).find((node) =>
        node.textContent?.includes('Extracted fields and citations'),
      );
      host = heading?.closest('.card')?.querySelector('.card-body') ?? null;
    } else {
      host = document.querySelector('#decision .decision-body');
    }
    if (!host) return;

    const palette =
      tone === 'high'
        ? { fg: '#b54708', bg: '#fffaeb', border: '#fedf89' }
        : { fg: '#067647', bg: '#ecfdf3', border: '#abefc6' };

    const strip = document.createElement('div');
    strip.className = 'argo-strip';
    strip.style.cssText = [
      'display:flex',
      'align-items:center',
      'gap:8px',
      'margin:0 0 10px',
      'padding:6px 9px',
      'border:1px solid ' + palette.border,
      'background:' + palette.bg,
      'color:' + palette.fg,
      'border-radius:6px',
      'font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace',
      'font-size:10px',
      'font-weight:600',
      'letter-spacing:.12em',
      'line-height:1.2',
      'text-transform:uppercase',
    ].join(';');
    strip.innerHTML = '<span style="width:6px;height:6px;border-radius:50%;background:' + palette.fg + ';display:inline-block"></span>' + label;
    host.prepend(strip);
  }, { target: where, label: text, tone });
  await page.waitForTimeout(120);
}

function endCardHtml(): string {
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=1920, initial-scale=1" />
<style>
  :root {
    --bg: #f3f4f1;
    --surface: #fbfbf8;
    --ink: #171a1f;
    --ink-2: #3f4652;
    --muted: #69717d;
    --line: #d0d2ca;
    --green: #173f38;
    --green-2: #2f6b5f;
    --ok: #067647;
    --navy: #142235;
    --sans: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; width: 1920px; height: 1080px; overflow: hidden; background: var(--bg); }
  body { font-family: var(--sans); color: var(--ink); }
  .stage { position: relative; width: 100%; height: 100%; padding: 62px 84px 54px; display: flex; flex-direction: column; }
  .wash {
    position: absolute; inset: -18%;
    background:
      radial-gradient(46% 46% at 18% 24%, rgba(23,63,56,0.10) 0%, rgba(23,63,56,0) 68%),
      radial-gradient(40% 52% at 86% 78%, rgba(20,34,53,0.08) 0%, rgba(20,34,53,0) 70%),
      linear-gradient(115deg, rgba(255,255,255,0.55) 0%, rgba(255,255,255,0) 48%);
    animation: wash 16s ease-in-out infinite alternate;
    pointer-events: none;
  }
  @keyframes wash {
    0% { transform: translate3d(-1.4%, -0.8%, 0) scale(1.02); }
    100% { transform: translate3d(1.6%, 1.2%, 0) scale(1.06); }
  }
  header, section, footer { position: relative; z-index: 1; }
  header { display: flex; align-items: center; justify-content: space-between; padding-bottom: 18px; border-bottom: 1px solid var(--line); }
  .brand { display: flex; align-items: center; gap: 12px; }
  .mark { width: 38px; height: 38px; border-radius: 9px; background: var(--navy); color: #fff; display: grid; place-items: center; font: 700 17px/1 var(--mono); }
  .brand-name { font-size: 17px; font-weight: 650; letter-spacing: -0.01em; }
  .brand-sub { font-size: 12px; color: var(--muted); margin-top: 2px; }
  .tag { font-family: var(--mono); font-size: 12px; letter-spacing: .14em; text-transform: uppercase; color: var(--green); border: 1px solid #b9c8c2; background: rgba(255,255,255,0.62); border-radius: 99px; padding: 7px 12px; }
  .hero { padding-top: 54px; max-width: 1580px; }
  .kicker { display: flex; align-items: center; gap: 11px; font-family: var(--mono); font-size: 13px; font-weight: 600; letter-spacing: .15em; text-transform: uppercase; color: var(--green-2); }
  .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--ok); box-shadow: 0 0 0 0 rgba(6,118,71,0.35); animation: pulse 2.6s ease-out infinite; }
  @keyframes pulse {
    0% { box-shadow: 0 0 0 0 rgba(6,118,71,0.32); }
    70% { box-shadow: 0 0 0 14px rgba(6,118,71,0); }
    100% { box-shadow: 0 0 0 0 rgba(6,118,71,0); }
  }
  h1 { margin: 22px 0 0; font-size: 84px; line-height: 1.015; letter-spacing: -0.038em; font-weight: 650; max-width: 1500px; }
  h1 em { font-style: normal; color: var(--green); }
  .rule { height: 4px; width: 0; margin-top: 26px; background: var(--green); border-radius: 2px; animation: rule 2.4s cubic-bezier(.2,.7,.2,1) 0.35s forwards; }
  @keyframes rule { to { width: 760px; } }
  .sub { margin: 20px 0 0; font-size: 21px; line-height: 1.45; color: var(--ink-2); max-width: 1180px; }
  .flow { display: flex; align-items: center; gap: 12px; margin-top: 54px; flex-wrap: wrap; }
  .flow span { border: 1px solid #c9d2cd; background: rgba(255,255,255,0.62); border-radius: 999px; padding: 9px 14px; font-family: var(--mono); font-size: 13px; letter-spacing: .04em; color: var(--green); white-space: nowrap; }
  .flow i { font-style: normal; color: #9aa6a1; font-size: 13px; }
  .stats { margin-top: auto; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; }
  .stat { background: rgba(255,255,255,0.78); border: 1px solid var(--line); border-radius: 14px; padding: 22px 24px 20px; opacity: 0; transform: translateY(14px); animation: rise 700ms cubic-bezier(.2,.7,.2,1) forwards; }
  .stat:nth-child(1) { animation-delay: .5s; }
  .stat:nth-child(2) { animation-delay: .64s; }
  .stat:nth-child(3) { animation-delay: .78s; }
  .stat:nth-child(4) { animation-delay: .92s; }
  @keyframes rise { to { opacity: 1; transform: translateY(0); } }
  .stat b { display: block; font-size: 58px; line-height: 1; letter-spacing: -0.045em; font-weight: 650; color: var(--green); }
  .stat span { display: block; margin-top: 10px; font-size: 16px; line-height: 1.25; color: var(--ink-2); }
  footer { margin-top: 28px; display: grid; grid-template-columns: 1.05fr 1.35fr 0.9fr; align-items: end; gap: 24px; padding-top: 20px; border-top: 1px solid var(--line); }
  .evals { font-size: 19px; font-weight: 550; color: var(--ink); }
  .evals i { font-style: normal; color: var(--muted); padding: 0 6px; }
  .repo { font-family: var(--mono); font-size: 20px; color: var(--green); letter-spacing: -0.01em; }
  .caveat { justify-self: end; font-family: var(--mono); font-size: 13px; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); text-align: right; }
</style>
</head>
<body>
  <main class="stage">
    <div class="wash"></div>
    <header>
      <div class="brand">
        <div class="mark">N</div>
        <div>
          <div class="brand-name">Northwind Freight Capital</div>
          <div class="brand-sub">Underwriting operations &middot; synthetic tenant</div>
        </div>
      </div>
      <div class="tag">Synthetic corpus &middot; local demo</div>
    </header>
    <section class="hero">
      <div class="kicker"><span class="dot"></span> Forge Agent + MCP</div>
      <h1>A freight underwriting agent that <em>cannot approve itself.</em></h1>
      <div class="rule"></div>
      <p class="sub">Typed MCP tools, a deterministic rule engine and a human approval gate, with a replay-safe audit trail.</p>
      <div class="flow">
        <span>Packet</span><i>&rarr;</i>
        <span>Cited extraction</span><i>&rarr;</i>
        <span>Rule engine</span><i>&rarr;</i>
        <span>Human approval</span><i>&rarr;</i>
        <span>Audited write</span>
      </div>
    </section>
    <section class="stats">
      <div class="stat"><b>100%</b><span>hard-stop recall</span></div>
      <div class="stat"><b>0</b><span>duplicate writes</span></div>
      <div class="stat"><b>0</b><span>approval bypasses</span></div>
      <div class="stat"><b>100%</b><span>audit completeness</span></div>
    </section>
    <footer>
      <div class="evals">12 evaluation cases <i>&middot;</i> 4 held out <i>&middot;</i> six hard gates</div>
      <div class="repo">github.com/ArpanSM/forge-agent-mcp</div>
      <div class="caveat">Synthetic corpus &middot; local demo &middot; not production</div>
    </footer>
  </main>
</body>
</html>`;
}

test('agent-mcp', async ({ page, narration }) => {
  test.setTimeout(360_000);

  const cursor = await createHumanCursor(page, { seed: 'agent-mcp', size: 30 });
  try {
    await page.goto('/');
    await page.locator('#queue-panel').waitFor();
    await page.locator('a[href="/cases/case_onb_ridgeline#decision"]').waitFor({ timeout: 20_000 });
    await page.locator('a[href="/cases/case_inv_northgate#decision"]').waitFor({ timeout: 20_000 });
    await installHook(page);
    await narration.startRecording(page);

    // 1. Queue and hook
    sceneStartedAt = Date.now();
    narration.mark('queue');
    const queue = page.locator('#queue-panel');
    await zoomTo(page, queue, {
      narration,
      scale: 1.25,
      duration: 6_100,
      fadeIn: 6_100,
      holdMs: 1,
    });
    const ridgelineRow = page.locator('tr', {
      has: page.locator('a[href="/cases/case_onb_ridgeline"]'),
    }).first();
    const northgateRow = page.locator('tr', {
      has: page.locator('a[href="/cases/case_inv_northgate"]'),
    }).first();

    await cursor.moveTo(ridgelineRow, { durationMs: 350 });
    await page.waitForTimeout(300);
    await cursor.moveTo(northgateRow, { durationMs: 350 });
    await page.waitForTimeout(2300);
    await removeHook(page);
    await glideScroll(page, 34, 2_500);
    await waitScene(page, narration, 'queue');

    // 2. Open Ridgeline: documents, flags, recommendation
    await cursor.click(page.locator('a[href="/cases/case_onb_ridgeline"]'), { durationMs: 400 });
    await page.locator('h1', { hasText: 'Ridgeline Haulage LLC' }).waitFor({ timeout: 30_000 });
    await page.locator('section', { hasText: 'Extracted fields and citations' }).waitFor();

    sceneStartedAt = Date.now();
    narration.mark('ridgeline');
    await zoomTo(page, page.locator('section', { hasText: 'Documents in the packet' }), {
      narration,
      scale: 1.22,
      duration: 4_400,
      fadeIn: 4_400,
      holdMs: 1,
    });
    await cursor.moveTo(page.locator('section', { hasText: 'Documents in the packet' }), { durationMs: 350 });
    await page.waitForTimeout(200);
    await glideScroll(page, 180, 3_200);
    await cursor.moveTo(page.locator('.rec-card'), { durationMs: 350 });
    await waitScene(page, narration, 'ridgeline');

    // 3. Extracted fields and one citation row
    const legalNameRow = page.locator('tr', { hasText: 'Legal name' }).first();
    await legalNameRow.scrollIntoViewIfNeeded();
    await legalNameRow.evaluate((el) => el.scrollIntoView({ block: 'center', behavior: 'instant' }));
    await page.waitForTimeout(300);

    sceneStartedAt = Date.now();
    narration.mark('citations');
    await addStrip(page, 'fields', 'Cited evidence · page + bbox');
    await zoomTo(page, legalNameRow, {
      narration,
      scale: 1.55,
      duration: 5_100,
      fadeIn: 5_100,
      holdMs: 1,
    });
    const quoteCell = legalNameRow.locator('td').nth(4);
    await cursor.moveTo(quoteCell, { durationMs: 400 });
    await waitScene(page, narration, 'citations');

    // 4. Human decision: approve and commit
    const decision = page.locator('#decision');
    await decision.evaluate((el) => el.scrollIntoView({ block: 'center', behavior: 'instant' }));
    await page.waitForTimeout(350);

    sceneStartedAt = Date.now();
    narration.mark('approve');
    await addStrip(page, 'decision', 'Human decision');
    await zoomTo(page, decision, {
      narration,
      scale: 1.5,
      duration: 6_700,
      fadeIn: 6_700,
      holdMs: 1,
    });
    await cursor.click(page.locator('label.action-btn', {
      has: page.locator('input[name="action"][value="approve"]'),
    }), { durationMs: 400 });
    await demoType(page, '#note', 'Evidence checked against source documents. Approved.', 10);
    await page.waitForTimeout(250);
    await cursor.click(page.locator('#decision button[type="submit"]'), { durationMs: 400 });
    await page.getByText('Committed decision', { exact: false }).first().waitFor({ timeout: 30_000 });
    await page.waitForTimeout(600);
    await addStrip(page, 'decision', 'One transaction');
    await cursor.moveTo(page.locator('.commit-banner'), { durationMs: 350 });
    await waitScene(page, narration, 'approve');

    // 5. Audit trail: write_committed and chain
    const commitRow = page.locator('tr', { hasText: 'write_committed' }).first();
    await commitRow.waitFor({ timeout: 20_000 });
    await commitRow.evaluate((el) => el.scrollIntoView({ block: 'center', behavior: 'instant' }));
    await page.waitForTimeout(350);

    sceneStartedAt = Date.now();
    narration.mark('audit');
    await zoomTo(page, commitRow, {
      narration,
      scale: 1.42,
      duration: 4_600,
      fadeIn: 4_600,
      holdMs: 1,
    });
    await cursor.moveTo(commitRow.locator('td').nth(2), { durationMs: 350 });
    await page.waitForTimeout(450);
    await cursor.moveTo(commitRow.locator('td').nth(6), { durationMs: 350 });
    await waitScene(page, narration, 'audit');

    // 6. Northgate invoice: hold recommendation, request documents
    await page.goto('/cases/case_inv_northgate');
    await page.locator('h1', { hasText: 'Northgate Freightways LLC' }).waitFor({ timeout: 30_000 });
    await page.locator('section', { hasText: 'Risk flags' }).first().waitFor();

    sceneStartedAt = Date.now();
    narration.mark('northgate');
    await addStrip(page, 'decision', 'A hold cannot be approved', 'high');
    const flagsCard = page.locator('section', { hasText: 'Risk flags' }).first();
    await zoomTo(page, flagsCard, {
      narration,
      scale: 1.2,
      duration: 700,
      fadeIn: 700,
      holdMs: 1,
    });
    await cursor.moveTo(page.locator('.flag-item').filter({ hasText: 'POD' }).first(), { durationMs: 350 });
    await page.waitForTimeout(450);
    const northDecision = page.locator('#decision');
    await northDecision.evaluate((el) => el.scrollIntoView({ block: 'center', behavior: 'instant' }));
    await page.waitForTimeout(300);
    await zoomTo(page, northDecision, {
      narration,
      scale: 1.45,
      duration: 3_600,
      fadeIn: 3_600,
      holdMs: 1,
    });
    await page.locator('label.action-btn', {
      has: page.locator('input[name="action"][value="request_documents"]'),
    }).click();
    await page.locator('#note').fill('Corrected rate confirmation and POD requested.');
    await page.waitForTimeout(200);
    await cursor.click(page.locator('#decision button[type="submit"]'), { durationMs: 400 });
    await page.getByText('Awaiting documents', { exact: false }).first().waitFor({ timeout: 30_000 });
    await page.waitForTimeout(500);
    await cursor.moveTo(page.locator('.commit-banner'), { durationMs: 350 });
    await waitScene(page, narration, 'northgate');

    // 7. End card — painted as a full-frame iframe over the app, so there is
    // no blank navigation frame between Northgate and the close.
    await cursor.dispose();
    await page.evaluate(async (html) => {
      document.getElementById('argo-endcard')?.remove();
      const frame = document.createElement('iframe');
      frame.id = 'argo-endcard';
      frame.setAttribute('title', 'Forge Agent + MCP end card');
      frame.style.cssText =
        'position:fixed;inset:0;width:100vw;height:100vh;border:0;z-index:2147483647;background:#f3f4f1;opacity:0;';
      frame.srcdoc = html;
      const loaded = new Promise<void>((resolve) => {
        frame.addEventListener('load', () => resolve(), { once: true });
      });
      document.body.appendChild(frame);
      await loaded;
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      frame.style.opacity = '1';
    }, endCardHtml());
    await page.waitForTimeout(700);
    sceneStartedAt = Date.now();
    narration.mark('close');
    await zoomTo(page, '#argo-endcard', {
      narration,
      scale: 1.08,
      duration: 6_000,
      fadeIn: 6_000,
      holdMs: 1,
    });
    await waitScene(page, narration, 'close');
  } finally {
    await cursor.dispose();
  }
});
