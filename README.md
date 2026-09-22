# Newsroom Lens

BBC · The Guardian · NHK WORLD-JAPAN · 연합뉴스 — 네 매체가 같은 세계를 어떻게 다르게 말하는지 비교한다.

**라이브:** https://df4knlft34smf.cloudfront.net/

이 도구가 답하려는 질문은 "무슨 일이 있었나" 가 아니라 **"누가 무엇을 말하지 않았나"** 다.
그래서 화면에서 가장 눈에 띄는 요소가 보도한 매체의 문장이 아니라 **비어 있는 채널**(`미보도`)이다.

### 렌즈 — 같은 사건, 네 개의 프레임

보도한 매체와 침묵한 매체가 **같은 폭**을 차지한다. 흐리게 처리하지 않고 측정 가능한 공백으로 둔다.
각 프레임은 원문 기사로 연결되는 근거 칩을 달고 있고, 근거가 없는 문장은 서버가 걸러 낸다.

![렌즈 보기](docs/img/lens.png)

### 뉴스룸 — 수집 현황

![뉴스룸 보기](docs/img/newsroom.png)

<details>
<summary>어두운 화면</summary>

![렌즈 어두운 화면](docs/img/lens-dark.png)

</details>

```
  브라우저 ──HTTPS──▶ CloudFront ──HTTP+X-Origin-Verify──▶ ALB ──▶ Fargate(:8000)
                                                            │         ├─ FastAPI (API + 정적 SPA)
                                                            │         ├─ 폴러 (120초, 4개 피드)
                                                            │         └─ 인메모리 저장소 (매체별 50건)
                                                            │
                                     SG: CloudFront 프리픽스 리스트만 허용
                                                                      └──REST──▶ Bedrock Converse
```

---

## 빠르게 실행

```bash
cd backend
uv venv --python 3.11 && uv pip install -e '.[dev]'
set -a; source ~/capstone/.env; set +a          # AWS_BEARER_TOKEN_BEDROCK
.venv/bin/uvicorn app.main:app --port 8000
# → http://localhost:8000
```

```bash
cd backend && .venv/bin/python -m pytest -q     # 33 passed
```

컨테이너로:

```bash
docker build -f backend/Dockerfile -t newsroom-lens .   # 빌드 컨텍스트 = 저장소 루트
docker run -p 8000:8000 -e AWS_BEARER_TOKEN_BEDROCK="$AWS_BEARER_TOKEN_BEDROCK" newsroom-lens
```

## 배포

```bash
# 1) 토큰을 Secrets Manager 에 넣는다. CDK 는 이 값을 보지 않는다 (이름만 참조).
aws secretsmanager create-secret --name newsroom-lens/bedrock-bearer-token \
  --secret-string "$AWS_BEARER_TOKEN_BEDROCK" --region ap-northeast-2

# 2) 배포
cd infra && npm install
export CDK_DEFAULT_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
export CDK_DEFAULT_REGION=ap-northeast-2
npx cdk deploy
```

`infra/.origin-verify` 는 X-Origin-Verify 값을 보관한다(gitignore). 지우면 다음 배포에서 새 값이 생기고
CloudFront·ALB 양쪽이 함께 갱신된다.

---

## 데이터 소스

전부 API 키가 필요 없다. URL 은 `backend/app/config.py` 에 있고 **각 항목마다 실측 확인 날짜**가 붙어 있다.

| 매체 | 원문 | 형식 | 확인일 |
|---|---|---|---|
| BBC World | 영어 | RSS (`feeds.bbci.co.uk/news/world/rss.xml`) | 2026-09-22 · 200, 32건 |
| The Guardian World | 영어 | RSS (`theguardian.com/world/rss`) | 2026-09-22 · 200, 45건 |
| NHK WORLD-JAPAN | 영어 | **JSON** (`www3.nhk.or.jp/nhkworld/data/en/news/all.json`) | 2026-09-22 · 200, 405건 |
| 연합뉴스 국제 | 한국어 | RSS (`yna.co.kr/rss/international.xml`) | 2026-09-22 · 200, 120건 |

**NHK 는 RSS 가 아니다.** 2026-09-22 확인 결과 `en/news/rss/all.xml`·`rss.xml`·`feed.xml`·`atom.xml` 이
모두 404 이고 `/nhkworld/en/news/` 는 `/nhkworld/news/` 로 meta-refresh 된다. NHK World 가 RSS 를 내리고
JSON 으로 옮긴 것이다. 공식 프런트엔드가 읽는 JSON 엔드포인트를 `kind="nhk_json"` 어댑터로 붙였다.
`robots.txt` 의 `User-agent: *` 는 `/*/r/` 만 막으므로 이 경로는 허용 범위다.

> NHK 엔드포인트가 내려주는 최신 기사는 2026-09-04 자다(엔드포인트가 그렇게 서빙한다).
> 그래서 라이브 화면에서 NHK 는 대부분의 클러스터에서 실제로 `미보도` 로 나온다 — 버그가 아니라
> 정직한 상태 표시다. 모델도 `overview` 에서 이 사실을 스스로 설명한다.

## API

| | |
|---|---|
| `GET /healthz` | 헬스체크. 피드가 다 죽어도 200 (재시작이 해결책이 아니므로) |
| `GET /api/articles?limit=20` | 매체별 최신 기사 + 매체 상태(건수·마지막 성공·실패 이유) |
| `POST /api/lens?refresh=false` | 클러스터링. 10분 버킷 캐시. Bedrock 실패 시 502 |

## 설계에서 양보하지 않은 것

| 규칙 | 이유 |
|---|---|
| 단일 스키마로 정규화 | XML/JSON, RFC822/epoch 차이가 `normalize.py` 에서 전부 죽는다. 그래야 비교가 가능하다 |
| 매체별 실패 격리 | `gather(return_exceptions=True)` + 매체별 상태. 한 피드가 죽어도 셋은 갱신된다 |
| 실패해도 기존 기사 유지 | 수집 실패는 화면을 비우는 사건이 아니다. 실패 이유만 컬럼에 적는다 |
| link 가 멱등 키 | 같은 기사를 다시 받아도 쌓이지 않는다. 매체별 50건 상한, 오래된 것부터 버린다 |
| 본문 미저장 | 제목·요약(300자)·링크만. 모든 카드가 원문으로 나간다 |
| 근거 없는 프레임은 버린다 | 모델이 인용한 인덱스를 검증하고, 범위를 벗어나면 그 매체를 `미보도` 로 내린다 |
| 2개 매체 미만 클러스터 폐기 | 비교가 성립하지 않는 카드는 화면에 올리지 않는다 |
| 침묵을 표시한다 | `미보도` 는 흐린 글씨가 아니라 보도한 채널과 **같은 폭의 빈 채널**이다 |
| 토큰은 런타임에만 | 이미지·템플릿·저장소 어디에도 없다. ECS Secrets 주입뿐 |

## 실측으로 바로잡은 값

스펙 초안과 다르게 간 곳. 둘 다 코드 주석에 근거를 남겼다.

- **`maxTokens` 1500 → 4000.** 한국어 5개 클러스터 출력이 실측 2,233 토큰이다. 1500 에서는
  `stopReason=max_tokens` 로 JSON 이 잘려 파싱 자체가 불가능했다.
- **매체별 입력 12건 → 25건.** 연합뉴스는 피드에 120건을 싣고 BBC 는 32건이다. 같은 "최신 12건" 을
  자르면 연합뉴스 창은 1시간치·BBC 창은 하루치가 되어 교집합이 구조적으로 사라진다. 그러면 유일한
  한국어 매체가 늘 `미보도` 로 밀리는데, 그때의 `미보도` 는 침묵이 아니라 **창 크기가 만든 거짓말**이다.

## 오리진 보호

ALB 를 두 겹으로 막는다. 한 겹만으로는 부족하다.

1. **보안 그룹** — CloudFront 관리형 프리픽스 리스트(`com.amazonaws.global.cloudfront.origin-facing`,
   조회해서 쓴다)에서 오는 80 만 허용. 인터넷에서 ALB DNS 를 직접 때리는 경로를 TCP 단계에서 끊는다.
2. **X-Origin-Verify 헤더** — 값이 틀리면 리스너 기본 동작이 403. 프리픽스 리스트는 *모든* CloudFront 를
   허용하므로, 남의 배포를 경유하는 경로는 1) 로는 막히지 않는다.

`alb.addListener()` 의 **`open: false` 가 필수**다. 기본값 `true` 는 보안 그룹에 `0.0.0.0/0` 인그레스를
조용히 추가하고, 그러면 1) 의 규칙이 나란히 남아 있으면서도 아무 의미가 없어진다. 코드만 봐서는 보이지
않으니 합성된 템플릿의 `SecurityGroupIngress` 를 직접 확인해야 한다.

검증(2026-09-22):

```
CloudFront  /, /styles.css, /app.js, /api/articles, /healthz  → 200
ALB 직접    헤더 없음 / 틀린 헤더                              → 연결 불가 (SG 가 TCP 차단)
ALB 설정    SG 인그레스 = pl-22a6434b 만 (IpRanges 0개)
            리스너 기본 = 403, priority 10 = X-Origin-Verify 일치 시 forward
```

## 구조

```
backend/app/
  config.py            피드 정의(+확인일) · 런타임 설정
  collector/
    normalize.py       HTML 제거 · 300자 컷 · 날짜 파싱 · RSS/JSON 어댑터
    feeds.py           가져오기 + 파싱. 실패는 전부 FeedError 로 좁힌다
    poller.py          120초 루프. 매체별 격리. 루프는 죽지 않는다
  store/
    models.py          Article · SourceStatus
    memory.py          link 멱등 · 매체별 50건 상한 · 스레드 락
  llm/
    prompts.py         JSON 계약 + 인덱스 인용 + 언어 교차 매칭 지시
    bedrock.py         Converse REST(Bearer) + 3단계 방어적 JSON 파싱
    lens.py            검증 · 10분 버킷 캐시 · 근거 인덱스→링크 해석
  api/routes.py        /healthz · /api/articles · /api/lens
frontend/              index.html · styles.css · app.js (빌드 단계 없음)
infra/                 CDK: CloudFront → ALB → Fargate
tools/shoot.mjs        스크린샷 (arm64 에 Chrome 이 없어 Chromium 을 직접 띄운다)
```
