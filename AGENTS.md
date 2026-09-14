# AGENTS.md — 임앤장(ImnJang) 프로젝트 에이전트 지침

이 파일은 이 저장소에서 일하는 모든 코딩 에이전트(Claude Code, Codex 등)와 사람이 따르는 **프로젝트 공통 규칙**이다.
Codex는 이 파일을 직접 읽는다. Claude Code는 저장소 루트의 `CLAUDE.md`에 `@AGENTS.md` 한 줄을 두어 읽는다.
`CLAUDE.md`는 개인 로컬 설정 파일로 `.gitignore` 대상이므로 각자 만든다(`echo '@AGENTS.md' > CLAUDE.md`). 규칙을 바꿀 때는 이 파일만 고친다.
사람용 협업 규칙의 원문은 [`README.md`](README.md)이며, 이 파일은 그것을 에이전트가 실행 가능한 형태로 좁힌 것이다.

## 1. 프로젝트 한 줄 정의

관심 있는 서울 아파트(9,160단지)를 직접 담아 **같은 기준으로 나란히 비교**하는 서비스.
원티드 AI Championship 2026 팀 출품작. 배포: https://imnjang-pi.vercel.app

- 제품 정의 정본: [`docs/product-identity.md`](docs/product-identity.md)
- 결정 기록: [`docs/decisions.md`](docs/decisions.md) — 새 결정은 번호를 이어 근거와 함께 추가
- 데이터 계약(payload·문구): [`docs/data-contract.md`](docs/data-contract.md)
- 재현 순서: [`docs/reproduce.md`](docs/reproduce.md)

## 2. 담당과 알림

| 영역 | 담당 | GitHub |
|---|---|---|
| `front/` 웹앱 | 정선우 | @SunooJ-KR |
| `models/`, `data/` 모델·데이터 | 김용진, 서지은 | @yjkim-94, @sje0221 |

- **`front/`를 수정하면 반드시 정선우 님에게 알린다.** PR 리뷰어로 @SunooJ-KR 을 지정하고 무엇을 왜 바꿨는지 PR 본문에 적는다.
- 공통 인터페이스(`front/public/data` payload 스키마, `output/` 컬럼)가 바뀌면 영향받는 담당자에게 먼저 알린다.

## 3. 작업 흐름 (필수)

1. **작업 단위마다** 최신 `main`에서 브랜치를 만든다. 예: `feat/front-map-cluster`, `fix/price-area-fallback`, `docs/backend-plan`.
2. 로컬 검증(§5)을 통과시킨다. 검사 명령은 **exit code로 다음 단계를 막는다**(`&&` 또는 실패 시 중단). 실패를 무시하고 커밋·PR로 넘어가지 않는다.
3. `front/`나 배포 산출물(`front/public/data`)이 바뀌면 **Vercel preview 배포로 테스트**한다(§4). 문서만 바뀐 작업은 생략할 수 있다.
4. preview에서 이상이 없을 때 PR(병합 요청)을 연다. PR에 변경 요약, 확인 방법(preview URL 포함), 영향 영역, 남은 이슈를 적는다.
5. **`main` 병합은 신중하게**: 담당자 리뷰(프론트는 정선우 님) 후에만 병합한다. 에이전트는 사람의 명시적 지시 없이 병합하지 않는다.
6. 프로덕션 배포(`--prod`)는 `main` 병합 후 `main`에서만 한다.

금지: `main` 직접 push, force push, 리뷰 없는 병합, preview 테스트 없이 프론트 PR 열기.

## 4. Vercel 배포

Vercel 프로젝트는 GitHub 자동 배포가 연결되어 있지 않아 CLI로 배포한다. 무료 플랜 업로드 파일 수 제한 때문에 `--archive=tgz`가 필요하다.

```bash
cd front
npx vercel deploy --yes --archive=tgz          # preview (브랜치 테스트)
npx vercel deploy --prod --yes --archive=tgz   # production (main 병합 후에만)
```

- 환경변수 `NEXT_PUBLIC_KAKAO_JS_KEY`: 카카오 **JavaScript 키**(도메인 제한 공개키)라 `--type config`로 등록한다. REST 키 등 비밀값에는 `NEXT_PUBLIC_`을 절대 붙이지 않는다.
- 카카오 지도는 **카카오 콘솔 [앱] → [플랫폼 키] → [JavaScript 키] → [JavaScript SDK 도메인]에 등록된 도메인에서만** 뜬다. 현재 등록: `https://imnjang-pi.vercel.app`.
  preview URL은 배포마다 바뀌어 미등록 상태이므로 preview에서는 지도가 좌표 미리보기로 대체되는 것이 정상이다.
  지도까지 확인하려면 고정 preview 주소 `https://imnjang-preview.vercel.app`(카카오 도메인 등록 대상)에 테스트할 배포를 연결한다.
  이 주소는 **한 번에 한 배포만** 가리키므로, 다른 PR을 테스트할 때마다 다시 연결하고 PR 코멘트에 어떤 브랜치를 연결했는지 적는다.

  ```bash
  npx vercel deploy --yes --archive=tgz                                   # 출력의 Preview URL 확인
  npx vercel alias set <Preview URL 호스트> imnjang-preview.vercel.app   # 고정 주소를 이 배포로 전환
  ```
- preview 환경에서 키가 필요하면 Preview 환경변수에도 같은 키를 등록해야 한다.

## 5. 로컬 검증 명령

Python은 루트 `.venv`, 프론트는 `front/`에서 실행한다.

```bash
# 가격·payload 파이프라인 (순서는 docs/reproduce.md)
.venv/bin/python models/price/32.build_price_cells.py
.venv/bin/python front/build/36.build_payload.py        # front/public/data 재생성
.venv/bin/python front/build/37.check_wording.py         # 금지 표현·필드명·source 분기 검사

# 프론트
cd front && npx tsc --noEmit && npm run build
```

- 프론트 코드나 payload를 바꾸면 `37.check_wording.py`와 `npm run build`를 둘 다 통과시킨다.
- 가격 로직을 바꾸면 `models/price/30.spike_hedonic.py`의 서비스 route OOT(30.4/30.5)로 전후를 비교하고, 채택 기준은 결과를 보기 전에 정해 `docs/decisions.md`에 적는다.

## 6. 제품 표기 규칙

- 가격은 **baseline-first**: 실거래 셀(`CELL_LAST`) → 같은 면적 다른 층대 실거래(`AREA_LAST`) → 단지 평균(`COMPLEX_MEAN`) → 단지 거래 이력이 없을 때만 모델 추정(`MODEL`). 임대 관련 명칭 단지는 `EXCLUDED`.
- "저평가/고평가/적정가/싸다/비싸다" 같은 판단 표현을 쓰지 않는다. 사실(언제·어느 층·얼마 거래)만 쓴다.
- 추정값에는 반드시 "추정"과 구간을 붙인다. 모델 구간을 "80% 보장"이라 쓰지 않는다.
- **런타임 생성형 AI 호출 0회**. 개발에는 AI를 쓰되 서비스 기능으로 넣지 않는다.

## 7. 데이터·보안

- `.env`, `front/.env.local`은 `chmod 600`, 절대 커밋하지 않는다. 키 이름만 `.env.example`에 적는다.
- API 호출 스크립트는 로그에 키를 남기지 않는다(`mask_key()` 사용). 스크립트는 `load_env("KEY")`로 `.env`를 읽는다.
- 대용량·원천 산출물(`output/raw`, 수백 MB 거래 원장 등)은 커밋하지 않는다. `front/public/data`는 배포 산출물이라 추적한다.
- `front/public/data/` 9,000여 개 파일을 전수 스캔(`find | xargs cat` 등)하지 않는다. 필요한 단지 몇 개만 연다.

## 8. 코드 스타일

- 주변 코드의 주석 밀도·명명·관용구를 따른다. 사용자에게 보이는 문구와 주석은 한국어, 식별자는 영어.
- 스크립트 이름은 `번호.이름.py`, 산출물은 `output/{번호}.{순서}.{이름}`.
- 최소 diff. 요청받지 않은 추상화·새 의존성을 추가하지 않는다.
- Commit 메시지는 `feat(front): …`, `fix(price): …`, `docs: …` 형식으로 무엇을 왜 바꿨는지 적는다.
