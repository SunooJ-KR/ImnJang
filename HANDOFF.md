# 인수인계 — 서버 세션용

> 작성: 2026-09-10 (D2 종료 시점) · 작성 주체: Windows 로컬 세션
>
> ⚠️ **이 문서는 D2 시점 기록이다. D3는 이미 완료됐다.**
> 5절의 미해결 2·3번과 6절의 D3 계획은 해소됐고, 저장소 구조도 디렉토리
> 수준으로 분리됐다. 현재 상태는 [`plan.md`](plan.md) §11과
> [`docs/decisions.md`](docs/decisions.md) D3 결정을 보라.
> 이 파일은 **서버에서 이어서 작업할 세션**이 읽는 현재 상태 기록이다.
> 계획 전문은 [`plan.md`](plan.md), 결정 이력은 [`docs/decisions.md`](docs/decisions.md).

---

## 1. 왜 이 문서가 필요한가

D1~D2는 Windows 로컬 세션(`Z:\home\yjkim\test\imnjang` 마운트)에서 진행했다.
그 세션은 **스크립트를 쓰기만 하고 실행하지 못한다**:

- `.venv`는 Linux용이라 Windows에서 실행 불가
- SSH 키 인증이 안 됨 (`192.168.50.18:10023` → `Permission denied (publickey)`)

그래서 D2까지는 **로컬이 작성 → 사용자가 서버에서 수동 실행 → 로그를 다시 로컬로** 붙이는 왕복이었다.
서버 세션은 이 왕복 없이 직접 실행할 수 있으므로, D3부터는 서버에서 진행한다.

**경로 규칙**: 서버에서는 `/home/yjkim/test/imnjang`. `Z:` 접두사는 존재하지 않는다.

---

## 2. 실행 환경

```bash
cd /home/yjkim/test/imnjang
.venv/bin/python <script>.py 2>&1 | tee output/<n>.run.log
```

- 가상환경: `.venv` (Linux, 구축 완료). geopandas·shapely·pyproj·rtree·pvlib·lightgbm·requests·pandas·playwright
- `.env`: API 키 3종 (공공데이터포털 / 카카오 REST / 카카오 JS)
- **`chmod 600 .env` 미실행 상태.** 서버 세션 첫 작업으로 처리할 것

---

## 3. 현재까지의 진행

### D1 (09-09) — 데이터 소스 검증 · horizon 프로토타입

`plan.md` §6에 실측 전문. 요약하면 horizon 엔진 2.9ms/관측점(서울 전역 약 6분),
OSM만으로 아파트 동 높이 95.0% 확보 → GIS건물통합정보(CC BY-NC-ND) 의존 제거.

### D2 (09-10) — 서울 전역 수집 · 셀 커버리지

`plan.md` §6b에 실측 전문. 스크립트 3개(`11`, `12`, `13`) 작성·실행 완료.

| 산출물 | 내용 |
|---|---|
| `output/11.1.trades_sale.txt` | 매매 247,362행 / 7,883단지 |
| `output/11.2.trades_rent.txt` | 전월세 1,306,717행 / 9,373단지 |
| `output/12.1.osm_buildings.txt` | 건물 70,250동 (아파트 37,269 / 150m+ 140) |
| `output/12.2.osm_complex_boundary.txt` | 단지 경계(landuse) 4,285 |
| `output/12.3.osm_poi.txt` | POI 7종 63,390건 |
| `output/13.1.cell_coverage.txt` | 면적타입 3후보 × floor_band 유무 비교표 |
| `output/raw/` | API 원본 JSON 캐시. **재실행 시 여기 있는 건 건너뛴다** |

폴리곤은 WKT 문자열로 저장돼 있다. 되읽을 때 `shapely.wkt.loads`.

---

## 4. 확정된 결정 (되묻지 말 것)

| 항목 | 확정 |
|---|---|
| 예측 단위 | **단지 × 면적타입 × 층대(floor_band)** 유지 |
| 면적타입 | **`round(전용면적 / 3) × 3`** (3m 폭 bin) |
| 거래 5건 미만 셀 | 예측하되 **신뢰도 LOW** 표기, 정렬 근거로 쓰지 않음 |
| R11 게이트 | "커버 단지 60%" **폐기** → **"5건+ 셀의 거래 점유율 ≥ 80%"** (실측 82.3% PASS) |
| 높이 결측 4,541동 | D3 매칭 후 **같은 단지 다른 동의 높이 중앙값으로 대체**, 대체 플래그를 남겨 리포트에 공개. 매칭 실패로 대체 불가한 동만 제외 |
| 실행 환경 | 서버에서 실행 (이 문서 §2) |

---

## 5. 남은 문제 / 미해결

| # | 내용 | 처리 시점 |
|---|---|---|
| 1 | **전월세 미수집 2건** — 종로구 202209, 중랑구 202606. 원인은 API 한도가 아니라 connect timeout. `data/collect/11.collect_trades.py`를 그냥 다시 돌리면 이 2건만 받는다 | 아무 때나 |
| 2 | ~~준공년도 ±2년 일치율 86.3%~~ **해소.** 그 수치는 OSM `start_date` 태그 품질이었다. 한국부동산원 등록 정보 기준 재측정 결과 **99.9%** (`17.verify_registry.py`) | D3 완료 |
| 3 | ~~`chmod 600 .env` 미실행~~ **해소** (이미 600) | — |
| 4 | 13번 층대는 **단지별 최고 거래층을 총 층수로 근사**한 값이다. D3에서 실제 동 층수가 붙으면 재측정 | D3 이후 |

---

## 6. 다음 할 일 — D3 (09-11), plan.md 최대 난관

`plan.md` §7 기준: **마스터 구축 — 지오코딩 → 클러스터 매칭 → 층화 100개 수동 검수**

강남구에서 검증된 파이프라인(`08` → `09`)을 **서울 전역으로 확장**하는 일이다.

1. ~~`08.geocode_match.py` 확장~~ → **완료**: `data/master/14.geocode_all.py` (99.96%) — 7,883단지 카카오 지오코딩 (강남 391건은 `output/cache_geocode.json`에 캐시됨)
2. ~~`09.assign_dong.py` 확장~~ → **완료**: `data/master/15.assign_dong_all.py` (동수 일치 93.2%) — 동 37,269개를 단지에 1:1 배정
3. 높이 결측 4,541동을 단지 중앙값으로 대체 (§4 결정)
4. **층화 정답셋 100개 수동 검수** (HIGH 30 / MEDIUM 30 / hard 40) — `plan.md` §10
   - 통과 기준: HIGH 무오류, MEDIUM 90% 이상
   - **이건 사용자가 눈으로 봐야 하는 작업이다.** 에이전트가 대신 판정하지 말 것

### 검수에서 밀리면

`plan.md` §8.2 선제 절단을 **협상 없이** 순서대로 발동한다:
`river/park/mountain_view` → 봄가을 일조 → 생활 인프라 우선순위 4~6 → S4 가중치 슬라이더 → Layer 2 ML → 조건 완화 추천

끝까지 지키는 최소 집합: 겨울 일조 · 조망 개방도 · 도로/철도 거리 · 추정 역 접근성 · S1 전역 필터링 · D8 배포.

---

## 7. 밟은 지뢰 (반복하지 말 것)

- **`requests` 예외 메시지에 요청 URL이 통째로 들어간다.** 실패 로그를 파일로 남기면 serviceKey가 평문으로 샌다. D2에 실제로 발생했고 커밋 전에 제거했다. `data/collect/11.collect_trades.py`의 `mask_key()`를 다른 API 스크립트에도 적용할 것
- **매매 API는 `roadNm`, 전월세 API는 `roadnm`.** 응답 키를 전부 소문자화한 뒤 표준명으로 되돌리는 계층(`FIELD_MAP`)을 반드시 거칠 것
- **매매는 '상세' 엔드포인트만 쓴다** (`RTMSDataSvcAptTradeDev`). 일반 엔드포인트에는 `aptSeq`가 없다
- **좌표 매칭 방향을 뒤집어야 한다.** "단지를 클러스터에 붙이기"는 한 클러스터에 최대 27개 단지가 붙어 실패했다(08). "동을 단지에 배정하기"로 풀면 중복이 원천 차단된다(09)
- 거리·면적 연산은 **EPSG:5179**. 저장·표시는 EPSG:4326
- `output/raw/`와 대용량 결과 테이블은 `.gitignore`에 있다. 커밋되지 않는다

---

## 8. git 상태 (작성 시점)

```
 M .gitignore      # output/raw/, 대용량 결과 테이블 추가
 M plan.md         # §6b D2 실측, §7 D2 완료, §9 R11 해소, §11 스크립트 상태
 M docs/algorithms.md   # 면적타입 정의, 표본 희박 셀 처리
 M docs/decisions.md    # D2 결정 11~15
?? 11.collect_trades.py 12.collect_osm.py 13.cell_coverage.py
?? HANDOFF.md output/13.1.cell_coverage.txt
```

**아직 커밋하지 않았다.** 서버 세션이 확인 후 커밋할 것.
