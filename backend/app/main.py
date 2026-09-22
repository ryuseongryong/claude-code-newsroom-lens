"""FastAPI 엔트리포인트 — API 와 정적 SPA 를 같은 :8000 에서 서빙한다.

한 포트에서 같이 나가므로 CORS 설정도, 프런트 빌드 단계도 없다.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import router
from .collector import poll_loop

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
log = logging.getLogger("newsroom-lens")

# 컨테이너에서는 /app/frontend, 로컬에서는 ../frontend.
FRONTEND_DIR = Path(os.getenv("FRONTEND_DIR", Path(__file__).resolve().parents[2] / "frontend"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 첫 수집을 기다리지 않는다. 컨테이너는 즉시 헬스체크에 응답해야 하고,
    # 화면은 빈 상태에서 시작해 120초 주기로 채워지면 된다.
    task = asyncio.create_task(poll_loop(), name="feed-poller")
    log.info("기동 완료 — 프런트엔드: %s", FRONTEND_DIR)
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


app = FastAPI(
    title="Newsroom Lens",
    description="BBC · The Guardian · NHK World · 연합뉴스 — 같은 사건, 네 개의 프레임",
    version="0.1.0",
    lifespan=lifespan,
)

# API 를 먼저 등록한다. 아래 정적 마운트가 "/" 를 통째로 먹기 때문에 순서가 중요하다.
app.include_router(router)

if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
else:  # pragma: no cover - 배포 이미지에서는 항상 존재한다
    log.warning("프런트엔드 디렉터리를 찾지 못했습니다: %s", FRONTEND_DIR)
