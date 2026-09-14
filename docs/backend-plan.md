# 백엔드 · 모델 서빙 · DB 구축 계획

> 작성 2026-09-14 · 요청: 정선우 님 ("backend, model, db 구축 계획 + 모델을 백엔드에 올려 무료 환경에서 쓸 수 있는지")
> 상태: **계획안(미착수)**. 채택 여부와 범위는 팀 결정 후 `docs/decisions.md`에 기록한다.

## 0. 결론 먼저

1. **무료로 가능하다.** 가격 모델(LightGBM)은 파일이 작고 추론이 가벼워서, Vercel 무료(Hobby) Python 함수와 Supabase 무료 Postgres 조합으로 올릴 수 있다.
2. **다만 지금 화면 기능에는 백엔드가 꼭 필요하지 않다.** 9,160단지의 가격·추정·비교사례는 이미 배치로 미리 계산해 정적 JSON으로 배포하고 있다.
   백엔드는 **미리 계산할 수 없는 기능**(사용자가 넣은 조건으로 즉석 추정, 비교 목록 저장·공유, 데이터 자동 갱신)을 넣을 때 의미가 있다.
3. **대회 제출(9/20)은 지금의 정적 구조를 유지**하고, 백엔드는 별도 브랜치에서 단계적으로 붙이는 것을 권장한다. 백엔드가 실패해도 정적 JSON으로 화면이 계속 동작하게 설계한다.
4. **상용화 시 주의**: Vercel Hobby는 약관상 **비상업·개인 용도만** 허용한다. 상용 서비스로 가면 Pro($20/사용자/월) 또는 다른 호스팅으로 옮겨야 한다.

## 1. 현재 구조와 백엔드가 필요한 지점

```text
[배치 · 로컬 Python]  수집 → 마스터 → 지표 → 가격(30~34) → payload(36) → 문구 검사(37)
[배포 · Vercel]       Next.js 정적 export + front/public/data (index.json + 단지별 JSON 9,160개, 약 72MB)
[런타임]              서버·DB·생성형 AI 호출 없음
```

| 기능 | 정적 JSON으로 가능? | 백엔드 필요 이유 |
|---|---|---|
| 검색·담기·비교·지도 | 가능 (현재) | — |
| cold-start 단지 가격 추정 표시 | 가능 (33.1 미리 계산) | — |
| **사용자가 입력한 면적·층 조건으로 즉석 추정** | 불가 | 조합이 많아 미리 계산 불가 → 모델 추론 API |
| **비교 목록 저장·링크 공유** | 불가 (지금은 브라우저 localStorage만) | 쓰기 저장소(DB) 필요 |
| **실거래 주기 갱신 자동화** | 수동 | 배치 스케줄러 + 적재 대상(DB) |
| 지도 영역(bbox) 조회, 대용량 인덱스 분할 | 부분 가능 | 단지 수가 크게 늘면 API가 유리 |

## 2. 권장 아키텍처 (무료)

```text
브라우저 (Next.js)
  ├─ 정적 JSON (기본 경로, 항상 동작)           ← 기존 그대로
  └─ /api/*  (선택 기능, 실패 시 정적 경로로 대체)
        │
Vercel Python Function (FastAPI)
  ├─ GET  /api/complex/{apt_seq}        DB 조회
  ├─ POST /api/estimate                 baseline-first 라우터 → 필요 시 LightGBM 추론 + 구간
  └─ POST /api/share, GET /api/share/{id}   비교 목록 저장·공유 (로그인 없음)
        │
Supabase Postgres (무료)
  └─ 배치 산출물 적재: complex, metrics, price_cells, price_series, estimates, comparables, regulation, shares

GitHub Actions (무료 러너)
  ├─ 주 1회: 실거래 수집 → 32/33/34/36 재계산 → DB 적재 → 배포   (규제 갱신 워크플로와 같은 방식)
  └─ 주 1회: Supabase 휴면 방지 ping
```

### 2.1 무료 플랜 한도 (2026-09 확인)

| 서비스 | 무료 한도 | 우리 사용량 추정 | 판단 |
|---|---|---|---|
| Vercel Hobby Functions | 월 호출 100만, Active CPU 4시간, 메모리 360 GB-hr, 함수당 최대 2GB/1vCPU·300초, Python 번들 500MB | 추론 1회 수~수십 ms, 의존성(lightgbm+numpy+scipy+pandas) 수백 MB 이하 | 충분. 번들 크기는 스파이크에서 실측 |
| Vercel Hobby 약관 | **비상업·개인 용도만** | 대회 데모는 해당 | 상용화 시 전환 필요 |
| Supabase Free | DB 500MB, 활성 프로젝트 2개, **1주 미사용 시 자동 일시정지**, 백업 없음 | 가공 테이블만 적재 시 수십 MB (§3.1) | 충분. 휴면 방지 ping 필요 |
| GitHub Actions | 공개 저장소 무료 러너(2코어), 작업당 최대 6시간 | 수집·재계산 시간 실측 필요 | cold-start 재학습 시간 확인 필요 |

대안: Render 무료 웹서비스(512MB RAM·0.1 CPU, 15분 미사용 시 잠들고 깨는 데 약 1분 — 데모 첫 요청이 느림), Google Cloud Run 무료 사용량(월 200만 요청·18만 vCPU-초, 결제 계정 등록 필요). 둘 다 가능하지만 프론트와 같은 Vercel 프로젝트에 두는 쪽이 운영이 단순하다.

## 3. DB 설계

### 3.1 적재 대상과 크기

원천 거래 원장(매매 24.7만 행 37MB, 전월세 130만 행 189MB)은 **DB에 넣지 않는다.** 인덱스까지 붙이면 500MB를 위협하고, 화면은 가공 결과만 쓴다.

| 테이블 | 원천 산출물 | 행 수 | 파일 크기 |
|---|---|---:|---:|
| `complex` | `23.1.complex`, `23.2.complex_metrics` | 9,160 | 약 4MB |
| `price_cell` | `32.1.price_cells` | 40,848 | 3.3MB |
| `price_series` | `32.2.price_series` | 137,405 | 5.9MB |
| `estimate` | `33.1.coldstart_estimates` | 6,028 | 수 MB |
| `comparable` | `34.1.comparables` | 121,346 | 21.8MB |
| `regulation` | `35.*` | 소량 | <1MB |
| `share` | 사용자 저장 목록 (신규) | 사용량 비례 | — |

합계 약 40~60MB(인덱스 포함 추정 100MB 이내)로 무료 500MB 안에 들어간다.

### 3.2 원칙

- 기본 키는 기존 계약을 따른다: `apt_seq`, `(apt_seq, area_type, floor_band)`.
- 배치 결과는 **스냅샷 단위로 통째 교체**한다(`as_of` 컬럼 + 트랜잭션 교체). 부분 갱신으로 정적 JSON과 DB가 어긋나지 않게 한다.
- 정적 JSON(36)과 DB 적재는 **같은 산출물 파일에서** 만든다. 진실 원천을 둘로 만들지 않는다.
- 권한: 브라우저는 DB에 직접 쓰지 않는다. 읽기는 API 경유, `share` 쓰기도 API가 검증 후 수행한다. Supabase service key는 API 서버와 Actions secret에만 둔다.

## 4. 모델 서빙 설계

### 4.1 지금 모델은 파일로 저장되지 않는다

`models/price/33.train_coldstart.py`는 학습 직후 같은 프로세스에서 6,028행을 추정해 파일(33.1)로 쓰고 끝난다. 서빙하려면 다음을 **아티팩트로 저장**해야 한다.

- LightGBM booster (`model.save_model`, 텍스트)
- 설계 행렬 재현 정보: feature 목록(SERVICE_MODEL_FEATURES), 법정동 dense key 목록, 범주 인코딩, 전세 지수 기준월
- conformal 구간 분위수(log 상대오차 10/90%)와 신뢰도 강등 규칙(≤20세대 LOW)
- 학습 데이터 기준월·코드 커밋 해시

### 4.2 동등성 검증 (필수 게이트)

API가 같은 입력에 대해 **배치 산출물 33.1과 같은 값**을 내는지 전수 비교한다(허용 오차 1e-6). 한 건이라도 다르면 배포하지 않는다.
feature 생성 코드는 새로 쓰지 않고 `_features.py`, `_jeonse.py`, `_price_router.py`를 그대로 import한다.

### 4.3 `/api/estimate` 동작

1. 입력: `apt_seq`, `area_type`, `floor_band`
2. `_price_router` 규칙 그대로: 실거래 셀 → 같은 면적 다른 층대 실거래 → 단지 평균 → **셋 다 없을 때만** 모델 추정
3. 모델 추정 시 점추정 + 구간 + 신뢰도, 문구 규칙(§ AGENTS.md 6)을 따르는 source 값 반환
4. 응답은 CDN 캐시(같은 입력은 재계산 안 함)

## 5. 단계별 계획

| 단계 | 내용 | 산출물 | 완료 조건 | 예상 |
|---|---|---|---|---|
| P0 스파이크 | Vercel 같은 프로젝트에 Python 함수 1개 배포(정적 export와 공존 가능 여부), lightgbm import 번들 크기·콜드스타트 실측 | 스파이크 브랜치 preview URL, 측정값 | 번들 < 500MB, 콜드스타트·응답시간 기록. 공존 불가면 API 전용 Vercel 프로젝트로 분리 결정 | 0.5일 |
| P1 모델 아티팩트 | 33에 아티팩트 저장 추가, 로더 작성 | `models/price/artifacts/`(크기 확인 후 커밋 여부 결정) | §4.2 동등성 6,028행 전부 일치 | 1일 |
| P2 DB | Supabase 스키마, `38.load_db.py`(스냅샷 교체 적재), 권한 설정 | 스키마 SQL, 적재 스크립트 | 행 수·합계가 산출물과 일치 | 1일 |
| P3 API | FastAPI 엔드포인트 3종, 입력 검증, 캐시 헤더, 테스트 | `api/` | 단위 테스트 + preview에서 호출 확인 | 1일 |
| P4 프론트 연동 | 기능 플래그 뒤에 연동, API 실패 시 정적 JSON 대체 | 프론트 PR (정선우 님 리뷰) | preview에서 API 장애를 흉내 내도 화면 정상 | 1일 |
| P5 자동화 | 주간 갱신 Actions, 휴면 방지 ping | 워크플로 2개 | 수동 실행 1회 성공 | 0.5일 |

합계 약 5일. 9/20 제출 전에는 **P0~P1까지만**(무료 서빙 가능성 실측 + 모델 재현 보장) 하고, P2 이후는 제출 뒤 진행하는 것을 권장한다.

## 6. 역할 제안

| 영역 | 담당 제안 |
|---|---|
| P0 스파이크, P3 API | 김용진 |
| P1 모델 아티팩트·동등성 검증 | 서지은 · 김용진 |
| P2 DB 스키마·적재 | 김용진 (인프라 담당 미정) |
| P4 프론트 연동 | 정선우 |
| P5 자동화 | 미정 |

## 7. 리스크

| 리스크 | 영향 | 대응 |
|---|---|---|
| Vercel Hobby 비상업 조항 | 상용화 시 약관 위반 | 상용 전환 시점에 Pro 또는 Cloud Run 등으로 이전 (API를 컨테이너로도 돌 수 있게 작성) |
| Supabase 1주 미사용 휴면 | 데모 당일 DB 응답 없음 | 휴면 방지 ping + API 실패 시 정적 JSON 대체 |
| Python 함수 콜드스타트 | 첫 추정 요청 지연 | P0에서 실측, 필요 시 import 최소화·응답 캐시 |
| 배치와 API 결과 불일치 | 같은 단지 가격이 화면마다 다름 | §4.2 동등성 게이트, 같은 산출물에서 DB·JSON 생성 |
| 주간 재학습 시간 초과 | Actions 6시간 제한 | 재학습은 월 1회·수동, 주간은 거래 갱신·셀 재계산만 |
| 키 유출 | 공공 API·DB 오남용 | Actions secret·Vercel env만 사용, `NEXT_PUBLIC_`에는 공개키만 |

## 8. 결정이 필요한 것

1. 제출 전 범위: P0~P1만 할지, 즉석 추정 API(P3 일부)까지 데모에 넣을지
2. 공유 기능(`share`)을 넣을지 — 넣으면 개인정보 없는 익명 목록으로 한정
3. 상용화 가정 시 호스팅 전환 시점

## 참고 자료

- Vercel Functions 한도: https://vercel.com/docs/functions/limitations
- Vercel Hobby 플랜: https://vercel.com/docs/plans/hobby
- Vercel FastAPI 배포: https://vercel.com/docs/frameworks/backend/fastapi
- Supabase 무료 한도(2026): https://www.itpathsolutions.com/supabase-free-tier-limits
- Render 무료 티어(2026): https://render.com/articles/platforms-with-a-real-free-tier-for-developers-in-2026
- Cloud Run 가격: https://cloud.google.com/run/pricing
