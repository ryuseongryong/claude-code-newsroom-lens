import { chromium } from 'playwright-core';
const base = 'https://df4knlft34smf.cloudfront.net';
const browser = await chromium.launch();
async function shoot(name, { theme, view, width = 1280 }) {
  const ctx = await browser.newContext({
    viewport: { width, height: 900 }, deviceScaleFactor: 1,
    colorScheme: theme, locale: 'ko-KR', timezoneId: 'Asia/Seoul',
  });
  const page = await ctx.newPage();
  await page.goto(base, { waitUntil: 'networkidle' });
  if (theme === 'dark') await page.evaluate(() => (document.documentElement.dataset.theme = 'dark'));
  if (view === 'lens') {
    await page.click('[data-view="lens"]');
    await page.click('#run-lens');
    await page.waitForSelector('.cluster', { timeout: 150000 });
    await page.waitForTimeout(800);
    // 문서용으로는 첫 두 클러스터까지만 담는다.
    await page.evaluate(() => {
      document.querySelectorAll('#lens-clusters .cluster').forEach((el, i) => { if (i >= 2) el.remove(); });
    });
  } else {
    // 뉴스룸은 컬럼을 8건까지만 남겨 문서용으로 짧게.
    await page.evaluate(() => {
      document.querySelectorAll('.col-body').forEach((ul) =>
        [...ul.children].forEach((li, i) => { if (i >= 8) li.remove(); }));
    });
  }
  await page.screenshot({ path: `docs/img/${name}.png`, fullPage: true });
  console.log(`docs/img/${name}.png`);
  await ctx.close();
}
await shoot('lens', { theme: 'light', view: 'lens' });
await shoot('newsroom', { theme: 'light', view: 'newsroom' });
await shoot('lens-dark', { theme: 'dark', view: 'lens' });
await browser.close();
