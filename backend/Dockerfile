# syntax=docker/dockerfile:1
#
# Newsroom Lens 단일 이미지 — FastAPI 가 API 와 정적 SPA 를 같은 포트에서 낸다.
#
# 이미지에 굽지 않는 것: Bedrock Bearer 토큰. 런타임에 ECS Secrets 로만 들어온다.
# 빌드 컨텍스트는 저장소 루트다(frontend/ 를 같이 넣어야 하므로):
#   docker build -f backend/Dockerfile -t newsroom-lens .

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FRONTEND_DIR=/app/frontend

WORKDIR /app

# 의존성을 먼저 깐다. 코드가 바뀌어도 이 레이어는 재사용된다.
COPY backend/pyproject.toml /app/backend/pyproject.toml
RUN pip install --no-cache-dir \
        "fastapi>=0.115" \
        "uvicorn[standard]>=0.32" \
        "feedparser>=6.0.11" \
        "httpx>=0.28"

COPY backend/app /app/backend/app
COPY frontend /app/frontend

# 루트로 돌리지 않는다.
RUN useradd --create-home --uid 10001 lens \
    && chown -R lens:lens /app
USER lens

WORKDIR /app/backend
EXPOSE 8000

# 컨테이너 자체 헬스체크. ALB 타깃 그룹도 같은 경로를 본다.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

# --workers 를 쓰지 않는다. 저장소가 프로세스 메모리에 있고 폴러가 프로세스마다
# 하나씩 돌기 때문에, 워커를 늘리면 요청이 어느 워커에 붙었는지에 따라 기사 수가
# 달라 보인다. 수평 확장이 필요하면 태스크 수를 늘리고 저장소를 밖으로 빼야 한다.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
