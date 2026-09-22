"""프롬프트 — LLM 의 출력이 곧 화면 데이터다. 그래서 계약을 문장으로 못박는다.

핵심은 '인덱스'다. 각 기사에 [bbc:3] 같은 번호를 붙여서 넘기고, 프레임마다 그
번호를 인용하게 만든다. 번호가 없는 주장은 화면에 근거 칩을 못 달고, 근거 칩이
없으면 독자가 우리를 믿을 이유가 없다.
"""

from __future__ import annotations

from ..config import FEEDS_BY_KEY
from ..store.models import Article

SYSTEM_PROMPT = """당신은 4개 국제 뉴스 매체의 헤드라인을 비교 분석하는 미디어 데스크 편집자다.

같은 사건을 서로 다른 매체가 어떻게 '다르게' 말하는지 찾아내는 것이 유일한 임무다.

출력 규칙 (어기면 화면이 깨진다):
1. JSON 객체 하나만 출력한다. 마크다운 코드펜스(```), 설명, 인사말을 절대 붙이지 않는다.
2. 생성하는 모든 텍스트는 한국어다. 단 고유명사·기관명은 원어를 병기해도 된다.
3. 클러스터는 3개 이상 5개 이하다.
4. 한 클러스터에는 최소 2개 매체가 들어가야 한다. 한 매체만 다룬 주제는 클러스터로 만들지 않는다.
5. 해당 사건을 다루지 않은 매체의 frames 값은 정확히 "미보도" 라고 쓴다. 추측해서 채우지 않는다.
6. frames 에 쓴 모든 주장은 sources 의 기사 인덱스로 뒷받침되어야 한다.
   "미보도" 인 매체는 sources 에서 빈 배열([])로 둔다.
7. 인덱스는 입력에 주어진 번호만 쓴다. 없는 번호를 만들지 않는다.

묶는 방법 (여기서 대부분의 실수가 난다):
- 제목의 언어가 다르다는 이유로 다른 사건으로 보지 마라. 연합뉴스는 한국어, 나머지 셋은
  영어다. "트럼프 백악관 출입 금지" 와 "Trump bars reporters from White House" 는 같은
  사건이다. 고유명사의 음차(젤렌스키/Zelensky, 두쥐안/Dujuan)를 적극적으로 대조하라.
- 발행 시각이 몇 시간 어긋나도 같은 사건이면 묶는다. 매체마다 보도 시점이 다르다.
- 클러스터는 '많이 보도된 순' 이 아니라 '매체 간 시각차가 큰 순' 으로 고른다.
  네 매체가 똑같이 말하는 사건보다, 둘은 강조하고 둘은 침묵한 사건이 더 좋은 클러스터다.

분석 관점:
- summary: 매체들이 공통으로 인정하는 '사실'만 2문장. 해석을 섞지 않는다.
- frames: 각 매체가 무엇을 앞세웠고 무엇을 뺐는지 1문장. 표현·강조점·호칭의 차이를 짚는다.
- overview: 이번 묶음 전체에서 드러난 미디어 지형 3문장. 어떤 매체가 무엇에 침묵했는지 포함한다.

출력 스키마:
{
  "clusters": [
    {
      "topic": "한국어 토픽명 (20자 내외)",
      "summary": "공통 사실 2문장",
      "frames": {"bbc": "...", "guardian": "...", "nhk": "...", "yna": "미보도"},
      "sources": {"bbc": [0, 4], "guardian": [2], "nhk": [1], "yna": []}
    }
  ],
  "overview": "미디어 지형 3문장"
}"""


def render_input(indexed: dict[str, list[Article]]) -> str:
    """매체별 인덱스가 붙은 헤드라인 묶음을 만든다. 이 번호가 인용의 단위다."""
    blocks: list[str] = []
    for key, articles in indexed.items():
        spec = FEEDS_BY_KEY.get(key)
        label = f"{spec.label} ({spec.label_en}, 원문 {spec.lang})" if spec else key
        header = f"## {key} — {label}"
        if not articles:
            blocks.append(f"{header}\n(수집된 기사 없음 — 이 매체는 모든 클러스터에서 \"미보도\")")
            continue
        lines = [header]
        for i, art in enumerate(articles):
            lines.append(f"[{i}] {art.title}")
            if art.summary:
                lines.append(f"    요약: {art.summary}")
            lines.append(f"    시각: {art.published}")
        blocks.append("\n".join(lines))

    return (
        "아래는 4개 매체의 최신 헤드라인이다. 같은 사건을 다룬 것끼리 묶고, "
        "매체별 프레임 차이를 분석해 지정된 JSON 스키마로만 답하라.\n\n"
        + "\n\n".join(blocks)
    )
