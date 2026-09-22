/* Newsroom Lens — 빌드 단계 없는 정적 SPA.
 *
 * 서버가 주는 두 개의 응답(/api/articles, /api/lens)을 그대로 그린다. 클라이언트에서
 * 클러스터링이나 판단을 하지 않는다 — 화면에 있는 모든 분석 문장은 서버가 검증을
 * 통과시킨 것이다.
 */
'use strict';

const ORDER = ['bbc', 'guardian', 'nhk', 'yna'];
const NO_COVERAGE = '미보도';

const $ = (sel) => document.querySelector(sel);

/* ── 테마 ─────────────────────────────────────────────────────────────── */

const THEME_KEY = 'newsroom-lens:theme';

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  // 버튼은 '지금 상태' 가 아니라 '누르면 될 상태' 를 말한다.
  $('#theme-label').textContent = theme === 'dark' ? '밝은 화면' : '어두운 화면';
}

function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem(THEME_KEY); } catch { /* 사생활 보호 모드 */ }
  const prefersDark = window.matchMedia?.('(prefers-color-scheme: dark)').matches;
  applyTheme(saved || (prefersDark ? 'dark' : 'light'));

  $('#theme-toggle').addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    applyTheme(next);
    try { localStorage.setItem(THEME_KEY, next); } catch { /* 무시 */ }
  });
}

/* ── 보기 전환 ────────────────────────────────────────────────────────── */

function initViews() {
  document.querySelectorAll('.viewswitch button').forEach((btn) => {
    btn.addEventListener('click', () => showView(btn.dataset.view));
  });
}

function showView(view) {
  document.querySelectorAll('.viewswitch button').forEach((btn) => {
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

  if (secs < 0) return '방금';          // 서버·발행처 시계가 어긋난 경우
  if (secs < 60) return '방금';
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

/* ── 상태 레일 + 뉴스룸 ───────────────────────────────────────────────── */

let lastSources = [];

function byOrder(sources) {
  const map = new Map(sources.map((s) => [s.source, s]));
  return ORDER.map((key) => map.get(key)).filter(Boolean);
}

function renderRail(sources) {
  const rail = $('#status-rail');
  rail.replaceChildren(...sources.map((s) => {
    const li = document.createElement('li');
    li.className = 'rail-item';
    li.style.setProperty('--edge', `var(--${s.source})`);

    const name = document.createElement('p');
    name.className = 'rail-name';
    name.textContent = s.label;

    // 이 파일은 innerHTML 을 쓰지 않는다. 제목·요약이 전부 제3자(피드)에서 온
    // 문자열이므로, 텍스트는 예외 없이 textContent 로만 넣는다.
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

    const state = document.createElement('p');
    state.className = `rail-state ${s.healthy ? 'live' : 'dead'}`;
    const dot = document.createElement('span');
    dot.className = 'dot';
    state.append(dot, document.createTextNode(s.healthy ? '정상' : (s.last_error || '대기 중')));

    li.append(name, figs, state);
    if (s.last_success) li.title = `마지막 수집 성공: ${absoluteTime(s.last_success)}`;
    return li;
  }));
}

function renderNewsroom(sources) {
  const grid = $('#newsroom-grid');
  const hasAny = sources.some((s) => s.articles.length > 0);

  if (!hasAny) {
    const note = $('#newsroom-empty');
    note.textContent = sources.every((s) => s.last_error)
      ? '네 매체 모두 수집에 실패했습니다. 다음 주기에 다시 시도합니다.'
      : '첫 수집을 기다리고 있습니다. 잠시 후 헤드라인이 채워집니다.';
    grid.replaceChildren(note);
    return;
  }

  grid.replaceChildren(...sources.map((s) => {
    const col = document.createElement('div');
    col.className = 'column';
    col.style.setProperty('--edge', `var(--${s.source})`);

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
    }

    const list = document.createElement('ul');
    list.className = 'col-body';
    list.append(...s.articles.map(articleCard));
    col.append(list);
    return col;
  }));
}

function articleCard(article) {
  const li = document.createElement('li');
  li.className = 'card';

  const a = document.createElement('a');
  a.href = article.link;
  a.target = '_blank';
  a.rel = 'noopener noreferrer';

  const title = document.createElement('p');
  title.className = 'card-title';
  title.textContent = article.title;      // 원문 제목은 번역하지 않고 그대로 둔다

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

  a.append(title, foot);
  if (article.summary) a.title = article.summary;
  li.append(a);
  return li;
}

async function loadArticles() {
  try {
    const res = await fetch('./api/articles?limit=20', { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    lastSources = byOrder(data.sources || []);
    renderRail(lastSources);
    renderNewsroom(lastSources);
    const interval = data.meta?.poll_interval_seconds ?? 120;
    $('#poll-note').textContent = `${interval}초마다 수집`;
  } catch (err) {
    $('#poll-note').textContent = `수집 상태를 불러오지 못했습니다 (${err.message})`;
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
  setLensStatus('네 매체의 헤드라인을 묶고 있습니다. 완료까지 20초 정도 걸립니다.');

  try {
    const res = await fetch('./api/lens', { method: 'POST' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
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

  const overview = $('#lens-overview');
  if (data.overview) {
    $('#overview-text').textContent = data.overview;
    $('#overview-meta').textContent =
      `${absoluteTime(data.generated_at)} · 클러스터 ${data.clusters.length}개 · ${data.model}`;
    overview.hidden = false;
  } else {
    overview.hidden = true;
  }

  const labels = labelMap();
  $('#lens-clusters').replaceChildren(...data.clusters.map((c) => clusterCard(c, labels)));
}

function labelMap() {
  const map = new Map(lastSources.map((s) => [s.source, s]));
  // 아직 /api/articles 응답이 없어도 렌즈는 그려져야 한다.
  const fallback = { bbc: 'BBC 월드', guardian: '가디언 월드', nhk: 'NHK 월드', yna: '연합뉴스 국제' };
  return (key) => map.get(key) || { label: fallback[key] || key, label_en: '' };
}

function clusterCard(cluster, labels) {
  const section = document.createElement('article');
  section.className = 'cluster';

  // ── 머리: 토픽 + 보도 계량기 + 공통 사실
  const head = document.createElement('div');
  head.className = 'cluster-head';

  const top = document.createElement('div');
  top.className = 'cluster-top';
  const h3 = document.createElement('h3');
  h3.textContent = cluster.topic;
  top.append(h3, coverageMeter(cluster));
  head.append(top);

  if (cluster.summary) {
    const sum = document.createElement('p');
    sum.className = 'cluster-summary';
    sum.textContent = cluster.summary;
    head.append(sum);
  }

  // ── 네 개의 채널
  const channels = document.createElement('div');
  channels.className = 'channels';
  channels.append(...ORDER.map((key) => channel(key, cluster, labels)));

  section.append(head, channels);
  return section;
}

function coverageMeter(cluster) {
  const wrap = document.createElement('div');
  wrap.className = 'meter';

  const cells = document.createElement('span');
  cells.className = 'meter-cells';
  cells.setAttribute('aria-hidden', 'true');
  ORDER.forEach((key) => {
    const cell = document.createElement('i');
    if (cluster.covered.includes(key)) {
      cell.classList.add('on');
      cell.style.background = `var(--${key})`;
    }
    cells.append(cell);
  });

  const count = document.createElement('span');
  count.className = 'meter-count';
  count.textContent = `${cluster.covered.length}/${ORDER.length} 보도`;

  wrap.append(cells, count);
  return wrap;
}

function channel(key, cluster, labels) {
  const info = labels(key);
  const frame = cluster.frames?.[key] || NO_COVERAGE;
  const evidence = cluster.evidence?.[key] || [];
  const silent = frame === NO_COVERAGE || evidence.length === 0;

  const div = document.createElement('div');
  div.className = `channel${silent ? ' silent' : ''}`;
  div.style.setProperty('--edge', `var(--${key})`);

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
  text.textContent = frame;
  div.append(text);

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
    // 뜻이 없고, 0부터 센다는 사실은 우리 구현 세부사항이다. 화면에는 이 매체의
    // 몇 번째 근거인지만 1부터 세어 보여주고, 실제 기사 제목은 tooltip 에 둔다.
    chip.textContent = `근거 ${i + 1}`;
    chip.title = e.title;
    chip.setAttribute('aria-label', `근거 ${i + 1}: ${e.title}`);
    return chip;
  }));
  div.append(chips);

  return div;
}

/* ── 기동 ─────────────────────────────────────────────────────────────── */

initTheme();
initViews();
loadArticles();

$('#refresh').addEventListener('click', loadArticles);
$('#run-lens').addEventListener('click', runLens);

// 수집 주기와 같은 리듬으로 화면을 갱신한다. 탭이 숨어 있으면 쉰다.
setInterval(() => {
  if (!document.hidden) loadArticles();
}, 60_000);
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) loadArticles();
});
