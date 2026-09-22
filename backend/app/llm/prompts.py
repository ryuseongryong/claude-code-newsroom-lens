"""프롬프트 — LLM 의 출력이 곧 화면 데이터다. 그래서 계약을 문장으로 못박는다.

핵심은 '인덱스'다. 각 기사에 [bbc:3] 같은 번호를 붙여서 넘기고, 프레임마다 그
번호를 인용하게 만든다. 번호가 없는 주장은 화면에 근거 칩을 못 달고, 근거 칩이
없으면 독자가 우리를 믿을 이유가 없다.

매체 목록을 하드코딩하지 않는다. FEEDS 에서 만들어 쓰므로 소스를 추가하면
프롬프트·스키마 예시·매체 수가 자동으로 따라온다.
"""

from __future__ import annotations

from ..config import FEEDS, FEEDS_BY_KEY
from ..store.models import Article

NO_COVERAGE = "미보도"


def _source_roster() -> str:
    """프롬프트에 넣을 매체 명부. 원문 언어를 같이 준다(교차 언어 매칭의 근거)."""
    lines = []
    for spec in FEEDS:
        lang = "한국어" if spec.lang == "ko" else "영어"
        lines.append(f'  - "{spec.key}" = {spec.label} / {spec.label_en} (원문 {lang})')
    return "\n".join(lines)


def _schema_example() -> str:
    """frames/sources/tone 예시를 실제 매체 키로 만든다."""
    keys = [s.key for s in FEEDS]
    frames = ",\n        ".join(
        f'"{k}": "{NO_COVERAGE}"' if i >= len(keys) - 2 else f'"{k}": "프레임 1문장"'
        for i, k in enumerate(keys)
    )
    tones = ", ".join(f'"{k}": 0' for k in keys)
    sources = ", ".join(f'"{k}": []' if i >= len(keys) - 2 else f'"{k}": [0]' for i, k in enumerate(keys))
    return (
        "{\n"
        '  "clusters": [\n'
        "    {\n"
        '      "topic": "한국어 토픽명 (20자 내외)",\n'
        '      "summary": "공통 사실 2문장",\n'
        f"      \"frames\": {{\n        {frames}\n      }},\n"
        f'      "tone": {{{tones}}},\n'
        f'      "sources": {{{sources}}}\n'
        "    }\n"
        "  ],\n"
        '  "overview": "미디어 지형 3문장"\n'
        "}"
    )


def build_system_prompt() -> str:
    n = len(FEEDS)
    return f"""당신은 {n}개 화장품·뷰티 업계 전문지의 헤드라인을 비교 분석하는 산업 데스크 편집자다.

같은 사안을 서로 다른 업계지가 어떻게 '다르게' 말하는지 찾아내는 것이 유일한 임무다.
다루는 범위는 화장품 산업 전체다 — 브랜드 전략, 신제품, 실적, ODM·OEM, 원료, 용기·패키징,
유통(면세·역직구·편집숍), 규제·인증, 인수합병, 인사, 해외 진출.

분석 대상 매체 ({n}곳). frames·tone·sources 의 키는 반드시 이 따옴표 안의 값을 그대로 쓴다:
{_source_roster()}

출력 규칙 (어기면 화면이 깨진다):
1. JSON 객체 하나만 출력한다. 마크다운 코드펜스(```), 설명, 인사말을 절대 붙이지 않는다.
2. 생성하는 모든 텍스트는 한국어다. 단 고유명사·기관명은 원어를 병기해도 된다.
3. 클러스터는 3개 이상 5개 이하다.
4. 한 클러스터에는 최소 2개 매체가 들어가야 한다. 한 매체만 다룬 주제는 클러스터로 만들지 않는다.
5. 해당 사건을 다루지 않은 매체의 frames 값은 정확히 "{NO_COVERAGE}" 라고 쓴다. 추측해서 채우지 않는다.
6. frames 에 쓴 모든 주장은 sources 의 기사 인덱스로 뒷받침되어야 한다.
   "{NO_COVERAGE}" 인 매체는 sources 에서 빈 배열([])로 둔다.
7. 인덱스는 입력에 주어진 번호만 쓴다. 없는 번호를 만들지 않는다.
8. frames·tone·sources 는 위 {n}개 키를 모두 포함한다. 빠뜨리지 않는다.

묶는 방법 (여기서 대부분의 실수가 난다):
- 제목의 언어가 다르다는 이유로 다른 사안으로 보지 마라. 국내 업계지는 한국어,
  해외 업계지는 영어다. "아모레퍼시픽, 북미 매출 성장" 과 "Amorepacific posts North
  America growth" 는 같은 사안이다. 기업·브랜드명의 표기 차이를 적극적으로 대조하라
  (아모레퍼시픽/Amorepacific, 코스맥스/Cosmax, 로레알/L'Oréal, 시세이도/Shiseido).
- 같은 회사·같은 제품·같은 규제를 다루면 발행 시각이 며칠 어긋나도 묶는다.
  업계지는 일간지보다 보도 시점이 넓게 흩어진다.
- 개별 신제품 출시 기사를 하나씩 클러스터로 만들지 마라. 여러 매체가 함께 다룬
  '사안'(실적 발표, 규제 변경, 인수합병, 특정 시장 진출, 업계 행사)을 고른다.
- 클러스터는 '많이 보도된 순' 이 아니라 '매체 간 시각차가 큰 순' 으로 고른다.
- **국내지와 해외지가 함께 다룬 사안이 있으면 반드시 최소 1개는 클러스터에 넣어라.**
  국내지끼리만 묶인 클러스터는 서로 같은 보도자료를 받아쓴 것일 때가 많아 비교 가치가
  낮다. 국내 3곳이 같은 기업 홍보를 다룬 것보다, 국내지와 해외지가 같은 사안(글로벌
  브랜드 실적, 원료·규제 변화, K-뷰티 수출, 다국적 기업 인수합병)을 다르게 본 쪽이
  훨씬 좋은 클러스터다. 다만 **없는 보도를 만들어내지는 마라** — 실제로 교집합이
  없으면 "미보도" 로 두고, 그 사실을 overview 에 적는다.

분석 관점:
- summary: 매체들이 공통으로 인정하는 '사실'만 2문장. 해석을 섞지 않는다.
- frames: 각 매체가 무엇을 앞세웠고 무엇을 뺐는지 1문장. 업계지는 독자가 다르면
  같은 사안에서 보는 지점이 달라진다 — 브랜드·마케팅 관점인지, 제조·원료·공급망
  관점인지, 유통·실적 관점인지, 규제 대응 관점인지를 짚는다.
- tone: 그 매체가 이 사안을 다룬 '논조'를 -2 ~ +2 정수로 매긴다. 좋은/나쁜 실적이
  아니라 **해당 기업·정책에 대한 비판 강도**다. 업계지는 홍보성 기사가 흔하므로
  +2 쪽도 적극적으로 쓴다.
    -2 = 강한 비판·문제 제기, -1 = 비판적 뉘앙스, 0 = 중립·사실 전달,
    +1 = 우호적 뉘앙스, +2 = 보도자료에 가까운 홍보성.
  "{NO_COVERAGE}" 인 매체의 tone 은 0 으로 둔다.
- overview: 이번 묶음에서 드러난 업계 보도 지형 3문장. 국내지와 해외지의 관심사가
  어디서 갈렸는지, 어떤 매체가 무엇에 침묵했는지 포함한다.

출력 스키마:
{_schema_example()}"""


# 모듈 로드 시 한 번 만든다. FEEDS 는 런타임에 바뀌지 않는다.
SYSTEM_PROMPT = build_system_prompt()


def render_input(indexed: dict[str, list[Article]]) -> str:
    """매체별 인덱스가 붙은 헤드라인 묶음을 만든다. 이 번호가 인용의 단위다."""
    blocks: list[str] = []
    for key, articles in indexed.items():
        spec = FEEDS_BY_KEY.get(key)
        label = f"{spec.label} ({spec.label_en}, 원문 {spec.lang})" if spec else key
        header = f"## {key} — {label}"
        if not articles:
            blocks.append(f'{header}\n(수집된 기사 없음 — 이 매체는 모든 클러스터에서 "{NO_COVERAGE}")')
            continue
        lines = [header]
        for i, art in enumerate(articles):
            lines.append(f"[{i}] {art.title}")
            if art.summary:
                lines.append(f"    요약: {art.summary}")
            lines.append(f"    시각: {art.published}")
        blocks.append("\n".join(lines))

    return (
        f"아래는 {len(indexed)}개 화장품·뷰티 업계지의 최신 헤드라인이다. 같은 사안을 다룬 "
        "것끼리 묶고, 매체별 프레임 차이를 분석해 지정된 JSON 스키마로만 답하라.\n\n"
        + "\n\n".join(blocks)
    )
