# 재현 실행 안내

## 범위와 전제

이 문서는 현재 저장소에서 추적되는 Python 코드의 파일 I/O를 기준으로 작성했다. `data/`와 `front/public/data/`는 대용량·배포 산출물이라 이 작업에서는 읽지 않았다. 따라서 아래 수집·payload 단계의 스크립트명과 목적은 기존 설계 문서(`plan.md`, `docs/implementation-plan.md`)의 기록을 따르고, 현재 존재하는 `models/` 단계의 입력·출력은 소스에서 직접 확인했다.

실행은 Linux에서 해야 한다. `21.horizon_batch.py`와 `29.fill_view_metrics.py`가 `multiprocessing.get_context("fork")`를 명시적으로 사용하므로 Windows의 `spawn` 환경은 지원하지 않는다. 기준 interpreter는 Python 3.13.13이며, 이 저장소의 확인 환경은 `/home/yjkim/test/imnjang/.venv/bin/python`이다.

```bash
/home/yjkim/test/imnjang/.venv/bin/python -m pip install -r requirements.txt
/home/yjkim/test/imnjang/.venv/bin/playwright install chromium  # 목업 PNG가 필요할 때만
```

`21`과 `29`는 CPU 수에서 2를 뺀 worker 수로 `fork` Pool을 실행한다. 메모리와 CPU 여유가 있는 Linux 호스트에서 실행해야 하며, Windows/WSL의 경로·process 모델 차이를 우회하지 않는다.

## 실행 순서

아래 순서는 번호가 아니라 실제 upstream 파일 의존성을 따른다. 수집 단계의 소스는 이 worktree에 없으므로 해당 단계는 원본 저장소의 수집 스크립트를 복원한 뒤 실행한다. `19 → 15·21`, `31 → 23` 의존성이 순서에 반영돼 있다.

| 순서 | 스크립트 | 입력 | 출력 | 네트워크/키 | 대략 소요 |
| --- | --- | --- | --- | --- | --- |
| 1 | `data/collect/11.collect_trades.py` | 국토교통부 실거래 API 응답/`rt.molit.go.kr` 서울 CSV | `11.1.trades_sale.txt`, `11.2.trades_rent.txt` | 필요; 공공데이터포털 API 승인 또는 CSV | 수십 분~수 시간 (캐시·벌크 여부) |
| 2 | `data/collect/12.collect_osm.py` | Overpass OSM | `12.1.osm_buildings.txt`, `12.2.osm_complex_boundary.txt`, `12.3.osm_poi.txt` | 필요; API 키 없음, User-Agent 필요 | 캐시 없으면 수 분 이상 |
| 3 | `data/collect/19.collect_building_ledger.py` | 건축물대장 표제부 API | `19.1.building_ledger.txt` | 필요; 공공데이터포털 API 키 | API quota에 좌우 |
| 4 | `data/master/14.geocode_all.py` | 11.1·11.2의 단지 주소 | `14.1.geocoded_master.txt` | 필요; Kakao Local REST key | 약 9천 건, cache 없으면 수 분 |
| 5 | `data/master/15.assign_dong_all.py` | `12.1`, `12.2`, `14.1`, **`19.1`** | `15.1.complex_final.txt`, `15.2.building_assigned.txt` | 없음 | 수 분 |
| 6 | `models/horizon/21.horizon_batch.py` | `12.1`, `15.2`, `15.1`, `14.1`, **`19.1`** | `21.1.complex_metrics.txt`, `21.2.observation_points.txt`, `21.3.skyline.npy` | 없음 | 약 90초 |
| 7 | `models/horizon/22.access_metrics.py` | `12.3`, `14.1` | `22.1.access_metrics.txt` | 없음 | 약 1초 |
| 8 | OSM 추가 수집(설계상 `24.collect_osm_extra.py`) | Overpass OSM | `24.1.osm_roads.txt`, `24.2.osm_rails.txt`, `24.3.osm_parks.txt`, `24.4.osm_nightlife.txt`, `24.5.osm_shops.txt`, `24.6.osm_schools.txt` | 필요; API 키 없음 | cache 있으면 약 20초, 없으면 약 8분 |
| 9 | 교통 원천 확보 및 `models/horizon/26.transit_metrics.py` | `12.3`, `22.1`, `24.1`, `24.2`, `output/raw/transit/subway_monthly.csv`, `output/raw/transit/subway_congestion.csv` | `26.1.transit_metrics.txt` | 원천 CSV 취득 시 필요; 계산은 없음 | 수 초 |
| 10 | 병원·조망 원천 수집(27·28) | 공공 병원 원천, Overpass 수계·산 | `27.1.hospitals.txt`, `28.1.water.txt`, `28.2.mountains.txt` | 필요; 원천별 API/Overpass | cache 여부에 좌우 |
| 11 | `models/horizon/29.fill_view_metrics.py` | `21.2`, `21.3`, `28.1`, `28.2`, `24.1`, `24.3`, `24.6`, `22.1`, `15.1`, `12.1`, `14.1` | `29.1.view_metrics.txt`, `29.2.complex_view.txt` | 없음 | CPU 병렬, 수 분 |
| 12 | `data/collect/31.match_redevelop.py` | 정비사업 원천과 `15.1` 단지 지번 | `31.1.complex_redevelop.txt` | 원천 갱신 시 필요 | 수 초~수 분 |
| 13 | `data/collect/35` | 토지거래허가구역 원천 | `35.1.land_permit_zones.txt`, `35.2.regulation_summary.json` | 원천 취득 시 필요 | 수 초 |
| 14 | `data/master/23.build_complex_metrics.py` | `15.1`, `21.1`, `22.1`, `26.1`, `29.2`, **`31.1`** | `23.1.complex.txt`, `23.2.complex_metrics.txt`, `23.3.horizon_profile.txt` | 없음 | 수 초~수 분 |
| 15 | `models/price/30.spike_hedonic.py` | `11.1`, `14.1`, `15.1`, `23.1`~`23.3` | `30.1`~`30.5` | 없음 | 재학습/검증 필요 시 수 분 이상 |
| 16 | `models/price/32.build_price_cells.py` | `11.1`, `15.1`, `23.1`, `23.3` | `32.1.price_cells.txt`, `32.2.price_series.txt` | 없음 | 수 초 |
| 17 | `models/price/33.train_coldstart.py` | `11.1`, `11.2`, `14.1`, `15.1`, `23.1`~`23.3`, `31.1`, `32.1` | `33.1`~`33.3` | 없음 | model training 포함, 수 분 이상 |
| 18 | `models/price/34.build_comparables.py` | `11.1`, `23.1`, `23.2`, `32.1`, `33.1`, `33.3` | `34.1.comparables.txt` | 없음 | 수 분 |
| 19 | `front/build/36.build_payload.py` | `23.1`~`23.3`, `31.1`, `32.1`, `32.2`, `33.1`, `33.3`, `34.1`, 규제 산출물 | 정적 payload | 없음 | 파일 수에 비례, 수 분 |

선택 검증 스크립트 `13`, `16`, `17`, `18`, `20`은 각 upstream 산출물을 점검하지만 payload 생성의 필수 선행 조건은 아니다. 목업 PNG는 `docs/mockups/10.render_mockups.py`가 HTML과 Playwright/Chromium을 입력으로 생성하며 데이터 pipeline과 독립적이다.

가격 셀은 기본적으로 새 산출물을 생성·저장한다. 기존 파일과 byte 수준의 DataFrame 일치를 점검하려면 다음처럼 명시적으로 gate를 켠다.

```bash
/home/yjkim/test/imnjang/.venv/bin/python models/price/32.build_price_cells.py
/home/yjkim/test/imnjang/.venv/bin/python models/price/32.build_price_cells.py --verify-against-existing
```

## gitignore 필수 입력 manifest

아래는 현재 `models/` 실행에 직접 필요하지만 `.gitignore`된 파일이다. 크기·SHA-256·수정일은 `/home/yjkim/test/imnjang`의 실제 파일에서 2026-09-14에 계산했다. `output/raw/`의 HTTP 응답 cache는 각 수집 단계가 다시 만들 수 있으므로 manifest에서 제외했다. 단, 26이 직접 읽는 두 교통 CSV는 cache가 아닌 원천 입력이므로 포함했다.

| 경로 | 크기 (bytes) | SHA-256 | 수정일 (+0900) | 취득 방법 |
| --- | ---: | --- | --- | --- |
| `output/11.1.trades_sale.txt` | 37,472,542 | `e6f1ae50ed1a783c3165e1a2e3aeb78f14803af7cc792a0e07256cd3e8a57f25` | 2026-09-10 12:48:06 | 11: 국토교통부 API/`rt.molit.go.kr` 서울 매매 CSV |
| `output/11.2.trades_rent.txt` | 189,353,268 | `b8723810cea5cf772ecd358918a3cef0ac1a71da68d3c2389d2dfda4a5cef372` | 2026-09-10 12:48:12 | 11: 국토교통부 API/`rt.molit.go.kr` 서울 전월세 CSV |
| `output/12.1.osm_buildings.txt` | 16,188,154 | `1bffdc4e16b7e34af4c44af86acb05a26dac8a4a2fdf7f813756fb9a5bcaaf58` | 2026-09-10 12:52:23 | 12: Overpass OSM 수집 |
| `output/12.2.osm_complex_boundary.txt` | 1,462,991 | `18b80bd7c154a834a1f7b14b4f8623786841eab3e5fe53933de8497f206f7444` | 2026-09-10 12:52:23 | 12: Overpass OSM 수집 |
| `output/12.3.osm_poi.txt` | 4,804,796 | `8244a78fd706634d61f2b702cf2b375981cd7f8c3bf3c823115d4250af40b21c` | 2026-09-10 12:52:23 | 12: Overpass OSM 수집 |
| `output/15.2.building_assigned.txt` | 3,513,687 | `952d550b68c65eeba1206d08943584be2a3f90aec12f14fc4ba178f29b71c241` | 2026-09-10 19:12:20 | 15: 12·14·19을 입력으로 동 배정 |
| `output/19.1.building_ledger.txt` | 10,763,898 | `770453d3ba3c5f8d249ae28f28adc09c2900f017324d680d49fd4d064f16d90e` | 2026-09-11 09:57:41 | 19: 건축물대장 표제부 API |
| `output/21.2.observation_points.txt` | 5,063,172 | `32d42b838f9d6c783a2523b363670f29b03136f108dff7438f1aabdf94ce5b28` | 2026-09-11 11:58:30 | 21: horizon batch |
| `output/21.3.skyline.npy` | 19,197,344 | `388fa50dcf3ffc2aa9a033289d7f21734cd0517b90a5b00a0bf3ccf061c1d4ee` | 2026-09-11 11:58:30 | 21: horizon batch |
| `output/24.1.osm_roads.txt` | 2,375,757 | `a24162c848ce252f9a0b6bddf1bf88e49b1852cac99de846835fab0f743a6a80` | 2026-09-11 10:12:44 | 24: Overpass OSM 추가 수집 |
| `output/24.3.osm_parks.txt` | 1,053,767 | `dbc7462cc7ddb64c372c594b38fc42986604649eafc50da55a286a08ea72c096` | 2026-09-11 10:12:44 | 24: Overpass OSM 추가 수집 |
| `output/raw/transit/subway_monthly.csv` | 121,549 | `f292a520afa3bb24511f1653c4c6e4802bbe880e1c5f6d3d3803ebf2ff49ac9c` | 2026-09-11 09:55:21 | 서울교통공사 역별 승하차 CSV |
| `output/raw/transit/subway_congestion.csv` | 429,446 | `36f3ec9b3b2e4a35e84604aba29f96908a3f6737e6a817145f61cac5f24162c8` | 2026-09-11 09:55:28 | 서울교통공사 혼잡도 CSV |

manifest 일치 여부는 다음 명령으로 재확인한다.

```bash
sha256sum output/11.1.trades_sale.txt output/11.2.trades_rent.txt output/12.1.osm_buildings.txt \
  output/12.2.osm_complex_boundary.txt output/12.3.osm_poi.txt output/15.2.building_assigned.txt \
  output/19.1.building_ledger.txt output/21.2.observation_points.txt output/21.3.skyline.npy \
  output/24.1.osm_roads.txt output/24.3.osm_parks.txt output/raw/transit/subway_monthly.csv \
  output/raw/transit/subway_congestion.csv
```
