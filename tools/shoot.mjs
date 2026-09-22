/* UI 확인용 스크린샷 도구.
 *
 * 이 환경(Linux arm64)에는 Google Chrome 이 없다 — Playwright 의 chrome 채널은
 * arm64 를 지원하지 않는다. 대신 번들 Chromium 을 직접 띄운다.
 *
 *   node tools/shoot.mjs http://localhost:8010 out/
 */
import { chromium } from 'playwright-core';
import { mkdir } from 'node:fs/promises';

const base = process.argv[2] ?? 'http://localhost:8010';
const outDir = process.argv[3] ?? 'tools/shots';
const runLens = process.argv.includes('--lens');

await mkdir(outDir, { recursive: true });

const browser = await chromium.launch();
const errors = [];

async function shoot(name, { width, height, theme, view }) {
  const ctx = await browser.newContext({
    viewport: { width, height },
    deviceScaleFactor: 2,
    colorScheme: theme,
    locale: 'ko-KR',
    timezoneId: 'Asia/Seoul',
  });
  const page = await ctx.newPage();
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(`[${name}] console: ${m.text()}`);
  });
  page.on('pageerror', (e) => errors.push(`[${name}] pageerror: ${e.message}`));

  await page.goto(base, { waitUntil: 'networkidle' });
  if (theme === 'dark') await page.evaluate(() => (document.documentElement.dataset.theme = 'dark'));

  if (view === 'lens') {
    await page.click('[data-view="lens"]');
    if (runLens) {
      await page.click('#run-lens');
      // Bedrock 왕복. 넉넉히 기다린다.
      await page.waitForSelector('.cluster', { timeout: 120_000 });
      await page.waitForTimeout(600);
    }
  }

  await page.screenshot({ path: `${outDir}/${name}.png`, fullPage: true });
  console.log(`  wrote ${outDir}/${name}.png`);
  await ctx.close();
}

await shoot('newsroom-light', { width: 1440, height: 1000, theme: 'light', view: 'newsroom' });
await shoot('newsroom-dark', { width: 1440, height: 1000, theme: 'dark', view: 'newsroom' });
await shoot('newsroom-mobile', { width: 390, height: 900, theme: 'light', view: 'newsroom' });
await shoot('lens-light', { width: 1440, height: 1200, theme: 'light', view: 'lens' });
if (runLens) {
  await shoot('lens-dark', { width: 1440, height: 1200, theme: 'dark', view: 'lens' });
  await shoot('lens-mobile', { width: 390, height: 1100, theme: 'light', view: 'lens' });
}

await browser.close();

if (errors.length) {
  console.error('\n브라우저 오류:');
  errors.forEach((e) => console.error('  ' + e));
  process.exit(1);
}
console.log('\n브라우저 콘솔 오류 없음');
