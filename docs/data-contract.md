# 데이터 계약 — 프론트엔드용

> 프론트(`front/`)가 소비하는 테이블의 실제 상태를 기록한다.
> 스키마 정의는 [`data-model-and-ui.md`](data-model-and-ui.md) §7이고, 이 문서는 **그 스키마 중 실제로 채워진 것이 무엇인지**를 말한다.
> 갱신: D5 종료 시점. 수치는 `data/master/23.build_complex_metrics.py` 실행 결과.

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
| `bjd_code` `far` `bcr` `parking_per_hh` | **0%** | 건축물대장 캐시에 원본이 있으나 파싱 미반영 |

### `complex_metrics`

**채워진 것 (프론트가 지금 쓸 수 있는 것)**

| 컬럼 | 채움률 | 출처 |
|---|---|---|
| `sun_hours_avg` `sun_hours_best` `view_open_avg` | 70.4% | horizon 배치. 6,450단지 |
| `station_dist_m` `station_walk_min_est` | 100% | 지하철 **출입구**까지 직선거리. 도보 시간은 추정치(`_est`) |
| `elem_school_m` `mart_m` `park_m` | 100% | 최근접 직선거리 |
| `cvs_500m` `restaurant_500m` | 100% | 반경 500m 개수 |

**빈 것 (20개 컬럼, 전량 NULL)**

`river_view_ratio` `road_centerline_m` `rail_centerline_m` `station_elev_diff` `station_ridership_daily` `traffic_weekday` `traffic_weekend` `elem_safe_route` `mid_school_m` `high_school_m` `daycare_500m` `tertiary_hosp_m` `general_hosp_m` `clinic_1km` `pediatric_1km` `dept_store_m` `supermarket_m` `park_area_m2` `nightlife_300m` `dawn_delivery`

### `horizon_profile`

채워진 것: `apt_seq` `floor_band` `sun_hours_winter` `view_block_pct` `open_angle_mean` (100%)
빈 것: `repr_floor` `obs_height` `sun_hours_spring` `open_span_max` `river_view` `park_view` `mountain_view`

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

## 4. 정렬 축

`docs/decisions.md` 37~38번 결정.

```
1차: sun_hours_avg      (winter_total_hours_8_16 기반, 상한 8.08h)
2차: view_open_avg      (일조 만점 단지를 가르는 축)
3차: station_walk_min_est 등 접근성
```

**일조 단독 정렬은 상위권이 뭉친다.** 8.08h 만점 단지가 272개이며 이는 지표 결함이 아니라 물리 현상이다 — 차폐가 없는 단지는 같은 위도에서 같은 태양을 보므로 실제로 동등하다. 그 272단지의 `view_open_avg`는 256개 고유값(72~176도)으로 완전히 갈리므로 2차 축이 필요하다.

변별력 실측: `sun_hours_avg` 1,164개 고유값 / `view_open_avg` 2,763개.

---

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

---

## 7. 알려진 한계 (S3 근거 화면에 노출할 것)

| 한계 | 영향 |
|---|---|
| DEM 미확보, 지면을 평지(z=0)로 가정 | 경사지에서 차폐가 과소/과대 추정 |
| 도로·철도 중심선 미수집 | 소음원 근접도 전량 결측 |
| 병원 등급·어린이집·교통량 미수집 | 해당 필터 제공 불가 |
| 조망 대상(한강·공원·산) 미판정 | `river_view` 등 전량 결측 |
| 일조는 동지 기준 단일 계절 | 봄가을 미계산 (`plan.md` §8.2 절단 2순위) |

---

## 8. 재생성 방법

```bash
.venv/bin/python data/master/15.assign_dong_all.py      # 동 배정
.venv/bin/python -u models/horizon/21.horizon_batch.py  # 일조·조망 (약 90초)
.venv/bin/python models/horizon/22.access_metrics.py    # 접근성
.venv/bin/python data/master/23.build_complex_metrics.py # 최종 3테이블
```

`output/` 아래 대용량 중간 산출물은 `.gitignore` 대상이다. 재생성 경로는 각 스크립트 헤더에 있다.
