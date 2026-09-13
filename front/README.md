# front

서울 아파트 비교 웹앱. **Next.js 15 (App Router) + TypeScript + Tailwind CSS v4 + shadcn/ui**.

**원칙: 런타임은 조회만 한다.** 모든 지표는 배치에서 사전 계산해 정적 JSON으로 떨어뜨린다. 서버 런타임도, 서비스 중 연산도, LLM 호출도 없다 (`plan.md` §2 제약 1). 그래서 `next.config.ts`에 `output: "export"`를 두고 정적 사이트로 빌드한다.

## 명령어

저장소 루트가 아니라 `front/`에서 실행한다.

```bash
cd front
npm install
```

| 명령어 | 하는 일 |
|---|---|
| `npm run dev` | 개발 서버 (http://localhost:3000). HMR 동작 |
| `npm run build` | 타입 검사 + 정적 export → `out/` |
| `npm run start` | 빌드 결과(`out/`)를 정적 서버로 띄운다 |
| `npm run preview` | `build` 후 바로 `start` |
| `npm run typecheck` | `tsc --noEmit` |
| `npm run lint` | `next lint` |
| `npm run check` | 타입 검사 + 문구/스키마 검사 |
| `npm run payload` | `build/36.build_payload.py` 실행 (Python 필요) |

> `npm run start`는 Next 서버가 아니라 `serve out`이다. 정적 export라 서버 런타임이 없다.

## 구조

```text
front/
  app/
    layout.tsx         html/body, metadata, viewport
    page.tsx           상태 소유 (검색어·필터·선택·담기·비교서)
    globals.css        Tailwind v4 + 디자인 토큰 + keyframes
  components/
    search-panel.tsx   검색, 빠른 필터, 결과 리스트
    map-preview.tsx    좌표 기반 위치 미리보기
    detail-panel.tsx   선택 단지 요약
    basket-bar.tsx     하단 비교 바
    compare-section.tsx 비교서 (가격·추이·유사단지·환경·확인사항)
    ui/                shadcn/ui 스타일 프리미티브 (button, badge, card, input, section-heading)
  lib/
    types.ts           36.build_payload.py 산출 스키마
    data.ts            payload fetch, 데모 폴백, 좌표 투영
    format.ts          단위·라벨 포매팅
    use-basket.ts      담은 목록 + localStorage
    utils.ts           cn()
  build/
    36.build_payload.py   output/ 테이블 → public/data/ payload
    37.check_wording.py   문구 규칙 + JSON 필드명 검사
  public/data/         payload 산출물 (.gitignore 대상)
  next.config.ts       output: "export"
  vercel.json          정적 배포 설정
```

`shadcn/ui`는 CLI로 받지 않고 소스를 저장소에 직접 두었다 (shadcn이 원래 그렇게 동작한다). 프리미티브는 `components/ui/`에 있고 직접 고쳐 쓰면 된다.

## 데이터

`public/data/`가 없으면 `lib/data.ts`의 `DEMO_COMPLEXES` 6곳으로 자동 폴백한다. 화면 작업만 할 때는 payload를 빌드하지 않아도 된다.

payload 생성:

```bash
npm run payload           # = python ../front/build/36.build_payload.py
```

Python은 `numpy`, `pandas`가 필요하다. 저장소 루트에 `.venv`를 두는 것을 기준으로 한다.

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install pandas numpy   # Windows
```

- 입력: `output/23.*`, `output/32.*`, `output/33.*`, `output/34.1`, `output/35.2`, `output/14.1`
- 출력: `front/public/data/index.json`, `front/public/data/complex/<apt_seq>.json.gz`
- 규모: 9,160개 단지, 총 15.7MB (`index.json` gzip 207.6KB). `complex/`는 매 실행마다 지우고 다시 만든다
- 끝에 자체 검증이 돌며 `index.json` gzip 500KB 상한 등 9개 항목을 확인한다

`index.json`에는 `match_confidence`가 `HIGH`가 아닐 때만 실린다. 9,160행에 기본값을 매번 싣지 않기 위해서다. 프론트는 값이 없으면 `HIGH`로 읽는다 (`lib/format.ts`의 `matchConfidence()`).

`front/public/data/`는 `.gitignore` 대상이다. 재빌드마다 전량이 바뀌므로 개발 중에는 추적하지 않고, **배포 직전에 한 번만 commit한다.**

## 검사

```bash
npm run check
```

`37.check_wording.py`가 `app/`, `components/`, `lib/`의 `.ts` / `.tsx` / `.css`를 스캔해서 본다.

1. 금지 표현 (`적정시세`, `저평가`, `고평가` 등 — `docs/product-identity.md` §6)
2. 참조하는 JSON 필드가 `36.build_payload.py` 산출 스키마에 실제로 있는지
3. `price[].source` 4종(`CELL_LAST` / `COMPLEX_MEAN` / `MODEL` / `EXCLUDED`) 분기가 모두 살아 있는지

2번 검사는 `data.` / `entry.` / `item.` / `c.` / `s.` / `d.` 형태의 속성 접근을 정규식으로 본다. 다른 이름으로 구조분해하면 검사망을 빠져나간다. 스키마 필드를 새로 쓸 때는 `36.build_payload.py`, `lib/types.ts`, `37.check_wording.py`의 `SCHEMA` 세 곳을 같은 PR에서 고친다.

## 배포

Vercel. `vercel.json`이 framework·빌드·출력 경로를 지정한다.

| 항목 | 값 |
|---|---|
| Root Directory | `front` |
| Framework | Next.js |
| Build Command | `next build` |
| Output Directory | `out` (정적 export) |

`/data/*`에는 `Cache-Control: public, max-age=3600`이 붙는다.

CLI 배포:

```bash
cd front
vercel          # preview
vercel --prod   # production
```

배포 순서:

1. `npm run payload`
2. `npm run check && npm run build`
3. `front/public/data/`를 commit (배포 직전 1회)
4. PR merge 또는 `vercel --prod`

## 디자인 규칙

- 포인트 컬러 `#4136E8` 하나에 선택·활성·주요 행동을 모은다
- 초록/주황/빨강 상태색은 쓰지 않는다. 상태는 회색 톤 + 텍스트 라벨로 구분한다
- border radius는 10~15px 범위 (`--radius-sm/md/lg`)
- 모바일 우선. 390px에서 가로 스크롤이 없어야 한다

| 폭 | 레이아웃 |
|---|---|
| ~767px | 단일 컬럼 |
| 768~1023px | 검색 + 지도 2단 |
| 1024px~ | 검색 / 지도 / 선택 패널 3단 |

애니메이션은 `transform`과 `opacity`만 쓰고 120~220ms로 제한한다. `prefers-reduced-motion: reduce`에서 전부 비활성화된다.

## 알려진 제약

- **지도는 아직 실제 배경지도가 아니다.** `SEOUL_BOUNDS` 기준 좌표 투영 미리보기다. VWorld API 키가 준비되면 `lib/data.ts`의 `projectToMap()`과 `components/map-preview.tsx`를 OpenLayers adapter로 교체한다.
- **단지 payload는 `.json.gz`로만 생성된다.** 정적 호스팅은 이 파일을 `Content-Type: application/gzip`으로 그냥 내려주므로 브라우저가 압축을 풀지 않는다 (`next dev`에서 확인). 그래서 `lib/data.ts`의 `fetchComplex()`가 `DecompressionStream("gzip")`으로 직접 푼다. 서버가 `Content-Encoding: gzip`을 붙여주는 환경이면 그대로 쓴다. **Vercel preview에서 한 번 더 확인할 것** — 아직 배포해보지 않았다.
- `npm audit`에 `postcss` 관련 경고가 뜬다. Next 15의 전이 의존성이고 빌드 타임에만 쓰인다. 해소하려면 Next 16으로 올려야 해서 지금은 두었다.
