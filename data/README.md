# data

배치 파이프라인 (Python). 번호 접두사가 실행 순서이며 폴더를 가로질러 이어진다.

| 디렉토리 | 스크립트 | 재실행 빈도 |
|---|---|---|
| `collect/` | `11` 실거래, `12` OSM 건물·경계·POI | 드묾 — API 한도와 캐시 때문 |
| `master/` | `14` 지오코딩, `15` 동 배정 | 잦음 — 알고리즘 수정 시마다 |
| `verify/` | `13` 셀 커버리지, `16` 검수 표본, `17` 등록 정보 대조 | 매번 |
| `archive/` | `01`~`09` 강남구 프로토타입 | 실행 안 함 — `docs/decisions.md`가 근거로 인용 |

실행은 저장소 루트에서 한다. 스크립트는 `Path(__file__).resolve().parents[2]`로 루트를 잡아
`.env`와 `output/`을 찾는다.

```bash
.venv/bin/python -u data/master/15.assign_dong_all.py 2>&1 | tee output/15.run.log
```

`output/raw/reb/apt_registry.csv` (한국부동산원 공동주택 단지 식별정보)는 용량 때문에
커밋하지 않는다. 재다운로드 주소는 `verify/17.verify_registry.py` 헤더에 있다.
