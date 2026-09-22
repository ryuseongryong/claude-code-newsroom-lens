import { chromium } from 'playwright-core';
const browser = await chromium.launch();
const ctx = await browser.newContext({
  viewport: { width: 1440, height: 1000 }, locale: 'ko-KR',
  permissions: ['clipboard-read', 'clipboard-write'],
});
const page = await ctx.newPage();
const errs = [];
page.on('pageerror', (e) => errs.push('pageerror: ' + e.message));
page.on('console', (m) => { if (m.type() === 'error') errs.push('console: ' + m.text()); });

await page.goto('http://localhost:8010', { waitUntil: 'networkidle' });

// ── 1. 열 수가 소스 수를 따라가는가
const cols = await page.evaluate(() =>
  getComputedStyle(document.documentElement).getPropertyValue('--cols').trim());
const railItems = await page.locator('.rail-item').count();
const gridCols = await page.locator('#newsroom-grid .column').count();
console.log(`열 수: --cols=${cols}, 레일=${railItems}, 그리드=${gridCols}`);

// ── 2. 키워드 추가 → 강조
await page.fill('#kw-input', 'trump, 트럼프');
await page.click('#kw-form button[type=submit]');
await page.waitForTimeout(300);
const chips = await page.locator('.kw-chip').allTextContents();
const marks = await page.locator('.card-title mark').count();
const kwCount = await page.locator('#kw-count').textContent();
console.log(`키워드 칩: ${JSON.stringify(chips)} | 강조된 낱말: ${marks}개 | ${kwCount}`);

// ── 3. 필터 토글 → 일치하는 기사만
const before = await page.locator('#newsroom-grid .card').count();
await page.check('#kw-filter');
await page.waitForTimeout(300);
const after = await page.locator('#newsroom-grid .card').count();
const allHit = await page.evaluate(() =>
  [...document.querySelectorAll('#newsroom-grid .card')].every((c) => c.classList.contains('hit')));
console.log(`필터: ${before}건 → ${after}건 | 남은 카드가 전부 일치: ${allHit}`);

// ── 4. 키워드 해제
await page.uncheck('#kw-filter');
await page.click('.kw-chip');
await page.waitForTimeout(250);
console.log(`칩 해제 후 남은 칩: ${await page.locator('.kw-chip').count()}개`);

// ── 5. localStorage 유지 (새로고침)
await page.reload({ waitUntil: 'networkidle' });
console.log(`새로고침 후 유지된 칩: ${JSON.stringify(await page.locator('.kw-chip').allTextContents())}`);

// ── 6. 렌즈 + 브리프 복사
await page.click('[data-view="lens"]');
await page.click('#run-lens');
await page.waitForSelector('.cluster', { timeout: 150000 });
const tones = await page.locator('.tone-label').count();
const toneWidths = await page.evaluate(() =>
  [...document.querySelectorAll('.tone-fill')].slice(0, 4).map((f) => f.style.width));
console.log(`논조 막대: ${tones}개, 폭 샘플=${JSON.stringify(toneWidths)}`);

await page.locator('.cluster-actions .copy').first().click();
await page.waitForTimeout(400);
const clip = await page.evaluate(() => navigator.clipboard.readText());
console.log('--- 브리프 복사 결과 (앞 500자) ---');
console.log(clip.slice(0, 500));
console.log(`--- 링크 포함: ${/https?:\/\//.test(clip)} | 미보도 포함: ${clip.includes('미보도')} | 논조 포함: ${clip.includes('논조')}`);

await page.click('#copy-all');
await page.waitForTimeout(400);
const full = await page.evaluate(() => navigator.clipboard.readText());
console.log(`전체 브리프: ${full.length}자, 클러스터 ${(full.match(/^■/gm) || []).length}개`);

await browser.close();
if (errs.length) { console.error('\n오류:'); errs.forEach((e) => console.error('  ' + e)); process.exit(1); }
console.log('\n브라우저 오류 없음');
