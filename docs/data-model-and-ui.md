# 데이터 모델 및 화면 설계

> `plan.md`의 스키마·UI 상세.

## 7. 데이터 모델

```sql
complex (                          -- 단지
  apt_seq         TEXT PK,         -- 실거래 API의 aptSeq를 마스터 키로 사용
  name            TEXT,
  bjd_code        TEXT,
  lat, lng        DOUBLE,
  built_year      INT,
  total_households INT,
  building_count  INT,
  far, bcr        DOUBLE,
  parking_per_hh  DOUBLE,
  polygon_matched BOOLEAN,         -- 건물 폴리곤 매칭 성공 여부
  match_confidence TEXT            -- HIGH / MEDIUM / LOW / FAILED
)

building (                         -- 동
  building_id     TEXT PK,
  apt_seq         TEXT FK,
  dong_name       TEXT,            -- 확보 실패 시 NULL 허용
  polygon         GEOMETRY,
  floors          INT,
  ground_elev     DOUBLE
)

horizon_profile (                  -- ★ 핵심 산출물
  building_id     TEXT,
  floor_band      TEXT,            -- LOW / MID / HIGH
  repr_floor      INT,
  obs_height      DOUBLE,
  profile         REAL[72],        -- 5° 간격 앙각
  sun_hours_winter DOUBLE,
  sun_hours_spring DOUBLE,
  view_block_pct  DOUBLE,
  open_angle_mean DOUBLE,
  open_span_max   DOUBLE,
  river_view      BOOLEAN,
  park_view       BOOLEAN,
  mountain_view   BOOLEAN,
  PRIMARY KEY (building_id, floor_band)
)

complex_metrics (                  -- 단지 레벨 집계 (지도 필터링용)
  apt_seq         TEXT PK,
  -- horizon 파생
  sun_hours_avg   DOUBLE,
  sun_hours_best  DOUBLE,
  view_open_avg   DOUBLE,
  river_view_ratio DOUBLE,
  -- 소음원 (중심선 거리)
  road_centerline_m DOUBLE,
  rail_centerline_m DOUBLE,
  -- 교통 (§6.8.6)
  station_dist_m  DOUBLE,
  station_elev_diff DOUBLE,
  station_walk_min_est DOUBLE,   -- 추정치임을 이름에 명시
  station_ridership_daily INT,   -- 최근접 역 일평균 승하차
  traffic_weekday INT,           -- NULL 허용: 300m 내 관측지점 없으면 결측
  traffic_weekend INT,           -- NULL 허용
  -- 교육 (§6.8.1)
  elem_school_m   DOUBLE,
  elem_safe_route BOOLEAN,       -- 초품아: 300m 이내 + 주간선도로 미횡단
  mid_school_m    DOUBLE,
  high_school_m   DOUBLE,
  daycare_500m    INT,
  -- 의료 (§6.8.2)
  tertiary_hosp_m DOUBLE,        -- 상급종합
  general_hosp_m  DOUBLE,        -- 종합병원
  clinic_1km      INT,
  pediatric_1km   INT,
  -- 생활 편의 (§6.8.3)
  mart_m          DOUBLE,
  dept_store_m    DOUBLE,
  supermarket_m   DOUBLE,
  cvs_500m        INT,
  restaurant_500m INT,
  park_m          DOUBLE,
  park_area_m2    DOUBLE,
  -- 야간 상권 (§6.8.5) — 중립 표기
  nightlife_300m  INT,
  -- 새벽배송 (§6.8.4) — 서울 전역 TRUE. 필터 아님, 배지용
  dawn_delivery   BOOLEAN
)

transaction (
  tx_id           TEXT PK,
  apt_seq         TEXT FK,
  deal_type       TEXT,            -- SALE / JEONSE
  contract_ym     TEXT,
  price           BIGINT,
  area            DOUBLE,
  area_type       DOUBLE,          -- 정규화된 면적 타입
  floor           INT,
  is_cancelled    BOOLEAN,
  is_outlier      BOOLEAN
)

prediction (                       -- 예측 단위는 floor_band. §6.5와 통일
  apt_seq         TEXT,
  area_type       DOUBLE,
  floor_band      TEXT,
  pred_price_per_area DOUBLE,
  pred_lower, pred_upper DOUBLE,   -- 예측 구간
  PRIMARY KEY (apt_seq, area_type, floor_band)
)
```

### 7.1 규모 추정

| 항목 | 추정 |
|---|---|
| 서울 아파트 단지 | ~4,000 |
| 동(건물) | ~40,000 |
| `horizon_profile` | 40,000 × 3 = **120,000행** |
| profile 배열 | 120,000 × 72 × 4byte ≈ **35MB** |
| `transaction` (5년) | ~50만행 |
| 총 | Supabase 무료 500MB 내 |

### 7.2 지도 전송량 (v1 누락분)

4,000개 마커를 한 번에 내려보내면 초기 로딩이 무너진다.

- 지도 초기 로드: 현재 뷰포트 내 단지만 조회 (`bbox` 쿼리)
- 줌 아웃 시: 행정동 단위 클러스터 집계값만 전송
- 마커 페이로드는 `apt_seq, lat, lng, score` 4필드로 제한 (단지당 ~40byte)
- 필터 변경 시 재조회가 아니라 **클라이언트 측 재계산** (점수 구성 요소를 미리 내려둠)

---

## 8. 화면 설계

### 8.1 화면 목록

| # | 화면 | 목적 | 우선순위 |
|---|---|---|---|
| S1 | 지도 + 필터 + **조건 완화 추천** | **차별점의 실체.** 조건 걸고 서울 전체 정렬 | **최상** |
| S2 | 단지 리포트 | 일조·조망·근접도·시세 | **최상** |
| S3 | 근거 상세 | 계산식·가정·출처·한계 | **상** |
| S4 | 가중치 설정 | 프리셋 + 슬라이더 | 상 |
| S5 | 단지 비교 | 2~3개 대조 | 중 (Layer 3) |

### 8.2 S1 — 지도 + 필터 (핵심 화면)

```
┌──────────────────────────────────────┐
│ 임앤장          [프리셋 ▾]  [검색 🔍]│
├──────────────────────────────────────┤
│ 겨울 일조   [5시간 이상      ▾]      │
│ 조망 개방   [상위 30%        ▾]      │
│ 지하철      [추정 도보 10분  ▾]      │  ← "추정" 명시
│ 매매가      [────●───── 10억]        │
│ ─── 더보기 ▾ ────────────────────    │
│ 초등학교    [초품아만        ▾]      │
│ 병원        [종합병원 2km내  ▾]      │
│ 마트        [1km 이내        ▾]      │
│ 건폐율      [20% 이하        ▾]      │
│ 야간상권    [적은 곳 / 무관   ▾]      │
│                                      │
│        조건 만족  27개 단지           │  ← 즉시 갱신
├──────────────────────────────────────┤
│                                      │
│      (카카오맵 · 서울 전역)          │
│   ● ● ●  색상=점수  크기=세대수      │
│   ○ 회색 = 폴리곤 매칭 실패(제외)     │
│                                      │
├──────────────────────────────────────┤
│  1. ○○아파트    일조5.8h  개방72%    │  ← 정렬된 리스트
│  2. △△단지      일조5.4h  개방68%    │
└──────────────────────────────────────┘
```

**결과가 0개일 때 (§6.7 조건 완화 추천)**

```
┌──────────────────────────────────────┐
│        조건 만족  0개 단지            │
├──────────────────────────────────────┤
│  조금만 양보하면 후보가 생깁니다      │
│                                      │
│  ▸ 일조 5.0h → 4.5h        +8개  [적용]│
│  ▸ 도보 10분 → 12분        +11개 [적용]│
│  ▸ 매매가 10억 → 10.8억     +6개  [적용]│
│  ▸ 일조 4.5h + 도보 12분   +23개 [적용]│
└──────────────────────────────────────┘
```

**이 화면이 발표 첫 30초다.** 조건을 바꾸면 지도가 즉시 다시 칠해지고 개수가 변하는 것 — 이것이 경쟁사가 못 하는 것의 시각적 증거다. 그리고 0개일 때 완화 추천이 뜨는 순간이 두 번째 임팩트다.

### 8.3 S2 — 단지 리포트

```
┌──────────────────────────────────────┐
│ ← 래미안○○ 1단지          종합 82점  │
├──────────────────────────────────────┤
│ [동 101 ▾]  [층 고층 ▾]              │
│ ※ 향은 시나리오입니다  ⓘ             │
├──────────────────────────────────────┤
│ ☀ 겨울 일조   5.8시간  ●●●●○         │
│    남향 가정 시 6.4h / 북향 2.1h      │
│ 🏞 조망 개방   차폐 18%  ●●●●●        │
│    [스카이라인 폴라 차트]             │
│    북동 방향 한강 시야 트임           │
│ 🔊 소음원     간선도로 중심선 45m     │
│ 🚇 지하철     직선 480m, 표고차 +12m  │
│    추정 도보 9분 · 역 승하차 4.2만/일 │
│    인접도로 교통량 평일 2.1만/주말 1.4만│
├──────────────────────────────────────┤
│ 🏫 교육                               │
│    초등학교 210m · 큰길 안 건넘 ✅     │
│    중학교 640m · 어린이집 500m내 4곳  │
│ 🏥 의료                               │
│    종합병원 1.4km · 상급종합 3.8km    │
│    1km내 의원 32곳(소아과 3)          │
│ 🛒 생활                               │
│    대형마트 620m · 백화점 2.1km       │
│    편의점 500m내 7곳 · 공원 340m      │
│    새벽배송 가능 (컬리·쿠팡) 🏷        │
│ 🌃 야간상권   300m내 주점 4곳 (낮음)   │
├──────────────────────────────────────┤
│ 🏗 단지 밀도                          │
│    건폐율 18% · 용적률 219%           │
│    세대당 주차 1.4대 · 대지지분 14평  │
├──────────────────────────────────────┤
│ 💰 시세 (단지 기준)                   │
│    84㎡ 최근 거래  13.8억 ~ 14.6억    │
│    최근 6개월 거래 7건                │
│    전세가율 54%                       │
│    모델 예상범위 13.9 ~ 14.8억        │
├──────────────────────────────────────┤
│ ⚠ 이 앱이 알 수 없는 것               │
│    실내 상태 / 관리 수준 / 실제 소음  │
│    이웃 / 주차 체감 / 세대 창 위치    │
├──────────────────────────────────────┤
│ [모든 수치의 계산 근거 보기]          │
└──────────────────────────────────────┘
```

### 8.4 UX 원칙

1. **로그인 없음.** 지도가 바로 뜨고 색이 칠해져 있다
2. **모든 숫자 클릭 가능** → 계산식·가정·출처가 나온다
3. **모바일 우선.** 지도는 하단 시트, 리포트는 세로 스크롤 한 줄
4. **로딩 없음.** 전부 사전 계산
5. **한계를 화면 안에 넣는다** (§3.4 표를 그대로)
6. **데이터 없는 단지 처리**: "연산 중"이 아니라 **지도에서 회색 마커 + "이 단지는 건물 폴리곤 매칭에 실패해 일조·조망을 계산하지 못했습니다"** 로 사유를 밝힌다. 미완성으로 보이지 않게 하는 것보다 정직한 편이 낫다

---

