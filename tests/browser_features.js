// Headless check of URL features: ?plugin=, Share setup round trip, ?gist= with the trust prompt.
const { chromium } = require('playwright');
const out = process.argv[2] || '.';
const base = process.argv[3] || 'http://localhost:8000/';
const fs = require('fs');
const path = require('path');
const tankSrc = fs.readFileSync(path.join(__dirname, '..', 'samples', 'plugins', 'tank.py'), 'utf8');

async function open(context, url) {
  const page = await context.newPage();
  page.on('pageerror', e => console.log('[pageerror]', e.message));
  await page.goto(url);
  return page;
}
const api = (page, method, p, body) => page.evaluate(async ([m, p, b]) => {
  const r = await fetch(localStorage.getItem('SERVICE_URL') + p, { method: m, body: b ? JSON.stringify(b) : undefined });
  return { status: r.status, body: await r.text() };
}, [method, p, body]);
const check = (name, ok, extra = '') => console.log(`${ok ? 'PASS' : 'FAIL'} ${name} ${extra}`);

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1400, height: 900 }, permissions: ['clipboard-read', 'clipboard-write'] });

  // 1. same-origin plugin + shorthand
  let page = await open(context, base + '?plugin=samples/plugins/tank.py&south=tank&rate=3&view=south');
  await page.waitForSelector('#gui', { state: 'visible', timeout: 180000 });
  await page.waitForTimeout(3000);
  let r = await api(page, 'GET', 'asset');
  check('?plugin= + ?south= gives tank01 readings', r.body.includes('tank01'), r.body);

  // change things, then share
  await api(page, 'PUT', 'category/tank', { valve: '65' });
  await api(page, 'POST', 'filter', { name: 'Level %', plugin: 'expression', filter_config: { enable: 'true', name: 'level_pct', expression: 'level / 5 * 100' } });
  r = await api(page, 'PUT', 'filter/tank/pipeline?allow_duplicates=true&append_filter=true', { pipeline: ['Level %'] });
  check('filter added through API', r.status === 200, r.body.slice(0, 120));
  await page.click('#share');
  await page.waitForFunction(() => document.getElementById('share-url').value.includes('#setup='), null, { timeout: 15000 });
  const link = await page.inputValue('#share-url');
  console.log('share link:', link.length, 'chars:', link.slice(0, 160) + '…');
  await page.screenshot({ path: `${out}/9-share.png` });
  await page.close();

  // 2. the share link recreates the setup in a fresh tab
  page = await open(context, link);
  await page.waitForSelector('#gui', { state: 'visible', timeout: 180000 });
  await page.waitForTimeout(4000);
  r = await api(page, 'GET', 'category/tank/valve');
  check('shared link restores tank valve = 65', /"value": "65/.test(r.body), r.body.slice(-60));
  r = await api(page, 'GET', 'category/tankAdvanced/readingsPerSec');
  check('shared link restores reading rate = 3', r.body.includes('"value": "3"'), r.body.slice(-40));
  r = await api(page, 'GET', 'asset/tank01/latest');
  check('shared link restores the Level % filter', r.body.includes('level_pct'), r.body.slice(0, 160));
  r = await api(page, 'GET', 'south');
  const names = JSON.parse(r.body).services.map(x => x.name);
  check('only one tank service (no duplicate from ?south=)', names.filter(n => n === 'tank').length === 1, JSON.stringify(names));
  await page.close();

  // 3. gist with a plugin from another origin: prompt, then run
  await context.route('https://api.github.com/gists/abc123', route => route.fulfill({
    contentType: 'application/json', headers: { 'Access-Control-Allow-Origin': '*' },
    body: JSON.stringify({ files: {
      'tank.py': { filename: 'tank.py', raw_url: 'https://gist.githubusercontent.com/someone/abc123/raw/tank.py', truncated: false, content: tankSrc.replace('"default": "tank"', '"default": "gisttank"') },
      'setup.json': { filename: 'setup.json', raw_url: 'https://gist.githubusercontent.com/someone/abc123/raw/setup.json', truncated: false,
        content: JSON.stringify([{ method: 'POST', path: '/fledge/service', body: { name: 'Tank from gist', type: 'south', plugin: 'gisttank', enabled: true } }]) },
    } }),
  }));
  page = await open(context, base + '?gist=abc123');
  await page.waitForSelector('#trust:not([hidden])', { timeout: 60000 });
  check('foreign plugin shows the trust prompt', true, await page.textContent('#trust-list'));
  await page.screenshot({ path: `${out}/10-trust.png` });
  await page.click('#trust-run');
  await page.waitForSelector('#gui', { state: 'visible', timeout: 180000 });
  await page.waitForTimeout(3000);
  r = await api(page, 'GET', 'south');
  check('gist setup + plugin ran', r.body.includes('Tank from gist') && r.body.includes('"running"'), r.body.slice(0, 160));
  await page.close();

  // 4. declining the prompt skips the plugin but still starts
  page = await open(context, base + '?gist=abc123');
  await page.waitForSelector('#trust:not([hidden])', { timeout: 60000 });
  await page.click('#trust-skip');
  await page.waitForSelector('#gui', { state: 'visible', timeout: 180000 });
  r = await api(page, 'GET', 'plugins/installed?type=south');
  check('skipping leaves the gist plugin uninstalled', !r.body.includes('gisttank'));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
