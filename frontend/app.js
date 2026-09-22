/* Newsroom Lens — 빌드 단계 없는 정적 SPA.
 *
 * 서버가 주는 두 개의 응답(/api/articles, /api/lens)을 그대로 그린다. 클라이언트에서
 * 클러스터링이나 판단을 하지 않는다 — 화면에 있는 모든 분석 문장은 서버가 검증을
 * 통과시킨 것이다.
 *
 * 매체 목록을 하드코딩하지 않는다. 열 수·트랙·채널·색이 전부 /api/articles 가 준
 * 소스 배열에서 나온다. 6번째 소스를 붙여도 이 파일은 고치지 않는다.
 *
 * 이 파일은 innerHTML 을 쓰지 않는다. 제목·요약이 전부 제3자(피드)에서 온
 * 문자열이므로 텍스트는 예외 없이 textContent 로만 넣는다.
 */
'use strict';

const NO_COVERAGE = '미보도';
const $ = (sel) => document.querySelector(sel);

/* 서버가 알려준 매체 순서. 첫 응답 전까지는 비어 있다. */
let sources = [];
let order = [];
let lastLens = null;

const LS = {
  theme: 'newsroom-lens:theme',
  keywords: 'newsroom-lens:keywords',
  filter: 'newsroom-lens:kw-filter',
  cache: 'newsroom-lens:last-articles',
  lang: 'newsroom-lens:title-lang',
  titles: 'newsroom-lens:titles',
};

function readLS(key, fallback = null) {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? fallback : JSON.parse(raw);
  } catch {
    return fallback;   // 사생활 보호 모드 / 파싱 실패
  }
}

function writeLS(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* 무시 */ }
}

/* ── 테마 ─────────────────────────────────────────────────────────────── */

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  // 버튼은 '지금 상태' 가 아니라 '누르면 될 상태' 를 말한다.
  $('#theme-label').textContent = theme === 'dark' ? '밝은 화면' : '어두운 화면';
}

function initTheme() {
  const saved = readLS(LS.theme);
  const prefersDark = window.matchMedia?.('(prefers-color-scheme: dark)').matches;
  applyTheme(saved || (prefersDark ? 'dark' : 'light'));

  $('#theme-toggle').addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    applyTheme(next);
    writeLS(LS.theme, next);
  });
}

/* ── 보기 전환 ────────────────────────────────────────────────────────── */

function initViews() {
  // 선택자를 #viewswitch 안으로 좁힌다. .langswitch 가 스타일을 재사용하려고
  // .viewswitch 클래스를 같이 달고 있어서, 좁히지 않으면 언어 버튼 클릭이
  // showView(undefined) 를 불러 뉴스룸·렌즈 두 패널이 모두 사라진다.
  document.querySelectorAll('#viewswitch button').forEach((btn) => {
    btn.addEventListener('click', () => showView(btn.dataset.view));
  });
}

function showView(view) {
  document.querySelectorAll('#viewswitch button').forEach((btn) => {
    btn.setAttribute('aria-selected', String(btn.dataset.view === view));
  });
  $('#view-newsroom').hidden = view !== 'newsroom';
  $('#view-lens').hidden = view !== 'lens';
}

/* ── 시간 표기 ────────────────────────────────────────────────────────── */

function relativeTime(iso) {
  if (!iso) return '시각 미상';
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return '시각 미상';
  const secs = Math.round((Date.now() - then) / 1000);

  if (secs < 60) return '방금';           // 음수(시계 어긋남)도 여기서 흡수된다
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}분 전`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}시간 전`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}일 전`;
  return new Date(then).toLocaleDateString('ko-KR', { month: 'long', day: 'numeric' });
}

function absoluteTime(iso) {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '';
  return new Date(t).toLocaleString('ko-KR', { dateStyle: 'medium', timeStyle: 'short' });
}

/* ── 제목 언어 ────────────────────────────────────────────────────────── */

/* 'original' | 'ko' | 'en'. 번역은 링크를 키로 서버가 캐시하고, 프런트도 한 벌 쥐고
   있다가 오프라인에서도 쓴다. */
let titleLang = readLS(LS.lang, 'original') || 'original';
let titleMap = readLS(LS.titles, {}) || {};
let titlesLoading = false;

/** 화면에 띄울 제목과, 그 아래 함께 보일 원문을 정한다. */
function titleFor(article) {
  const original = article.title;
  if (titleLang === 'original') return { main: original, sub: '' };

  const pair = titleMap[article.link];
  const wanted = pair?.[titleLang];
  // 번역이 아직 없으면 원문을 그대로 쓴다. 빈 제목이나 '번역 중' 을 띄우지 않는다.
  if (!wanted) return { main: original, sub: '' };
  // 이미 그 언어인 기사(연합뉴스 + 한국어)는 원문을 두 번 보여줄 이유가 없다.
  if (wanted.trim() === original.trim()) return { main: original, sub: '' };
  return { main: wanted, sub: original };
}

function initLangSwitch() {
  const buttons = [...document.querySelectorAll('.langswitch button')];

  function paint() {
    buttons.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.lang === titleLang)));
  }

  buttons.forEach((btn) => {
    btn.addEventListener('click', async () => {
      titleLang = btn.dataset.lang;
      writeLS(LS.lang, titleLang);
      paint();
      renderNewsroom(sources);          // 먼저 원문으로 즉시 다시 그린다
      if (titleLang !== 'original') await loadTitles();
    });
  });

  paint();
  if (titleLang !== 'original') loadTitles();
}

/* 매체별로 몇 건씩 넓혀가며 번역을 받아온다.
 *
 * 한 번에 limit=20(매체 5곳 × 20 = 100건)을 요청하면 첫 호출이 60초를 넘고,
 * CloudFront 의 오리진 read timeout(60초)에 걸려 504 가 된다 — 실측으로 확인했다.
 * 단계를 나누면 첫 화면이 ~15초에 들어오고, 뒤 단계는 서버 캐시가 있어 거의 무료다.
 * 중간 단계마다 다시 그리므로 사용자는 위쪽 카드부터 번역되는 걸 본다. */
const TITLE_STEPS = [5, 10, 20];

async function loadTitles() {
  if (titlesLoading) return;
  titlesLoading = true;
  const note = $('#lang-note');
  note.hidden = false;

  let failedTotal = 0;
  let requestedTotal = 0;

  try {
    for (const [i, limit] of TITLE_STEPS.entries()) {
      note.textContent =
        `제목을 번역하는 중입니다 (${i + 1}/${TITLE_STEPS.length}). 원문을 먼저 보여주고, 끝나면 바꿉니다.`;
      const res = await fetch(`./api/titles?limit=${limit}`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);

      titleMap = { ...titleMap, ...(data.titles || {}) };
      writeLS(LS.titles, titleMap);
      renderNewsroom(sources);      // 단계마다 즉시 반영
      failedTotal = data.failed ?? 0;
      requestedTotal = data.requested ?? 0;
    }

    // 일부가 원문 폴백이면 그렇다고 말한다. 조용히 원문을 보여주면 번역된 줄 안다.
    if (failedTotal > 0) {
      note.textContent = `${requestedTotal}건 중 ${failedTotal}건은 번역하지 못해 원문으로 표시합니다.`;
    } else {
      note.hidden = true;
    }
  } catch (err) {
    // 여기까지 받은 번역은 이미 화면에 반영되어 있다. 실패한 지점만 알린다.
    note.textContent = `제목 번역이 중단되었습니다: ${err.message}. 나머지는 원문으로 표시합니다.`;
  } finally {
    titlesLoading = false;
  }
}

/* ── 키워드 추적 (Bonus B) ────────────────────────────────────────────── */

let keywords = readLS(LS.keywords, []) || [];
let filterOn = readLS(LS.filter, false) === true;

function normalizeKeyword(raw) {
  return String(raw || '').trim().toLowerCase();
}

function matches(text) {
  if (!keywords.length) return false;
  const haystack = String(text || '').toLowerCase();
  return keywords.some((k) => haystack.includes(k));
}

/**
 * 키워드를 강조한 텍스트 노드들을 만든다.
 *
 * innerHTML 대신 <mark> 요소를 직접 만들어 붙인다. 피드 제목에 '<' 가 들어오는 일은
 * 실제로 있고(수식·인용), 그걸 HTML 로 해석하게 두면 안 된다.
 */
function highlighted(text) {
  const str = String(text || '');
  if (!keywords.length) return [document.createTextNode(str)];

  // 가장 긴 키워드부터 찾아야 짧은 키워드가 긴 것을 잘라먹지 않는다.
  const sorted = [...keywords].sort((a, b) => b.length - a.length);
  const lower = str.toLowerCase();
  const nodes = [];
  let i = 0;

  while (i < str.length) {
    let hit = null;
    for (const k of sorted) {
      if (k && lower.startsWith(k, i)) { hit = k; break; }
    }
    if (hit) {
      const mark = document.createElement('mark');
      mark.textContent = str.slice(i, i + hit.length);
      nodes.push(mark);
      i += hit.length;
    } else {
      // 다음 일치 지점까지 한 번에 건너뛴다(문자 단위 노드 폭발 방지).
      let next = str.length;
      for (const k of sorted) {
        if (!k) continue;
        const at = lower.indexOf(k, i + 1);
        if (at !== -1 && at < next) next = at;
      }
      nodes.push(document.createTextNode(str.slice(i, next)));
      i = next;
    }
  }
  return nodes;
}

function renderKeywordChips() {
  const list = $('#kw-list');
  list.replaceChildren(...keywords.map((k) => {
    const li = document.createElement('li');
    const btn = document.createElement('button');
    btn.className = 'kw-chip';
    btn.type = 'button';
    btn.textContent = k;
    btn.title = `"${k}" 추적 해제`;
    btn.setAttribute('aria-label', `키워드 ${k} 추적 해제`);
    btn.addEventListener('click', () => {
      keywords = keywords.filter((x) => x !== k);
      writeLS(LS.keywords, keywords);
      afterKeywordChange();
    });
    li.append(btn);
    return li;
  }));

  const filterBox = $('#kw-filter');
  filterBox.checked = filterOn;
  filterBox.disabled = keywords.length === 0;
}

function afterKeywordChange() {
  renderKeywordChips();
  renderNewsroom(sources);
  if (lastLens) renderLens(lastLens);
}

function initKeywords() {
  $('#kw-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const input = $('#kw-input');
    // 쉼표로 여러 개를 한 번에 받는다.
    const added = input.value.split(',').map(normalizeKeyword).filter(Boolean);
    for (const k of added) if (!keywords.includes(k)) keywords.push(k);
    input.value = '';
    writeLS(LS.keywords, keywords);
    afterKeywordChange();
  });

  $('#kw-filter').addEventListener('change', (e) => {
    filterOn = e.target.checked;
    writeLS(LS.filter, filterOn);
    afterKeywordChange();
  });

  renderKeywordChips();
}

/* ── 매체 메타 ────────────────────────────────────────────────────────── */

function applySourceIdentity(list) {
  order = list.map((s) => s.source);
  document.documentElement.style.setProperty('--cols', String(order.length));

  // 매체별 색은 CSS 에 미리 정의된 것을 쓰고, 정의가 없는 새 소스는 팔레트를 순환한다.
  // 그래야 소스를 추가해도 색이 '없는' 채널이 생기지 않는다.
  list.forEach((s, i) => {
    document.documentElement.style.setProperty(`--src-${s.source}`, `var(--${s.source}, var(--fallback-${i % 6}))`);
  });

  $('#tracks').replaceChildren(...list.map((s) => {
    const i = document.createElement('i');
    i.style.background = `var(--src-${s.source})`;
    return i;
  }));

  $('#tagline').textContent = `같은 사건, ${list.length}개의 프레임`;
  $('#lens-sub').textContent =
    `${list.length}개 매체의 최신 헤드라인을 묶고, 같은 사건을 서로 어떻게 달리 말했는지 봅니다.`;
}

function sourceMeta(key) {
  const found = sources.find((s) => s.source === key);
  return found || { source: key, label: key, label_en: '', lang: 'en' };
}

/* ── 상태 레일 + 뉴스룸 ───────────────────────────────────────────────── */

function renderRail(list) {
  $('#status-rail').replaceChildren(...list.map((s) => {
    const li = document.createElement('li');
    li.className = 'rail-item';
    li.style.setProperty('--edge', `var(--src-${s.source})`);

    const name = document.createElement('p');
    name.className = 'rail-name';
    name.textContent = s.label;

    const figs = document.createElement('p');
    figs.className = 'rail-figs';
    const countEl = document.createElement('b');
    countEl.textContent = String(s.count);
    figs.append(
      countEl,
      document.createTextNode(
        `건 · ${s.last_success ? relativeTime(s.last_success) + ' 수집' : '수집 대기'}`,
      ),
    );

    // 세 가지 상태를 구분한다. '정체' 가 따로 있어야 하는 이유: 얼어붙은 인덱스는
    // 수집이 계속 성공하므로 실패로 잡히지 않는데, 화면에는 옛날 기사만 남는다.
    const state = document.createElement('p');
    let mood = 'live', word = '정상';
    if (s.last_error) { mood = 'dead'; word = s.last_error; }
    else if (s.stale) { mood = 'warn'; word = `정체 — 최신 기사 ${relativeTime(s.newest_published)}`; }
    state.className = `rail-state ${mood}`;
    const dot = document.createElement('span');
    dot.className = 'dot';
    state.append(dot, document.createTextNode(word));

    li.append(name, figs, state);
    if (s.last_success) li.title = `마지막 수집 성공: ${absoluteTime(s.last_success)}`;
    return li;
  }));
}

function renderNewsroom(list) {
  const grid = $('#newsroom-grid');
  let shown = 0;
  let total = 0;

  const columns = list.map((s) => {
    const col = document.createElement('div');
    col.className = 'column';
    col.style.setProperty('--edge', `var(--src-${s.source})`);

    const head = document.createElement('div');
    head.className = 'col-head';
    const h3 = document.createElement('h3');
    h3.textContent = s.label;
    const sub = document.createElement('span');
    sub.textContent = `${s.label_en} · 원문 ${s.lang === 'ko' ? '한국어' : '영어'}`;
    head.append(h3, sub);
    col.append(head);

    // 죽은 피드는 숨기지 않고 이유를 적는다. 빈 칸과 고장은 다르다.
    if (s.last_error) {
      const warn = document.createElement('p');
      warn.className = 'col-dead';
      warn.textContent = `수집 실패: ${s.last_error}`;
      col.append(warn);
    } else if (s.stale) {
      const warn = document.createElement('p');
      warn.className = 'col-dead col-stale';
      warn.textContent =
        `이 매체의 최신 기사가 ${relativeTime(s.newest_published)}입니다. 수집은 정상이므로 소스가 갱신을 멈춘 것으로 보입니다.`;
      col.append(warn);
    }

    const articles = s.articles || [];
    total += articles.length;
    const visible = filterOn && keywords.length
      ? articles.filter(articleMatches)
      : articles;
    shown += visible.length;

    const ul = document.createElement('ul');
    ul.className = 'col-body';
    if (!visible.length && articles.length) {
      const none = document.createElement('li');
      none.className = 'col-none';
      none.textContent = '일치하는 기사 없음';
      ul.append(none);
    }
    ul.append(...visible.map(articleCard));
    col.append(ul);
    return col;
  });

  const hasAny = list.some((s) => (s.articles || []).length > 0);
  if (!hasAny) {
    const note = $('#newsroom-empty');
    note.textContent = list.length && list.every((s) => s.last_error)
      ? '모든 매체에서 수집에 실패했습니다. 다음 주기에 다시 시도합니다.'
      : '첫 수집을 기다리고 있습니다. 잠시 후 헤드라인이 채워집니다.';
    grid.replaceChildren(note);
  } else {
    grid.replaceChildren(...columns);
  }

  $('#kw-count').textContent = keywords.length
    ? `${total}건 중 ${shown}건 일치`
    : '';
}

function articleCard(article) {
  const li = document.createElement('li');
  li.className = 'card';
  if (articleMatches(article)) li.classList.add('hit');

  const a = document.createElement('a');
  a.href = article.link;
  a.target = '_blank';
  a.rel = 'noopener noreferrer';

  const { main, sub } = titleFor(article);

  const title = document.createElement('p');
  title.className = 'card-title';
  title.replaceChildren(...highlighted(main));
  a.append(title);

  // 번역을 보고 있을 때는 원문을 아래에 함께 남긴다. 번역만 남으면 독자가
  // 원문을 검증할 수 없고, 오역을 알아챌 방법도 없다.
  if (sub) {
    const orig = document.createElement('p');
    orig.className = 'card-original';
    orig.replaceChildren(...highlighted(sub));
    a.append(orig);
  }

  const foot = document.createElement('p');
  foot.className = 'card-foot';
  const when = document.createElement('time');
  when.dateTime = article.published;
  when.textContent = relativeTime(article.published);
  when.title = absoluteTime(article.published);
  const ext = document.createElement('span');
  ext.className = 'ext';
  ext.textContent = '원문 보기';
  foot.append(when, ext);

  a.append(foot);
  if (article.summary) a.title = article.summary;
  li.append(a);
  return li;
}

/** 키워드 일치는 원문·한국어·영어 제목 전부에서 본다.
 *  'trump' 를 추적하는데 한국어 제목만 보이는 상태에서 일치가 사라지면 추적이 거짓이 된다. */
function articleMatches(article) {
  const pair = titleMap[article.link];
  return matches(article.title)
      || matches(article.summary)
      || matches(pair?.ko)
      || matches(pair?.en);
}

/* ── 데이터 적재 (+ 오프라인 폴백) ────────────────────────────────────── */

function setOffline(on) {
  $('#offline-note').hidden = !on;
  $('#run-lens').disabled = on;
}

async function loadArticles() {
  try {
    const res = await fetch('./api/articles?limit=20', { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    sources = data.sources || [];
    applySourceIdentity(sources);
    renderRail(sources);
    renderNewsroom(sources);
    const secs = data.meta?.poll_interval_seconds ?? 300;
    const every = secs % 60 === 0 ? `${secs / 60}분` : `${secs}초`;
    $('#poll-note').textContent = `${every}마다 자동 수집`;
    setOffline(false);

    // 다음 오프라인을 위해 마지막 성공 응답을 남긴다.
    writeLS(LS.cache, { at: new Date().toISOString(), data });
  } catch (err) {
    // 서버에 닿지 못했다. 마지막으로 받은 기사를 보여준다 — 빈 화면보다 낫다.
    const cached = readLS(LS.cache);
    if (cached?.data?.sources?.length) {
      sources = cached.data.sources;
      applySourceIdentity(sources);
      renderRail(sources);
      renderNewsroom(sources);
      $('#poll-note').textContent = `오프라인 · ${absoluteTime(cached.at)} 기준`;
      setOffline(true);
    } else {
      $('#poll-note').textContent = `수집 상태를 불러오지 못했습니다 (${err.message})`;
      setOffline(true);
    }
  }
}

/* ── 지금 새로 고침 ───────────────────────────────────────────────────── */

let refreshing = false;

/** 자동 주기를 기다리지 않고 즉시 수집을 요청한다.
 *  최소 간격은 서버가 판단한다 — 브라우저 쪽 제한은 새로 고침 한 번으로 사라진다. */
async function refreshNow() {
  if (refreshing) return;
  refreshing = true;
  const btn = $('#refresh');
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = '수집 중…';

  try {
    const res = await fetch('./api/refresh', { method: 'POST' });
    const data = await res.json();
    await loadArticles();
    if (data.refreshed === false) {
      // 거부는 오류가 아니다. 이미 최신이라는 뜻이다.
      $('#poll-note').textContent =
        `방금 수집했습니다. ${data.retry_after_seconds}초 뒤에 다시 받아올 수 있습니다.`;
    }
  } catch (err) {
    $('#poll-note').textContent = `새로 고침에 실패했습니다 (${err.message})`;
  } finally {
    refreshing = false;
    btn.disabled = false;
    btn.textContent = label;
  }
}

/* ── 렌즈 ─────────────────────────────────────────────────────────────── */

let lensRunning = false;

function setLensStatus(text, isBad = false) {
  const el = $('#lens-status');
  el.hidden = !text;
  el.textContent = text || '';
  el.classList.toggle('bad', Boolean(isBad));
}

async function runLens() {
  if (lensRunning) return;             // 분석 중에는 버튼을 잠근다
  lensRunning = true;
  const btn = $('#run-lens');
  btn.disabled = true;
  btn.textContent = '분석 중…';
  setLensStatus('매체별 헤드라인을 묶고 있습니다. 완료까지 30초 정도 걸리며, 그동안 이 버튼은 잠깁니다.');

  try {
    const res = await fetch('./api/lens', { method: 'POST' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    lastLens = data;
    renderLens(data);
    setLensStatus(
      data.cached
        ? `${relativeTime(data.generated_at)} 분석 결과입니다. ${Math.round(data.bucket_seconds / 60)}분마다 새로 계산합니다.`
        : `${absoluteTime(data.generated_at)} 기준 분석을 마쳤습니다.`
    );
  } catch (err) {
    setLensStatus(`분석에 실패했습니다: ${err.message}`, true);
  } finally {
    lensRunning = false;
    btn.disabled = false;
    btn.textContent = '비교 분석 실행';
  }
}

function renderLens(data) {
  $('#lens-empty').hidden = true;
  $('#foot-model').textContent = data.model || 'Claude';
  $('#copy-all').hidden = false;

  const overview = $('#lens-overview');
  if (data.overview) {
    $('#overview-text').replaceChildren(...highlighted(data.overview));
    $('#overview-meta').textContent =
      `${absoluteTime(data.generated_at)} · 클러스터 ${data.clusters.length}개 · ${data.model}`;
    overview.hidden = false;
  } else {
    overview.hidden = true;
  }

  const visible = filterOn && keywords.length
    ? data.clusters.filter(clusterMatches)
    : data.clusters;

  const host = $('#lens-clusters');
  if (!visible.length) {
    const none = document.createElement('p');
    none.className = 'empty';
    none.textContent = '추적 중인 키워드와 일치하는 토픽이 없습니다.';
    host.replaceChildren(none);
    return;
  }
  host.replaceChildren(...visible.map(clusterCard));
}

function clusterMatches(cluster) {
  if (matches(cluster.topic) || matches(cluster.summary)) return true;
  return Object.values(cluster.frames || {}).some((f) => matches(f))
      || Object.values(cluster.evidence || {}).some((list) => list.some((e) => matches(e.title)));
}

function clusterCard(cluster) {
  const section = document.createElement('article');
  section.className = 'cluster';

  const head = document.createElement('div');
  head.className = 'cluster-head';

  const top = document.createElement('div');
  top.className = 'cluster-top';
  const h3 = document.createElement('h3');
  h3.replaceChildren(...highlighted(cluster.topic));
  top.append(h3, coverageMeter(cluster));
  head.append(top);

  if (cluster.summary) {
    const sum = document.createElement('p');
    sum.className = 'cluster-summary';
    sum.replaceChildren(...highlighted(cluster.summary));
    head.append(sum);
  }

  // 클러스터 하나를 Slack 에 붙일 수 있는 평문으로 복사한다.
  const actions = document.createElement('p');
  actions.className = 'cluster-actions';
  actions.append(copyButton('브리프 복사', () => briefText(cluster)));
  head.append(actions);

  const channels = document.createElement('div');
  channels.className = 'channels';
  channels.append(...order.map((key) => channel(key, cluster)));

  section.append(head, channels);
  return section;
}

function coverageMeter(cluster) {
  const wrap = document.createElement('div');
  wrap.className = 'meter';

  const cells = document.createElement('span');
  cells.className = 'meter-cells';
  cells.setAttribute('aria-hidden', 'true');
  order.forEach((key) => {
    const cell = document.createElement('i');
    if (cluster.covered.includes(key)) {
      cell.classList.add('on');
      cell.style.background = `var(--src-${key})`;
    }
    cells.append(cell);
  });

  const count = document.createElement('span');
  count.className = 'meter-count';
  count.textContent = `${cluster.covered.length}/${order.length} 보도`;

  wrap.append(cells, count);
  return wrap;
}

/* 논조 막대 (Bonus C). -2..+2 를 중앙 기준 양방향 막대로 그린다. */
function toneBar(key, value) {
  const wrap = document.createElement('p');
  wrap.className = 'tone';

  const label = document.createElement('span');
  label.className = 'tone-label';
  label.textContent = TONE_WORDS[value] ?? '중립';

  const track = document.createElement('span');
  track.className = 'tone-track';
  const fill = document.createElement('span');
  fill.className = `tone-fill ${value < 0 ? 'neg' : value > 0 ? 'pos' : 'zero'}`;
  // 중앙(50%)에서 좌우로 뻗는다. |value|=2 → 절반을 꽉 채운다.
  fill.style.width = `${Math.abs(value) * 25}%`;
  if (value < 0) fill.style.right = '50%'; else fill.style.left = '50%';
  if (value !== 0) fill.style.background = `var(--src-${key})`;
  track.append(fill);

  wrap.append(track, label);
  wrap.title = `논조 ${value > 0 ? '+' : ''}${value} — ${TONE_WORDS[value] ?? '중립'}`;
  return wrap;
}

const TONE_WORDS = {
  '-2': '강한 비판', '-1': '비판적', 0: '중립', 1: '우호적', 2: '적극 옹호',
};

function channel(key, cluster) {
  const info = sourceMeta(key);
  const frame = cluster.frames?.[key] || NO_COVERAGE;
  const evidence = cluster.evidence?.[key] || [];
  const silent = frame === NO_COVERAGE || evidence.length === 0;

  const div = document.createElement('div');
  div.className = `channel${silent ? ' silent' : ''}`;
  div.style.setProperty('--edge', `var(--src-${key})`);

  const name = document.createElement('p');
  name.className = 'channel-name';
  name.textContent = info.label;
  if (info.label_en) {
    const small = document.createElement('small');
    small.textContent = info.label_en;
    name.append(small);
  }
  div.append(name);

  if (silent) {
    // 흐린 글씨가 아니라 '측정된 공백'. 보도한 채널과 같은 폭을 차지한다.
    const voidBox = document.createElement('p');
    voidBox.className = 'channel-void';
    voidBox.textContent = NO_COVERAGE;
    div.append(voidBox);
    return div;
  }

  const text = document.createElement('p');
  text.className = 'channel-frame';
  text.replaceChildren(...highlighted(frame));
  div.append(text);

  div.append(toneBar(key, cluster.tone?.[key] ?? 0));

  // 근거 칩. 인덱스가 아니라 실제 기사로 나간다.
  const chips = document.createElement('p');
  chips.className = 'chips';
  chips.append(...evidence.map((e, i) => {
    const chip = document.createElement('a');
    chip.className = 'chip';
    chip.href = e.link;
    chip.target = '_blank';
    chip.rel = 'noopener noreferrer';
    // 서버 내부 인덱스(e.index)를 그대로 보여주지 않는다. 독자에게 '근거 0' 은 아무
    // 뜻이 없고, 0부터 센다는 사실은 우리 구현 세부사항이다.
    chip.textContent = `근거 ${i + 1}`;
    chip.title = e.title;
    chip.setAttribute('aria-label', `근거 ${i + 1}: ${e.title}`);
    return chip;
  }));
  div.append(chips);

  return div;
}

/* ── 브리프 내보내기 (Bonus D) ────────────────────────────────────────── */

function briefText(cluster) {
  const lines = [`■ ${cluster.topic}  (${cluster.covered.length}/${order.length} 보도)`];
  if (cluster.summary) lines.push(cluster.summary, '');

  for (const key of order) {
    const info = sourceMeta(key);
    const frame = cluster.frames?.[key] || NO_COVERAGE;
    const evidence = cluster.evidence?.[key] || [];
    if (frame === NO_COVERAGE || !evidence.length) {
      lines.push(`· ${info.label}: ${NO_COVERAGE}`);
      continue;
    }
    const tone = cluster.tone?.[key] ?? 0;
    lines.push(`· ${info.label} [논조 ${tone > 0 ? '+' : ''}${tone} ${TONE_WORDS[tone] ?? '중립'}]`);
    lines.push(`  ${frame}`);
    // 링크를 빼면 Slack 에 붙였을 때 검증 불가능한 주장만 남는다.
    evidence.forEach((e) => lines.push(`  - ${e.title}\n    ${e.link}`));
  }
  return lines.join('\n');
}

function fullBriefText(data) {
  const header = [
    'Newsroom Lens — 매체 관점 비교',
    `${absoluteTime(data.generated_at)} · ${data.model}`,
    '',
  ];
  if (data.overview) header.push(`[미디어 지형]`, data.overview, '');
  return header.concat(data.clusters.map(briefText)).join('\n') +
    '\n\n원문 링크는 각 매체 기사로 직접 연결됩니다.';
}

function copyButton(label, getText) {
  const btn = document.createElement('button');
  btn.className = 'linkbtn copy';
  btn.type = 'button';
  btn.textContent = label;
  btn.addEventListener('click', async () => {
    const ok = await copyText(getText());
    btn.textContent = ok ? '복사했습니다' : '복사 실패 — 직접 선택하세요';
    setTimeout(() => { btn.textContent = label; }, 2000);
  });
  return btn;
}

async function copyText(text) {
  // navigator.clipboard 는 보안 컨텍스트(HTTPS/localhost)에서만 있다. 없으면
  // 선택+execCommand 로 내려간다 — HTTP 로 띄운 로컬 확인에서도 동작해야 한다.
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* 아래 폴백 */ }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.append(ta);
    ta.select();
    const ok = document.execCommand('copy');
    ta.remove();
    return ok;
  } catch {
    return false;
  }
}

/* ── 기동 ─────────────────────────────────────────────────────────────── */

initTheme();
initViews();
initKeywords();
initLangSwitch();
loadArticles();

$('#refresh').addEventListener('click', refreshNow);
$('#run-lens').addEventListener('click', runLens);
$('#copy-all').addEventListener('click', async (e) => {
  if (!lastLens) return;
  // e.currentTarget 을 await 뒤에 읽으면 null 이다. 이벤트 디스패치가 끝나면 브라우저가
  // currentTarget 을 비우고, async 핸들러의 await 뒤 코드는 이미 다음 마이크로태스크다.
  // 그래서 await 하기 '전에' 버튼을 붙잡아 둔다.
  const btn = e.currentTarget;
  const ok = await copyText(fullBriefText(lastLens));
  btn.textContent = ok ? '복사했습니다' : '복사 실패';
  setTimeout(() => { btn.textContent = '전체 브리프 복사'; }, 2000);
});

// 화면만 다시 읽는다(수집을 유발하지 않는다). 상대 시각이 늙지 않게 1분마다.
setInterval(() => { if (!document.hidden) loadArticles(); }, 60_000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) loadArticles(); });
window.addEventListener('online', loadArticles);
window.addEventListener('offline', () => setOffline(true));
