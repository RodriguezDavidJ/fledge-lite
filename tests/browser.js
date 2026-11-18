// Headless browser check: load fledge-lite with a setup URL, wait for the GUI, screenshot pages.
const { chromium } = require('playwright');
const out = process.argv[2] || '.';
const base = process.argv[3] || 'http://localhost:8000/';
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 }, deviceScaleFactor: 1 });
  const t0 = Date.now();
  page.on('console', m => console.log(`[${((Date.now()-t0)/1000).toFixed(1)}s console.${m.type()}]`, m.text().slice(0, 300)));
  page.on('pageerror', e => console.log('[pageerror]', e.message));
  page.on('worker', w => { console.log('[worker started]', w.url()); });
  const timer = setInterval(async () => {
    try { console.log(`[${((Date.now()-t0)/1000).toFixed(0)}s status]`, await page.textContent('#status'), '|', await page.textContent('#error')); } catch (e) {}
  }, 10000);
  await page.goto(base + '?setup=' + (process.argv[4] || 'samples/pump_demo.json'));
  await page.waitForSelector('#gui', { state: 'visible', timeout: 180000 });
  clearInterval(timer);
  console.log(`GUI iframe shown after ${((Date.now()-t0)/1000).toFixed(1)}s`);
  const frame = page.frameLocator('#gui');
  await page.waitForTimeout(8000);
  await page.screenshot({ path: `${out}/1-dashboard.png` });
  const apiCheck = await page.evaluate(async () => {
    const r = await fetch(localStorage.getItem('SERVICE_URL') + 'south');
    return r.status + ' ' + (await r.text()).slice(0, 400);
  });
  console.log('API via service worker:', apiCheck);
  for (const [name, hash] of [['2-south', 'south'], ['3-assets', 'asset'], ['4-audit', 'logs/audit']]) {
    await page.evaluate(h => { document.getElementById('gui').contentWindow.location.hash = '#/' + h; }, hash);
    await page.waitForTimeout(4000);
    await page.screenshot({ path: `${out}/${name}.png` });
  }
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
