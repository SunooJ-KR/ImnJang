# 임앤장 비교 서비스 구현 계획

> **작업자 안내**: 각 Task는 독립적으로 테스트 가능한 산출물 하나를 만든다. 체크박스로 진행을 추적한다.
> 코드는 codex terra가 쓰고, 추론이 필요한 검증은 codex sol이 맡는다.

**목표**: 사용자가 고른 아파트 2~4곳을 같은 기준으로 비교하는 정적 웹앱을 배포한다.

**구조**: 모든 계산을 빌드 타임에 끝내고 정적 JSON으로 굽는다. 런타임 서버가 없으므로 운영비가 0이고, 심사 기간 내내 무료 티어로 접속을 유지할 수 있다. 런타임 생성형 AI 호출은 0회다.

**기술 스택**: Python 3.13 (pandas · geopandas · statsmodels · LightGBM) / 정적 HTML+JS (프레임워크 없음) / Kakao Maps JS SDK / Vercel 정적 배포

**스펙**: [`docs/product-identity.md`](product-identity.md) — 이 계획은 그 문서를 구현한다. 실행자는 둘 다 읽어야 한다.

---

## 전역 제약 (모든 Task에 적용)

스펙 §3·§6·§8에서 그대로 옮긴다.

- **런타임 생성형 AI 호출 0회.** 개발 도구로서의 AI는 자유
- **무료 티어 내 운영 계획.** 유료 API·유료 티어 금지
- **결측을 0이나 False로 채우지 않는다.** "값이 없음"과 "0임"은 다른 상태다
- **금지 표현**: "적정시세" / "저평가" / "고평가" / "일조는 가격에 반영되지 않습니다" / "도보 N분"(→ "추정 도보 N분") / "서울 전역 완전 검색"
- **모델이 쓰인 값은 화면에서 "추정"으로 라벨이 달라야 한다.** 실거래와 섞지 않는다
- 모든 스크립트: `work_dir = Path(__file__).resolve().parents[2]`, 한국어 주석, 헤더에 Author/Purpose/Description, 끝에 `===== N. 자체 검증 =====` 절과 `assert`
- 실행 인터프리터는 `.venv/bin/python` 하나로 통일한다
- API 키는 `mask_key()`로 마스킹한다. 평문 노출 이력이 있다

---

## 현재 구현물 — 사용 / 폐기 구분

### 그대로 쓴다

| 파일 | 산출물 | 새 정체성에서의 역할 |
|---|---|---|
| `data/collect/11` | 매매 247,363 / 전월세 1,306,718 | 가격 화면의 원천. 5년 추이 그래프 |
| `data/collect/12` `24` `25` `27` `28` | OSM 건물·POI·도로·공원·학교·병원·수계·산 | 환경 지표의 원천 |
| `data/collect/19` | 건축물대장 표제부 | 용적률·건폐율·주차 |
| `data/collect/31` `35` | 정비사업 496건 · 토지거래허가구역 11건 | 재건축 단지 150개 · 규제 표시. 둘 다 주기 갱신 가능 |
| `data/master/14` `15` | 지오코딩 99.96%, 동 배정 | 물리 시뮬레이션의 전제 |
| `data/master/23` | `complex` `complex_metrics` `horizon_profile` | **비교서의 환경 축 전체** |
| `models/horizon/21` `22` `26` `29` | 일조·조망·접근성·교통·조망대상 | 우리 고유 자산 |
| `models/price/30` | hedonic 스파이크 | 설계 근거. 재실행 가능해야 한다 |

### 역할이 바뀐다

| 자산 | 이전 | 이후 |
|---|---|---|
| 지도 + 필터 정렬 | 제품의 약속 | S1에서 **단지를 찾는 보조 수단**. 정렬 순위를 제품 주장으로 쓰지 않는다 |
| `sun_hours_avg` 정렬 축 | 1차 정렬 기준 | 비교서의 환경 항목 중 하나 |
| `match_confidence` | 지도 마커 회색 처리 | 비교서에서 "이 단지는 분석 불가" 표기 |

### 폐기한다

| 대상 | 사유 |
|---|---|
| `data/archive/01~09` | 강남구 프로토타입. 이미 archive |
| SHAP 기여도 분해 | 입지 feature 상관이 높아 attribution이 배경 설정에 좌우된다 (스펙 §8) |
| "실거주 적합도 종합 점수" (`algorithms.md` §6.6) | 종합 판정은 스펙 §3에서 하지 않기로 했다 |
| `traffic_weekday` `traffic_weekend` | 서울 관측 지점 139개, 300m 커버리지 4.6% |
| `station_elev_diff` `dawn_delivery` | 소스 없음 |
| `daycare_500m` | 키 미확보. 구현하되 결측으로 둔다 |
| `mountain_view`의 **가격 설명 용도** | 부호 역전(−10.7%). 화면 표시는 유지, 가격 문장에는 쓰지 않는다 |

---

## 파일 구조

```
models/price/
  32.build_price_cells.py     단지×면적타입×층대 가격 셀 + 5년 시계열
  33.train_coldstart.py       cold-start 추정 모델 학습·추론 (2,318단지)
  34.build_comparables.py     비교사례 retrieval
front/
  build/36.build_payload.py   정적 JSON 생성
  public/index.html           단일 페이지 (S1·S2·S3)
  public/app.js               검색·담기·비교 로직
  public/styles.css
  public/data/index.json      검색 인덱스
  public/data/complex/*.json  단지 상세 9,160개
  vercel.json
```

각 파일은 책임이 하나다. 32는 집계만, 33은 모델만, 34는 검색만, 36은 직렬화만 한다.

---

## Task 1 — 가격 셀 집계

**파일**
- 생성: `models/price/32.build_price_cells.py`
- 입력: `output/11.1.trades_sale.txt`, `output/23.1.complex.txt`
- 출력: `output/32.1.price_cells.txt`, `output/32.2.price_series.txt`

**인터페이스**
- 생산: `32.1.price_cells` = `apt_seq, area_type, floor_band, n_trades_24m, last_deal_ym, last_price_manwon, last_price_per_m2, mean_price_per_m2_24m, area_last_floor_band, price_source`
  `price_source` ∈ `{CELL_LAST, AREA_LAST, COMPLEX_MEAN, MODEL}` — `MODEL`은 Task 3이 채운다
- 생산: `32.2.price_series` = `apt_seq, area_type, deal_ym, n_trades, median_price_per_m2` (최근 60개월)

**정의 (스펙 §5.1과 일치시킬 것)**
- `area_type = round(전용면적 / 3) * 3` — 3m bin. 90개 타입, 단지당 중앙값 2개
- `floor_band`: `models/price/_floor_band.py` 공통 함수로 실제 최고층을 기준으로 LOW/MID/HIGH를 나눈다. `horizon_profile`의 `repr_floor`를 최고층 proxy로 쓰지 않는다.
- 셀 거래가 없고 같은 `apt_seq×area_type`의 최근 24개월 거래가 있으면, `deal_date, source_order` 마지막 거래를 `AREA_LAST`로 사용한다. `last_*`에는 그 실제 거래를, `area_last_floor_band`에는 근거 층대를 넣는다. `mean_price_per_m2_24m`은 평균 열의 의미를 지키기 위해 비운다.
- 정제: `is_cancelled=True` 제외, 단지×면적타입 기준 상하위 1% 제외

- [ ] **Step 1** — 32를 작성한다. 셀 집계와 시계열 두 산출물을 만든다.
- [ ] **Step 2** — 자체 검증을 넣는다.
  - `apt_seq × area_type × floor_band` 키 유일
  - `price_source`가 `CELL_LAST`인 행은 `last_price_manwon`이 결측이 아님
  - `32.2`의 `deal_ym`이 전부 최근 60개월 안
  - 가격/㎡ 가 548 ~ 4,903 만원 범위 밖인 행 수를 출력 (30 스파이크의 train cutoff)
- [ ] **Step 3** — 실행. `.venv/bin/python models/price/32.build_price_cells.py`. 자체 검증 전부 PASS.
- [ ] **Step 4** — 커밋. `git add models/price/32.build_price_cells.py output/32.*.txt`

---

## Task 2 — cold-start 추정 모델

**파일**
- 생성: `models/price/33.train_coldstart.py`
- 입력: `output/32.1.price_cells.txt`, `output/23.1~23.3`, `output/31.1.complex_redevelop.txt`
- 출력: `output/33.1.coldstart_estimates.txt`, `output/33.2.coldstart_metrics.txt`, `output/33.3.excluded_complexes.txt`

**인터페이스**
- 소비: Task 1의 `32.1.price_cells`
- 생산: `33.1.coldstart_estimates` = `apt_seq, area_type, floor_band, est_price_per_m2, est_low, est_high, est_confidence, est_note` (`est_note=NEW_BUILD_NO_SALE`만 신축·매매 이력 없음 표시)
- 생산: `33.3.excluded_complexes` = `apt_seq, name, exclude_reason, matched_keyword, sale_5y_n, rent_5y_n, jeonse_share, built_year, total_households, rule`. `exclude_reason`은 `RENTAL_ONLY` 또는 `NO_SALE_5Y`, 후자는 결정 68 R1/R2와 그 근거 열을 기록한다.

**대상**: 최근 24개월 매매 거래가 **하나도 없는 단지 2,318개**. 거래가 있는 6,842단지에는 모델을 쓰지 않는다.

**전세를 쓴다** (스펙 §5.5). 이 단지들 중 1,290개는 매매만 없을 뿐 전세 거래가 있다. 전세로 면적타입을 찾아 추정 대상이 2,315단지로 늘고, 전세 보증금을 feature로 넣어 단지 홀드아웃 MAPE가 15.58% → 9.59%로 떨어진다. 반전세는 연 5% 전환율로 환산한다.

**feature**: `SERVICE_MODEL_FEATURES`와 정비사업 signal, 채택한 전세 feature를 쓴다. 물리 feature는 서비스 가격 모델에서 제외하며, 스파이크 M3 전체 feature를 그대로 옮기지 않는다.

**구간 추정**: 학습 잔차의 분위수로 80% 구간을 만든다. 잔차 분위수는 **학습 기간에서만** 계산한다.

- [ ] **Step 1** — 33을 작성한다. 30의 feature 구성 코드를 재사용하되 복사하지 말고 import 하거나 공통 함수로 뽑는다.
- [ ] **Step 2** — 검증 설계를 30과 동일하게 맞춘다. 학습 2024-09~2026-02 / 검증 2026-03~2026-08. 이상치 컷오프는 학습 기간 가격으로만 계산한다.
- [ ] **Step 3** — 자체 검증.
  - **거래가 있는 단지에 추정치를 만들지 않았는가** (2,318단지에만 행이 존재)
  - 80% 구간의 실제 coverage가 검증 기간에서 72~88% 안에 드는가 — 벗어나면 구간이 거짓말이다
  - 추정치가 전부 양수이고 가격/㎡ 범위 안인가
  - `est_confidence`가 물리 지표 유무에 따라 갈리는가 (커버리지 70.4%)
- [ ] **Step 4** — 실행하고 `33.2.coldstart_metrics.txt`에 MAPE·coverage·표본 크기를 남긴다.
- [ ] **Step 5** — **sol에게 구간 신뢰성 검증을 맡긴다.** "coverage가 명목 80%와 맞는가, 표본이 작은 셀에서 구간이 과도하게 좁지 않은가"를 묻는다.
- [ ] **Step 6** — 커밋.

---

## Task 3 — 비교사례 retrieval

**파일**
- 생성: `models/price/34.build_comparables.py`
- 출력: `output/34.1.comparables.txt`

**인터페이스**
- 입력 대상: `32.1.price_cells`와 `33.1.coldstart_estimates`의 `apt_seq × area_type`. `target_source`는 각각 `CELL`, `COLDSTART`로 기록한다. 후보는 최근 24개월 매매 거래가 있는 32.1 단지로만 제한한다.
- 생산: `34.1.comparables` = `apt_seq, area_type, target_source, rank, comp_apt_seq, comp_name, comp_deal_ym, comp_price_manwon, comp_price_per_m2, adj_price_per_m2, adj_reason, dist_m`

**선정 순서 (스펙 §7). 이 순서를 바꾸지 말 것.**
1. 면적타입 동일 (±3m 이내)
2. 준공년도 ±5년
3. 총세대수 0.5~2배
4. 반경 3km 이내
5. 위 조건을 통과한 후보 중 환경 feature 유사도 상위

**보정**: 거래 시점을 기준일(최근 월)로 맞춘다. 행정동×월 실거래 지수가 아니라 **자치구×월 지수**를 쓴다 — 행정동은 월 거래가 너무 적어 지수가 튄다.

**개수를 채우지 않는다.** 조건을 통과한 후보가 2곳이면 2곳만 남긴다. 최대 10곳.
**보정폭이 20%를 넘는 사례는 제외한다.**

- [ ] **Step 1** — 34를 작성한다.
- [ ] **Step 2** — 자체 검증.
  - 비교사례가 0곳인 단지 수를 출력한다 (0곳도 정상 상태다)
  - `adj_price_per_m2`와 `comp_price_per_m2`의 차이가 전부 20% 이내
  - `comp_apt_seq != apt_seq` (자기 자신을 비교사례로 넣지 않음)
  - 비교사례 수 분포 (중앙값·0곳 비율)
  - `33.1` cold-start 단지 중 비교사례가 1곳 이상인 단지 비율을 출력하고, 0%이면 실패한다
  - `comp_apt_seq`가 모두 32.1 가격 셀 단지인지 확인한다
- [ ] **Step 3** — 실행.
- [ ] **Step 4** — 커밋.

---

## Task 4 — 정적 payload 생성

**파일**
- 생성: `front/build/36.build_payload.py`
- 출력: `front/public/data/index.json`, `front/public/data/complex/{aptSeq}.json`

**인터페이스**
- 소비: Task 1~3의 산출물 + `23.1~23.3` + `31.1`

**`index.json`** (검색용. 경량 유지가 목적이다)
```json
{"complexes": [{"id": "11680-1", "n": "래미안대치팰리스", "g": "강남구", "u": "대치동",
                "lat": 37.5, "lng": 127.05, "y": 2015, "h": 1608}]}
```
키를 1글자로 줄인다. 9,160개 × 약 80B ≈ 750KB, gzip 후 200KB 내외를 목표로 한다.

**`complex/{aptSeq}.json`**
```json
{"id": "...", "name": "...", "built_year": 2015, "households": 1608,
 "match_confidence": "HIGH",
 "env": {"sun_hours_avg": 6.1, "view_open_avg": 92.3, "river_view_ratio": 0.4,
         "station_dist_m": 320, "station_walk_min_est": 6, "elem_school_m": 210, "...": null},
 "floors": [{"band": "HIGH", "repr_floor": 22, "sun_hours_winter": 6.1,
             "river_view": true, "park_view": false, "mountain_view": null}],
 "price": [{"area_type": 84, "floor_band": "MID", "source": "CELL_LAST",
            "last_deal_ym": "2026-07", "last_price_manwon": 142000,
            "est_low": null, "est_high": null}],
 "series": [{"area_type": 84, "points": [["2021-09", 1180], ["2021-10", 1195]]}],
 "comparables": [{"name": "...", "deal_ym": "2026-05", "price_manwon": 138000,
                  "adj_price_per_m2": 1640, "adj_reason": "2026-09 기준 보정", "dist_m": 820}],
 "redevelop": {"type": "공동주택재건축", "stage": "조합설립"},
 "unknowns": ["향", "호수", "실내 상태", "소음 실측"]}
```

`null`은 "값 없음"이다. 프론트는 이를 0으로 렌더링하면 안 된다.

- [ ] **Step 1** — 36을 작성한다.
- [ ] **Step 2** — 자체 검증.
  - 생성된 파일 수가 9,160개
  - `index.json` gzip 크기가 500KB 이하
  - 단지 JSON 중앙값 크기가 20KB 이하
  - 무작위 20개를 다시 읽어 `23.2`의 원값과 일치하는지 대조
  - `price[].source == "MODEL"` 인 행은 `est_low`/`est_high`가 결측이 아님
- [ ] **Step 3** — 실행.
- [ ] **Step 4** — 커밋. 산출물은 `.gitignore` 대상인지 판단해 크기를 확인하고 결정한다.

---

## Task 5 — 프론트 뼈대와 S1 검색

**파일**
- 생성: `front/public/index.html`, `front/public/app.js`, `front/public/styles.css`

**요구**
- 모바일 우선 반응형. 폭 400px에서 가로 스크롤이 없어야 한다
- Kakao Maps JS SDK. JS 키는 도메인 등록이 필요하므로 **로컬에서는 지도 없이도 검색이 동작해야 한다**
- 검색: `index.json`을 한 번 받아 클라이언트에서 단지명·동명으로 필터. 서버 호출 없음

- [ ] **Step 1** — `index.html`에 세 영역을 만든다: 검색창, 결과 목록, 담은 목록(하단 고정).
- [ ] **Step 2** — `app.js`에서 `index.json`을 fetch해 검색을 구현한다. 초성 검색은 하지 않는다(YAGNI).
- [ ] **Step 3** — 검색 결과에서 "비교에 담기" 버튼. 담은 목록은 `localStorage`에 저장하고 최대 4곳.
- [ ] **Step 4** — 브라우저에서 확인. "래미안"으로 검색해 결과가 나오고, 담기가 동작하고, 새로고침해도 담은 목록이 유지되는지.
- [ ] **Step 5** — 커밋.

---

## Task 6 — S3 비교서

**파일**
- 수정: `front/public/app.js`, `front/public/styles.css`

**세 블록을 이 순서로 그린다.**

1. **가격** — 단지별로 면적타입 선택 → 최근 실거래를 크게. 5년 추이는 인라인 SVG 스파크라인(차트 라이브러리 금지, CSP·용량 때문)
2. **환경** — 일조·조망·역·학교·병원·상권을 같은 축에 나란히. 값이 `null`이면 "정보 없음"
3. **현장 확인** — `unknowns` 배열을 체크리스트로

**표기 규칙 구현 (스펙 §6). 이걸 어기면 기능이 아니라 거짓말이 된다.**
- `source == "CELL_LAST"` → "2026년 7월 실거래 14.2억"
- `source == "AREA_LAST"` → "이 층대 최근 2년 매매 거래 없음 · 같은 면적 저층 2026년 7월 실거래 14.2억"
- `source == "COMPLEX_MEAN"` → "이 면적·층대의 최근 2년 매매 거래 없음 · 단지 평균 13.8억"
- `source == "MODEL"` → "추정 13.5~15.1억 · 최근 2년 매매 거래가 없어 추정했습니다" + "원단가 …만원/㎡~…만원/㎡" + 신뢰도 배지
- `source == "EXCLUDED"` → "임대 관련 명칭으로 추정 대상에서 제외했습니다"
- `match_confidence == "FAILED"` → 환경 블록에 "건물 매칭 실패로 일조·조망 분석 불가"
- 도보 시간은 반드시 "추정 도보 N분"

- [ ] **Step 1** — 비교서 레이아웃. 2~4열, 400px에서는 세로 스택.
- [ ] **Step 2** — 가격 블록. `source`별 분기를 위 문구 그대로 구현한다.
- [ ] **Step 3** — 스파크라인. `<svg>` `<polyline>` 하나면 된다.
- [ ] **Step 4** — 환경 블록. `null` 처리를 먼저 짜고 값 렌더링을 나중에 붙인다.
- [ ] **Step 5** — 현장 확인 블록.
- [ ] **Step 6** — **표기 규칙 자동 검사.** `front/build/37.check_wording.py`를 만들어 `app.js`와 `index.html`에서 금지 표현(`적정시세`·`저평가`·`고평가`·`도보 N분` 패턴)을 grep하고 검출되면 실패시킨다. 사람 눈에 의존하지 않는다.
- [ ] **Step 7** — 커밋.

---

## Task 7 — 지도와 배포

- [ ] **Step 1** — Kakao Maps 도메인 등록. **사용자 작업이다.** 배포 URL이 정해진 뒤에 해야 하므로 Step 2 다음이다.
- [ ] **Step 2** — Vercel 정적 배포. `vercel.json`에 `front/public`을 루트로 지정한다.
- [ ] **Step 3** — 배포 URL로 Kakao 도메인 등록 → 지도 마커 렌더링 확인.
- [ ] **Step 4** — 모바일 실기기에서 확인. 400px 가로 스크롤 없음, 검색 → 담기 → 비교 동선 완주.
- [ ] **Step 5** — 커밋.

---

## Task 8 — 문서 정합성

**이 Task는 Task 1~7이 끝난 뒤에 한다.** 구현이 바뀌면 문서도 다시 바뀐다.

| 파일 | 할 일 |
|---|---|
| `plan.md` | 제품 정의를 `product-identity.md` 참조로 바꾸고 Layer 구조를 갱신 |
| `docs/project-charter.md` | §1 배경, §2 목표, §4 차별화, D9 일정, 역할 분담 갱신 |
| `docs/data-contract.md` | §2에 신규 컬럼(조망 4종·춘분·초품아·정비사업) 추가, §4 정렬 축 절 삭제, §6에 가격 표기 규칙 추가 |
| `docs/algorithms.md` | §6.5 시세 모델을 baseline-first로 교체, §6.6 종합 점수 폐기 표기 |
| `docs/pitch-and-roadmap.md` | §13 심사 대응을 스펙 §8의 세 숫자로 교체, SHAP 삭제 |
| `docs/decisions.md` | 46번부터 이번 세션 결정 추가 |

- [x] **Step 1** — 위 6개 파일을 수정한다. (2026-09-12 완료)
- [ ] **Step 2** — `grep -rn "적정시세\|저평가\|살기 좋은 순서\|임장 대체" docs/ plan.md` 로 잔존 표현을 확인한다. 0건이어야 한다.
- [ ] **Step 3** — 커밋.

---

## 일정

| 날짜 | Task | 완료 기준 |
|---|---|---|
| 09-11 (목) | 1, 2 | 가격 셀과 cold-start 추정치 생성, 구간 coverage 검증 통과 |
| 09-12 (금) | 3, 4 | 비교사례와 정적 payload 생성 |
| 09-13~14 (토·일) | 5, 6 | 검색·담기·비교서 동작 |
| 09-15 (월) | 7 | 배포 URL 확보, 지도 렌더링 |
| 09-16~17 (화·수) | 실사용 점검 | 실제 탐색자에게 후보 비교를 시켜보고 오류 수정 |
| 09-18 (목) | **참가 접수 마감** | 접수 완료 |
| 09-19 (금) | 8, 데모 녹화 | 문서 정합성, 발표 자료 |
| 09-20 (토) | 제출 | 접속 확인 |

**버퍼가 없다.** Task 2의 구간 검증이 실패하면 cold-start 추정을 통째로 빼고 "이 단지는 최근 거래가 없습니다"만 표시한다. 제품은 그래도 성립한다.

---

## 알려진 위험

| 위험 | 대응 |
|---|---|
| cold-start 구간 coverage가 명목 80%와 안 맞음 | 추정 표시를 빼고 "거래 없음"만 표시. Task 2 Step 5에서 조기에 판단 |
| Kakao 도메인 등록 지연 | 지도 없이도 검색·비교가 동작하도록 Task 5에서 분리 설계 |
| `is_redevelop` ΔR² +0.054가 150단지에서 나옴 | 극단값 소수를 외운 것일 수 있다. 화면에는 "재건축 추진 중"만 표기하고 가격 프리미엄 수치는 말하지 않는다 |
| 단지 JSON 9,160개의 배포 용량 | Task 4 Step 2에서 크기를 재고, 초과하면 시계열을 분기 집계로 줄인다 |
| 물리 지표 커버리지 70.4% | 비교 대상 중 일부가 환경 분석 불가일 수 있다. 스펙대로 표기하고 숨기지 않는다 |
