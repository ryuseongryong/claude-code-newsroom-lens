"""Bedrock Converse REST 호출 — SDK 를 쓰지 않는다.

이유는 보안이다. Bearer 토큰(AWS_BEARER_TOKEN_BEDROCK)만 있으면 SigV4 서명 체인 없이
converse 를 부를 수 있고, 토큰은 런타임 환경변수로만 들어온다. 이미지에는 아무것도
굽히지 않는다.

  POST https://bedrock-runtime.{region}.amazonaws.com/model/{modelId}/converse
  Authorization: Bearer {token}
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from ..config import settings

log = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class LensError(RuntimeError):
    """Lens 생성 실패. 라우터가 502 로 바꿔 내보낸다."""


async def converse(system_prompt: str, user_text: str) -> str:
    """Converse API 를 한 번 호출하고 모델의 텍스트를 그대로 돌려준다."""
    token = settings.bedrock_bearer_token
    if not token:
        raise LensError("AWS_BEARER_TOKEN_BEDROCK 이 설정되지 않았습니다.")

    payload = {
        "system": [{"text": system_prompt}],
        "messages": [{"role": "user", "content": [{"text": user_text}]}],
        "inferenceConfig": {"maxTokens": settings.bedrock_max_tokens, "temperature": 0.2},
    }

    try:
        async with httpx.AsyncClient(timeout=settings.bedrock_timeout_seconds) as client:
            response = await client.post(
                settings.bedrock_converse_url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
            )
    except httpx.TimeoutException as exc:
        # 504 로 올려도 되지만, 프런트 입장에서 원인 구분이 필요한 건 '타임아웃' 그 자체다.
        raise LensError(f"Bedrock 응답 시간 초과({settings.bedrock_timeout_seconds:.0f}초)") from exc
    except httpx.HTTPError as exc:
        raise LensError(f"Bedrock 연결 실패: {type(exc).__name__}") from exc

    if response.status_code != 200:
        # 토큰을 로그에 남기지 않도록 응답 본문만 자른다.
        detail = response.text[:300]
        raise LensError(f"Bedrock HTTP {response.status_code}: {detail}")

    try:
        body = response.json()
        blocks = body["output"]["message"]["content"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise LensError(f"Bedrock 응답 구조가 예상과 다릅니다: {exc}") from exc

    text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict))
    if not text.strip():
        raise LensError(f"Bedrock 이 빈 응답을 반환했습니다 (stopReason={body.get('stopReason')})")

    if body.get("stopReason") == "max_tokens":
        # 잘린 JSON 은 아래 parse_json_object 에서 복구를 시도한다. 로그로 남겨 둔다.
        log.warning("Bedrock 출력이 maxTokens(%d)에서 잘렸습니다", settings.bedrock_max_tokens)

    return text


def parse_json_object(text: str) -> dict[str, Any]:
    """모델 출력에서 JSON 객체를 방어적으로 꺼낸다.

    프롬프트로 '펜스 금지' 를 못박아도 모델은 때때로 ```json 을 붙이고, 앞뒤에 한 줄
    설명을 남긴다. 그때마다 502 를 내면 기능이 도박이 된다. 그래서 세 단계로 내려간다.
      1) 그대로 파싱
      2) 코드펜스를 벗기고 파싱
      3) 첫 '{' 부터 짝이 맞는 '}' 까지 잘라내서 파싱
    """
    candidates = [text, _FENCE_RE.sub("", text.strip())]
    balanced = _extract_balanced_object(text)
    if balanced:
        candidates.append(balanced)

    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise LensError("모델 응답에서 JSON 객체를 찾지 못했습니다.")


def _extract_balanced_object(text: str) -> str | None:
    """중괄호 깊이를 세서 첫 완전한 객체만 잘라낸다. 문자열 안의 괄호는 세지 않는다."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None
