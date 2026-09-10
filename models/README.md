# models

물리 지표 엔진과 예측 모델. 배치(`data/`)가 만든 마스터를 입력으로 받는다.

| 디렉토리 | 내용 | 일정 |
|---|---|---|
| `horizon/` | horizon profile 엔진 — 단일 연산에서 일조·조망 개방도·향별값을 전부 파생. `05.horizon_prototype.py`가 D1 검증본이며 서울 전역 배치로 확장 예정 | D4 |
| (예정) | 단지 시세 모델 (LightGBM + SHAP). 시간 부족 시 생략 가능 — `plan.md` §8.1 | D9 |

입력: `output/15.1.complex_final.txt`, `output/15.2.building_assigned.txt`
