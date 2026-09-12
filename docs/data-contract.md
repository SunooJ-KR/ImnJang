# 데이터 계약 — 프론트엔드용

> 프론트(`front/`)가 소비하는 테이블의 실제 상태를 기록한다.
> 스키마 정의는 [`data-model-and-ui.md`](data-model-and-ui.md) §7이고, 이 문서는 **그 스키마 중 실제로 채워진 것이 무엇인지**를 말한다.
> 갱신: 2026-09-12. 제품 정의의 정본은 [`product-identity.md`](product-identity.md)다.

---

## 1. 파일과 규모

| 파일 | 테이블 | 행 수 | 키 |
|---|---|---|---|
| `output/23.1.complex.txt` | `complex` | 9,160 | `apt_seq` |
| `output/23.2.complex_metrics.txt` | `complex_metrics` | 9,160 | `apt_seq` |
| `output/23.3.horizon_profile.txt` | `horizon_profile` | 19,349 (단지 6,450) | `apt_seq` + `floor_band` |

전부 탭 구분 UTF-8. 키 정합성: `complex ⊇ complex_metrics ⊇ horizon_profile`.

**`horizon_profile`의 PK는 스키마와 다르다.** 정의는 `building_id`였으나 실제 산출은 **단지 × 층대** 집계다. 동 단위 지표는 만들지 않았다.

---

## 2. 채워진 컬럼 / 빈 컬럼

### `complex`

| 컬럼 | 채움률 | 비고 |
|---|---|---|
| `apt_seq` `name` `lat` `lng` `built_year` | 100% | |
| `polygon_matched` `match_confidence` | 100% | 아래 §3 |
| `total_households` `building_count` | 96.6% | 건축물대장 기준. 결측 316건 |
| `bjd_code` | 90.2% | 19.1과 지번(join_key) 매칭 성공 단지. 나머지 9.8%(900단지)는 지번 자체를 못 찾음 — 채움률의 상한 |
| `far` `bcr` | 63.7% | 지번 대표값(동마다 반복 기재된 값 중 0이 아닌 최댓값 하나) 기준. 지번은 매칭됐어도 표제부에 값이 아예 없는 경우가 있어 90.2%보다 낮음 |
| `parking_per_hh` | 63.0% | 옥내외 자주식·기계식 4종 중 1개 이상이 기재된 지번 기준. 소수 단지(34개, `parking_per_hh` > 5)는 여러 아파트가 같은 지번을 공유해 대표값이 부풀려 보임 — 지번 단위 값을 등록 단위로 나눈 구조적 한계 |

### `complex_metrics`

**채워진 것 (프론트가 지금 쓸 수 있는 것)**

| 컬럼 | 채움률 | 출처 |
|---|---|---|
| `sun_hours_avg` `sun_hours_best` `view_open_avg` | 70.4% | horizon 배치. 6,450단지 |
| `station_dist_m` `station_walk_min_est` | 100% | 지하철 **출입구**까지 직선거리. 도보 시간은 추정치(`_est`) |
| `elem_school_m` `mart_m` | 100% | 최근접 직선거리 |
| `park_m` `park_area_m2` | 100% | 24.3 공원 **폴리곤** 기준 재계산. `park_m`이 가리키는 공원과 `park_area_m2`가 항상 같은 공원(폴리곤 인덱스 공유). 기존 22.1(POI node 기준) 대비 median 26.7m 더 가까움 — node는 공원의 대표점 1개까지 거리였고 폴리곤은 경계까지 거리라서 구조적으로 짧아짐 |
| `mid_school_m` `high_school_m` | 100% | 24.6 `is_middle`/`is_high`(isced:level 태그 우선, 이름 보완) 기준 최근접. NEIS 없이 OSM 태그만으로 급별 구분 가능 |
| `dept_store_m` `supermarket_m` | 100% | 24.5 `shop` 원본 태그(`department_store`/`supermarket`) 기준 최근접 |
| `cvs_500m` `restaurant_500m` `nightlife_300m` | 100% | 반경 내 개수(0건은 결측 아님). `nightlife_300m`은 24.4(bar/pub/nightclub) 반경 300m |
| `road_centerline_m` `rail_centerline_m` | 100% | 26.1. 도로는 간선·보조간선 중 최솟값, 철도는 지상 구간만 |
| `road_arterial_dist_m` `road_secondary_dist_m` | 100% | 26.1. 위계별(간선/보조간선) 거리. 스키마 외 추가 컬럼 |
| `station_ridership_daily` | 88.0% | 최근접 역 일평균 승하차. 중앙값 29,089명 |
| `station_congestion_peak` | 85.8% | 평일 08~09시 최대 혼잡도(%). 스키마 외 추가 컬럼 |
| `tertiary_hosp_m` `general_hosp_m` | 100% | 상급종합(14곳) / 종합병원까지 최근접. 중앙값 2.7km / 1.5km |
| `clinic_1km` `pediatric_1km` | 100% | 반경 1km 내 의원 수(중앙값 72) / 소아 표방 기관 수(중앙값 3) |
| `river_view_ratio` | 70.4% | 단지 관측점 중 **한강 본류**(5km² 이상 수계) 조망 비율. 소하천은 제외 |
| `elem_safe_route` | 100% | 초품아 근사. 단지→최근접 초등학교 **직선**이 간선도로를 교차하지 않으면 true. 실제 보행경로가 아니다 |

**빈 것 (전량 NULL)**

`station_elev_diff` `traffic_weekday` `traffic_weekend` `daycare_500m` `dawn_delivery`

전량 NULL 컬럼은 정적 payload(`front/public/data/`)에서 제거된다. 9,160개 파일마다 반복되는 순수 낭비다.

### `horizon_profile`

채워진 것: `apt_seq` `floor_band` `sun_hours_winter` `view_block_pct` `open_angle_mean` (100%)
추가로 채워진 것: `repr_floor` `obs_height` `sun_hours_spring` `open_span_max` `river_view` `park_view` (100%), `mountain_view` (68.7%, DEM 부재로 표고 근사)
빈 것: 없음

`floor_band`는 `LOW` / `MID` / `HIGH` 세 값이다.

---

## 3. `match_confidence` — 화면에 반드시 반영할 것

동 배정의 신뢰도이며, **`plan.md` §4③ "모르는 것을 모른다고 표기"의 실행 지점**이다.

| 값 | 단지 수 | 실제 정확도 | 화면 처리 |
|---|---|---|---|
| `HIGH` | 741 | 99.1% | 정상 표기 |
| `MEDIUM` | 1,759 | 94.6% | 정상 표기 |
| `LOW` | 3,548 | 84.4% | 정상 표기하되 근거 화면(S3)에 신뢰도 명시 |
| `FAILED` | 3,112 | — | **정렬 근거로 쓰지 않음.** 지도에 회색 마커, `분석 불가 — 건물 footprint 미매칭` |

정확도는 준공년도 독립 검증 실측치다 (`docs/decisions.md` 독립 검증 절).

`FAILED` 3,112건 중 2,653건은 `polygon_matched=false`(동을 못 받음)이고, 나머지는 배정됐으나 층수가 대장과 어긋난 단지다.

**`FAILED` 단지를 필터에서 조용히 제외하지 말 것.** `decisions.md` 42번 확정 결정이다. 지도에서 회색으로 보이되 클릭하면 사유가 떠야 한다.

---

## 4. 가격 표기 — `price[].source`가 문구를 가른다

정적 payload의 `price[]` 각 원소는 `source`를 갖는다. **이 축으로 문구가 완전히 갈리며 절대 섞지 않는다.**

| `source` | 화면 문구 | 함께 오는 필드 |
|---|---|---|
| `CELL_LAST` | "2026년 7월 실거래 3.3억" | `last_deal_ym` `last_price_manwon` |
| `COMPLEX_MEAN` | "이 면적 거래 없음 · 단지 평균 …" | `mean_price_per_m2_24m` |
| `MODEL` | "추정 …~… · 이 단지는 최근 2년 거래가 없습니다" + 신뢰도 배지 | `est_low` `est_high` `est_confidence` |
| `EXCLUDED` | "임대 전용 단지로 매매 거래가 없습니다" | `reason` |

`MODEL`은 전체 단지의 일부(2,206단지)에만 붙는다. 예측구간의 명목 수준은 80%이고 검증 기간 실측 coverage는 80.13%다. **다만 "80% 보장"이라 쓰지 말 것** — 첫 coverage 실패를 보고 방식을 바꿨으므로 test가 재사용됐다. "개발 기간에서 관측된 값"까지만 말할 수 있다.

**정렬 축은 더 이상 제품의 약속이 아니다.** 이전 버전의 "일조 → 개방도 → 접근성" 1차 정렬은 검색을 돕는 보조 수단으로 강등됐다 (`product-identity.md` §2).

## 5. S1 필터 동작 확인 (실측)

```
일조 5h+ AND 역 도보 10분              → 2,751단지
일조 6h+ AND 도보 7분 AND 편의점 10개+ →   987단지
일조 7h+ AND 도보 5분 AND 공원 200m    →   173단지
```

조건을 조일수록 후보가 줄고, 줄어든 뒤에도 정렬이 가능하다. §6.7 조건 완화 추천은 이 축들을 완화하며 재계산하면 된다.

---

## 6. 표기 규칙 (어기면 사실 왜곡)

| 항목 | 표기 | 금지 |
|---|---|---|
| 도보 시간 | "추정 도보 N분" | "도보 N분" — 직선거리 × 1.3 ÷ 4km/h 추정치다 |
| 역까지 거리 | 최근접 **출입구** 기준 | 역 중심 기준이라고 쓰지 말 것 |
| 소음 | "도로 중심선까지 N m" | "소음 N dB" — 캘리브레이션 라벨이 없어 폐기됐다 (`algorithms.md` §6.3) |
| 일조 | "동지 08~16시 총 N시간" | 기준 시각을 빼고 "일조 N시간"만 쓰지 말 것 |
| 커버리지 | "분석 가능 단지 6,450 / 9,160" | "서울 전역 완전 검색" — 성립하지 않는다 |
| 가격 | "최근 실거래" 또는 "추정" 라벨을 반드시 구분 | "적정시세" · "저평가" · "고평가" |
| 조망·일조와 가격 | 따로 보여준다 | **둘을 엮는 문장 전부.** 측정 결과 예측력이 없었다 |
| 규제 | "토지거래허가구역 (2026-08-16 기준)" | 기준일 없는 단정 |
| 재건축 | "재건축 추진 중 (조합설립 단계)" | "재건축으로 N% 오릅니다" |
| 승하차 | "1~8호선 기준" 또는 미표기 | 9호선·신분당선 역 근처 단지에 "승하차 정보 없음"을 빈 값으로 두지 말 것. 사유 표기 |

---

## 7. 알려진 한계 (S3 근거 화면에 노출할 것)

| 한계 | 영향 |
|---|---|
| DEM 미확보, 지면을 평지(z=0)로 가정 | 경사지에서 차폐가 과소/과대 추정 |
| 승하차·혼잡도는 **서울교통공사 운영 노선만** | 9호선·공항철도·신분당선·GTX·경의중앙선·수인분당선·우이신설선과 경기 연장 구간은 발행처가 달라 데이터가 없다. 역 317개 중 245개 매칭(77.3%). 표기 오류가 아니라 운영주체 범위 차이다 |
| 어린이집 미수집 | `daycare_500m` 결측. info.childcare.go.kr 개발계정 키 필요 |
| **소아과는 기관명 근사** | 심평원 응답에 진료과목별 필드가 없다(의과·치과·한방 구분뿐). `pediatric_1km`은 기관명에 '소아'가 들어가는 477곳으로 근사했고, 종합병원 안의 소아과는 놓친다 |
| 교통량 **폐기** | 서울 관측 지점이 139개뿐이라 300m 기준 커버리지 4.6%. 소음원 근접도는 `road_centerline_m`(100% 채움)으로 대체한다 (`decisions.md` 44~45) |
| 조망 대상(한강·공원·산) 미판정 | `river_view` 등 전량 결측 |
| 일조는 동지 기준 단일 계절 | 봄가을 미계산 (`plan.md` §8.2 절단 2순위) |
| 건축물대장 표제부 자체가 부분 결측 | `far`/`bcr`/`parking_per_hh`는 19.1과 매칭된 지번(90.2%) 중에서도 63% 안팎만 표제부에 실제 값이 있음. 오래된 건축물은 미기재 — 0으로 채우지 않고 결측 유지 |
| 학교 급별(초/중/고) OSM 태그 커버리지 82.6% | 나머지 17.4%(211개교)는 `isced:level` 태그도 이름도 없어 급별 판정 불가 — 최근접 계산에서 제외(구 단위 공백은 없음을 확인) |

---

## 8. 재생성 방법

```bash
.venv/bin/python data/master/15.assign_dong_all.py         # 동 배정
.venv/bin/python -u models/horizon/21.horizon_batch.py     # 일조·조망 (약 90초)
.venv/bin/python models/horizon/22.access_metrics.py       # 접근성
.venv/bin/python data/collect/19.collect_building_ledger.py # 건축물대장 수집
.venv/bin/python data/collect/24.collect_osm_extra.py      # 도로·철도·공원·야간상권·상점 수집
.venv/bin/python models/horizon/26.transit_metrics.py      # 교통(승하차·혼잡도·중심선) — 22.1 필요
.venv/bin/python data/master/23.build_complex_metrics.py   # 최종 3테이블
```

`output/` 아래 대용량 중간 산출물은 `.gitignore` 대상이다. 재생성 경로는 각 스크립트 헤더에 있다.
