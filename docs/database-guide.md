# Railway Database 사용 가이드

> 작성 2026-09-15 · 대상: Railway Postgres 초기 운영, 배치 적재, 모델/API 읽기 전용 접속
> 상태: 초기 구축 완료. 스키마는 [`db/001_schema.sql`](../db/001_schema.sql), 적재 스크립트는 [`db/38.load_db.py`](../db/38.load_db.py)를 따른다.

## 0. 원칙

1. DB는 프론트 읽기 가속용이 아니라 **진실 원천(system of record)** 과 배치 적재 대상이다.
2. 브라우저의 기본 읽기 경로는 계속 `front/public/data` 정적 JSON이다.
3. 데이터 삽입·갱신은 배치 적재 계정만 수행한다.
4. 모델/API 구현은 `DATABASE_READONLY_URL`만 사용한다. 이 계정은 기본 트랜잭션이 read-only라 쓰기가 막힌다.
5. `.env`에는 실제 접속 URL을 넣되 절대 커밋하지 않는다.

## 1. Railway 구성

Railway 프로젝트에 Postgres 서비스를 1개 둔다.

로컬 PC나 GitHub Actions에서 DB에 접속해야 하면 Postgres 서비스의 `Settings` → `Networking`에서 Public Access를 켠다. 이후 Railway Variables에서 다음 값을 확인한다.

| 변수 | 용도 |
|---|---|
| `DATABASE_PUBLIC_URL` | 로컬 PC, GitHub Actions 등 외부에서 초기 스키마 적용·적재할 때 사용 |
| `DATABASE_URL` | Railway 내부 서비스에서 접속할 때 사용 |
| `DATABASE_LOADER_URL` | 배치 적재 전용 계정. 이 저장소의 `.env`에 생성해서 사용 |
| `DATABASE_READONLY_URL` | 모델/API 읽기 전용 계정. 런타임 서비스에는 이 값만 전달 |

초기 role 생성은 Railway가 제공한 기본 `DATABASE_PUBLIC_URL`로 접속해 수행한다. 이 프로젝트에서는 이미 `imnjang_loader`, `imnjang_readonly` 계정을 만들었고 `.env`에 파생 URL을 추가했다.

## 2. 로컬 준비

PostgreSQL client가 설치돼 있어야 한다.

```powershell
psql --version
```

프로젝트 루트 `.env`에는 아래 세 값을 둔다.

```text
DATABASE_PUBLIC_URL=postgresql://...
DATABASE_LOADER_URL=postgresql://imnjang_loader:...@...
DATABASE_READONLY_URL=postgresql://imnjang_readonly:...@...
```

키와 비밀번호가 포함된 값은 로그, 문서, PR 본문에 붙이지 않는다.

## 3. 스키마 적용

스키마를 새 DB에 적용하거나 권한을 다시 보강할 때 실행한다.

```powershell
$line = Get-Content -Encoding UTF8 .env | Where-Object { $_ -match '^DATABASE_PUBLIC_URL=' } | Select-Object -First 1
$dbUrl = $line.Substring('DATABASE_PUBLIC_URL='.Length).Trim().Trim('"')
psql $dbUrl -v ON_ERROR_STOP=1 -f db\001_schema.sql
```

생성되는 주요 테이블은 다음과 같다.

| 테이블 | 내용 |
|---|---|
| `app.dataset_snapshot` | 데이터 스냅샷 메타데이터와 active 포인터 |
| `app.complex` | 단지 기본 정보 |
| `app.complex_metrics` | 교통·교육·생활·환경 지표 |
| `app.horizon_profile` | 단지×층대 일조·조망 지표 |
| `app.price_cell` | 단지×면적×층대 가격 라우팅 결과 |
| `app.price_series` | 단지×면적 월별 가격 시계열 |
| `app.estimate` | cold-start 모델 추정 결과 |
| `app.comparable` | 비교 거래 사례 |
| `app.regulation_summary` | 토지거래허가구역 요약 JSON |
| `app.share` | 공유 기능용 익명 단지 목록. 기능 채택 시 사용 |

## 4. 데이터 적재

적재는 `output/*.txt` 가공 산출물을 기준으로 한다. 원천 거래 원장(`output/raw`, 수백 MB 거래 원장 등)은 DB에 넣지 않는다.

현재 적재 스크립트가 읽는 파일:

| 파일 | 대상 테이블 |
|---|---|
| `output/23.1.complex.txt` | `app.complex` |
| `output/23.2.complex_metrics.txt` | `app.complex_metrics` |
| `output/23.3.horizon_profile.txt` | `app.horizon_profile` |
| `output/32.1.price_cells.txt` | `app.price_cell` |
| `output/32.2.price_series.txt` | `app.price_series` |
| `output/33.1.coldstart_estimates.txt` | `app.estimate` |
| `output/34.1.comparables.txt` | `app.comparable` |
| `output/35.2.regulation_summary.json` | `app.regulation_summary` |

실행:

```powershell
.venv\Scripts\python.exe db\38.load_db.py
```

스크립트는 다음 순서로 동작한다.

1. 입력 파일 존재 확인
2. 임시 staging 테이블 생성
3. `psql \copy`로 TSV 파일 bulk 적재
4. 현재 파일 행 수와 DB 적재 행 수 비교
5. 새 `dataset_snapshot` 생성
6. `app.*` 테이블에 새 `snapshot_id`로 삽입
7. FK·행 수 검증
8. 검증 성공 시에만 active snapshot 전환

실패하면 트랜잭션이 롤백되므로 기존 active snapshot은 유지된다.

## 5. 현재 적재 상태

2026-09-15 초기 적재 결과:

| 항목 | 값 |
|---|---:|
| `snapshot_id` | 2 |
| `as_of` | 2026-08-16 |
| `complex` | 9,160 |
| `price_cell` | 40,848 |
| `estimate` | 5,207 |
| `comparable` | 120,271 |

`docs/serving-analysis.md` 작성 시점의 행 수와 현재 로컬 산출물 행 수가 다를 수 있다. 적재 스크립트는 문서의 고정 숫자가 아니라 현재 파일 행 수를 직접 세어 검증한다.

## 6. 읽기 전용 검증

모델/API에서 사용할 계정은 읽기만 가능해야 한다.

```powershell
$line = Get-Content -Encoding UTF8 .env | Where-Object { $_ -match '^DATABASE_READONLY_URL=' } | Select-Object -First 1
$dbUrl = $line.Substring('DATABASE_READONLY_URL='.Length).Trim().Trim('"')
psql $dbUrl -v ON_ERROR_STOP=1 -c "select current_user, current_setting('transaction_read_only');"
```

기대 결과:

```text
current_user = imnjang_readonly
transaction_read_only = on
```

쓰기 차단 확인:

```powershell
psql $dbUrl -c "insert into app.share (share_id, apt_seqs) values ('readonly-probe', array['11110-100']);"
```

기대 결과:

```text
ERROR: cannot execute INSERT in a read-only transaction
```

## 7. 앱·모델에서 읽는 방법

active snapshot만 조회한다.

```sql
select c.*
from app.dataset_snapshot ds
join app.complex c on c.snapshot_id = ds.snapshot_id
where ds.is_active
  and c.apt_seq = '11110-100';
```

가격 셀 조회 예:

```sql
select pc.*
from app.dataset_snapshot ds
join app.price_cell pc on pc.snapshot_id = ds.snapshot_id
where ds.is_active
  and pc.apt_seq = '11110-100'
order by pc.area_type, pc.floor_band;
```

모델/API 코드는 반드시 `DATABASE_READONLY_URL`을 사용한다. 즉석 추정 기능을 구현하더라도 DB에는 추론 결과를 저장하지 않고 응답만 반환한다.

## 8. 데이터 추가 절차

새 지표나 새 테이블이 필요하면 아래 순서로 진행한다.

1. `docs/data-contract.md`에 컬럼 의미, 결측 의미, 화면 문구를 먼저 기록한다.
2. 기존 번호 체계에 맞춰 `output/{번호}.{순서}.{이름}.txt` 산출물을 만든다.
3. `db/001_schema.sql`에 컬럼 또는 테이블을 추가한다.
4. `db/38.load_db.py`에 입력 파일, 임시 테이블, insert 변환, 검증을 추가한다.
5. Railway DB에 스키마를 적용한다.
6. `db/38.load_db.py`로 새 스냅샷을 적재한다.
7. read-only 계정으로 active snapshot 조회를 확인한다.

부분 update로 운영 DB를 직접 고치지 않는다. 배치 산출물을 다시 만들고 새 스냅샷으로 통째 교체한다.
