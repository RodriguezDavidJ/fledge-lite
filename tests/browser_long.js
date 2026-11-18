// Longer run: let statistics history accumulate, inject a fault via the API, open a readings chart.
const { chromium } = require('playwright');
const out = process.argv[2] || '.';
const base = process.argv[3] || 'http://localhost:8000/';
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 }, deviceScaleFactor: 1 });
  page.on('pageerror', e => console.log('[pageerror]', e.message));
  await page.goto(base + '?setup=samples/pump_demo.json');
  await page.waitForSelector('#gui', { state: 'visible', timeout: 180000 });
  const api = (method, path, body) => page.evaluate(async ([m, p, b]) => {
    const r = await fetch(localStorage.getItem('SERVICE_URL') + p, { method: m, body: b ? JSON.stringify(b) : undefined });
    return r.status + ' ' + (await r.text()).slice(0, 200);
  }, [method, path, body]);
  await page.waitForTimeout(5000);
  console.log('fault on:', await api('PUT', 'category/Pump 01', { fault: 'true' }));
  await page.waitForTimeout(25000);
  console.log('latest:', await api('GET', 'asset/pump01/latest'));
  console.log('stats history:', await api('GET', 'statistics/history?minutes=5'));
  const gui = page.frameLocator('#gui');
  await page.evaluate(() => { document.getElementById('gui').contentWindow.location.hash = '#/asset'; });
  await page.waitForTimeout(3000);
  const row = gui.locator('tr', { hasText: 'pump01' });
  console.log('row html:', (await row.innerHTML()).replace(/\s+/g, ' ').slice(0, 700));
  await row.locator('[title*="hart"], [data-tooltip*="hart"], .bi-graph-up, .fa-chart-line').first().click({ timeout: 5000 })
    .catch(async () => row.locator('td').nth(2).locator('*').first().click({ timeout: 5000 }))
    .catch(e => console.log('click failed', e.message.split('\n')[0]));
  await page.waitForTimeout(5000);
  await page.screenshot({ path: `${out}/6-readings-chart.png` });
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
