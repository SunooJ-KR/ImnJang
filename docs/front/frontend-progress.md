# 프론트엔드 진행 현황

> 작성일: 2026-09-13
> 브랜치: `feat/frontend-map-ui-draft`
> 이 폴더(`front/docs/`)는 `.gitignore` 대상이며 로컬 작업 메모로만 사용한다.

## 1. 현재 목표

지도 API 키는 추후 연동하고, 우선 `front/docs/web_ui_design_theme.md`의 방향을 기준으로 지도 기반 탐색 UI의 정적 프론트 구조를 구현한다.

디자인 핵심은 다음과 같다.

- 저채도 지도형 배경
- 정돈된 카드 기반 정보 구조
- 포인트 컬러 `#4136E8`
- 음영 컬러 `#F6F7F9`
- 단일 톤 컬러 시스템
- 지도와 리스트가 함께 동작하는 위치 기반 탐색 경험
- 모바일 우선 레이아웃

추가 디자인 결정:

- 버튼 border radius는 10~15px 범위로 제한한다.
- 초록/주황/빨강 상태색은 지양하고 포인트 컬러, 회색 톤, 텍스트 라벨로 상태를 구분한다.
- 선택, 활성, 주요 행동은 `#4136E8`에 집중한다.

## 2. 현재 변경 파일

Git 기준 변경 파일:

```text
.gitignore
front/public/index.html
front/public/styles.css
front/public/app.js
```

로컬 문서 파일:

```text
front/docs/web_ui_design_theme.md
front/docs/frontend-composition-draft.md
front/docs/frontend-progress.md
```

`front/docs/`는 `.gitignore`에 추가되어 Git에 포함되지 않는다.

## 3. 구현 완료

### 3.1 앱 레이아웃

`front/public/index.html`을 지도 기반 앱 셸로 재구성했다.

현재 화면 구조:

```text
상단 바
  브랜드
  탐색 / 비교서 링크

메인
  검색 패널
  지도형 위치 미리보기
  선택 단지 요약 패널
  비교서 영역

하단 고정 비교 바
푸터
```

### 3.2 검색 패널

구현된 기능:

- 단지명 검색
- 구 이름 검색
- 법정동 이름 검색
- 빠른 필터
  - 전체
  - 분석 가능
  - 확인 필요
- 검색 결과 리스트
- 결과 항목 클릭 시 선택 패널과 지도 포인트 동기화
- 결과 항목에서 바로 비교 담기

### 3.3 지도형 위치 미리보기

지도 API 키가 아직 없으므로 실제 VWorld 타일은 붙이지 않았다.

현재 구현:

- 서울 경계 대략 범위(`SEOUL_BOUNDS`) 기준으로 `lat/lng`를 화면 좌표로 투영
- 저채도 지도 느낌의 배경
- 한강을 암시하는 낮은 대비의 수계 레이어
- 단지 위치를 원형 포인트로 표시
- 선택된 단지는 강조 표시
- `match_confidence=FAILED` 단지는 회색 포인트로 표시

주의:

- 현재 지도는 실제 배경지도 타일이 아니라 좌표 기반 미리보기다.
- 정확한 도로/건물/지번 맥락은 VWorld API 연동 후 제공한다.

### 3.4 선택 단지 패널

검색 결과 또는 지도 포인트 선택 시 다음 정보를 표시한다.

- 단지명
- 구/법정동
- 준공연도
- 세대수
- 위도/경도
- 분석 가능 여부
- `FAILED` 사유 문구
- 비교에 담기 버튼

### 3.5 비교 담기

기존 흐름을 유지했다.

- 최대 4개 단지까지 담기
- `localStorage` 저장
- 하단 고정 비교 바 표시
- 담김 상태 버튼 반영
- 비우기 지원

### 3.6 비교서

기존 비교서 렌더링을 유지하면서 새 레이아웃 안에 배치했다.

표시 항목:

- 가격
- 5년 추이
- 비슷한 조건의 다른 단지
- 환경
- 현장에서 확인할 것

가격 문구는 계속 `price[].source` 기준으로 분기한다.

## 4. 데이터 처리 상태

현재 `front/public/data/`는 `.gitignore` 대상이다.

앱 동작 방식:

1. `front/public/data/index.json` 로드 시도
2. 성공하면 실제 단지 인덱스 사용
3. 실패하면 화면 확인용 데모 데이터 사용

데모 데이터는 `app.js` 내부 `DEMO_COMPLEXES`에 있다.

실제 인덱스 계약:

```json
{
  "id": "apt_seq",
  "n": "단지명",
  "g": "구",
  "u": "법정동",
  "lat": 37.5,
  "lng": 127.0,
  "y": 2015,
  "h": 1608
}
```

현재 빠른 필터의 `분석 가능` / `확인 필요`는 `match_confidence`가 있을 때만 의미 있게 동작한다.

실제 `index.json`에는 아직 `match_confidence`가 없으므로, 다음 단계에서 아래 중 하나를 결정해야 한다.

1. `index.json`에 `match_confidence`를 추가한다.
2. 지도/검색 리스트에서는 `FAILED` 상태 표시를 하지 않고, 상세 JSON 로드 후에만 표시한다.
3. 지도용 별도 경량 인덱스를 만든다.

추천은 1번이다.

## 5. 검증 완료

실행한 검사:

```text
node --check front/public/app.js
python front/build/37.check_wording.py
git diff --check
```

결과:

- JS 문법 검사 통과
- 금지 문구 검사 통과
- JSON 필드명 검사 통과
- `price[].source` 4종 분기 검사 통과
- whitespace 검사 통과

로컬 서버 확인:

```text
python -m http.server 4173
```

응답 확인:

```text
http://localhost:4173/
http://localhost:4173/app.js
http://localhost:4173/styles.css
```

모두 HTTP 200 응답 확인.

## 6. 확인하지 못한 것

인앱 브라우저가 현재 세션에서 연결되지 않아 실제 스크린샷 검증은 하지 못했다.

Playwright도 현재 로컬 의존성으로 잡히지 않아 자동 렌더링 테스트를 실행하지 못했다.

따라서 다음은 사람 눈으로 확인해야 한다.

- 400px 모바일 폭에서 가로 스크롤이 없는지
- 지도 툴바가 마커를 과도하게 가리지 않는지
- 검색 결과가 많을 때 좌측 패널 스크롤이 자연스러운지
- 하단 비교 바가 콘텐츠를 가리지 않는지
- 선택 패널의 위도/경도 숫자가 과하게 눈에 띄지 않는지

## 7. 다음 작업

### 7.1 실제 화면 QA

우선 브라우저에서 다음 URL을 확인한다.

```text
http://localhost:4173/
```

확인할 화면 폭:

- 390px
- 430px
- 768px
- 1280px

확인할 동작:

- 검색 입력
- 빠른 필터 전환
- 검색 결과 클릭
- 지도 포인트 클릭
- 비교에 담기
- 비교 바 비우기
- 비교서 열기

### 7.2 `index.json` 지도용 필드 확장

현재 지도/리스트에서 `FAILED` 단지를 정확히 표시하려면 `front/build/36.build_payload.py`의 `index_rows`에 필드를 추가해야 한다.

후보:

```json
{
  "m": "FAILED"
}
```

다만 `front/build/37.check_wording.py`의 허용 인덱스 필드에도 함께 추가해야 한다.

더 읽기 쉬운 대안:

```json
{
  "match_confidence": "FAILED"
}
```

인덱스 용량은 조금 늘지만 프론트 코드와 검사 스크립트가 단순해진다.

### 7.3 VWorld 지도 API 연동

추후 키가 준비되면 다음 순서로 붙인다.

1. VWorld API 키 발급
2. 사용 도메인 제한 설정
3. `front/public/config.js` 또는 HTML inline config 추가
4. OpenLayers 로드
5. VWorld WMTS Base tile layer 생성
6. 현재 `renderMap()`을 OpenLayers vector layer adapter로 교체
7. 지도 클릭 이벤트를 `selectItem()`에 연결
8. 줌아웃 상태에서 cluster 적용

VWorld WMTS URL 형태:

```text
https://api.vworld.kr/req/wmts/1.0.0/{VWORLD_KEY}/Base/{z}/{y}/{x}.png
```

현재 코드에서 교체 지점:

```text
projectToMap()
renderMap()
selectItem()
```

### 7.4 지도 성능 개선

실제 전체 데이터는 9,160개 단지다.

현재 임시 지도는 최대 220개 포인트만 그린다.

VWorld/OpenLayers 연동 후에는 다음 방식으로 바꾼다.

- 전체 단지는 `ol.source.Vector`
- 줌아웃은 `ol.source.Cluster`
- 클릭된 cluster는 확대
- 단일 feature 클릭은 선택 패널 표시
- 상세 JSON은 비교서 또는 상세 열람 시점까지 지연 로드

### 7.5 비교서 UX 개선

지도 중심 화면이 잡힌 뒤 비교서를 다듬는다.

우선순위:

- 가격/환경/현장 확인 블록의 시각 위계 개선
- 모바일에서 카드 길이 축소
- 접기/펼치기 검토
- `MODEL` 가격과 실거래 가격의 시각 구분 강화
- 유사 거래의 실거래/보정가격 차이 설명 위치 개선

### 7.6 접근성 정리

현재 지도 포인트는 `button`이라 키보드 접근은 가능하다.

추가 작업:

- 선택된 포인트의 `aria-pressed` 부여
- 검색 결과 선택 상태의 `aria-current` 검토
- 지도 패널의 설명 문구 보강
- 색상 외 텍스트/형태로 상태 구분 유지

### 7.7 UI 현대화 (패딩 축소 / 트렌드 / 마이크로 애니메이션 / 반응형)

> 상태: 구현 완료 (2026-09-13). 아래 계획대로 반영했고, 변경점은 7.7.7에 정리했다.

요청 범위:

1. 패딩 축소
2. 최신 트렌드에 맞춘 UI 정리
3. 마이크로 애니메이션 추가
4. 화면 사이즈별 최적화

#### 7.7.1 패딩 축소

현재 카드/패널/섹션 여백이 모바일에서 과하다.

작업 방향:

- 간격 값을 CSS 변수(`--space-1` ~ `--space-6`)로 통일한다.
- 카드 내부 패딩을 축소하고, 밀도를 높인다.
- 섹션 사이 간격은 패딩 대신 `gap`으로 관리한다.
- 하드코딩된 `px` 여백을 변수로 치환한다.

#### 7.7.2 최신 트렌드 UI

기존 디자인 결정(포인트 `#4136E8`, 음영 `#F6F7F9`, radius 10~15px, 상태색 지양)은 유지한다.

추가 방향:

- 테두리보다 낮은 대비의 면 분리를 우선한다.
- 그림자는 얕고 넓게, 1~2단계만 사용한다.
- 타이포 스케일을 정리해 위계를 텍스트 크기/굵기로 표현한다.
- 검색 패널과 지도 패널의 경계를 부드럽게 처리한다.
- 다크 모드는 이번 범위에서 제외한다.

#### 7.7.3 마이크로 애니메이션

대상과 방식:

- 지도 포인트 선택: scale + 색 전환
- 검색 결과 항목 hover/active: 배경 전환
- 비교 담기 버튼: 담김 상태 전환 피드백
- 하단 비교 바: 등장/퇴장 slide
- 선택 패널 교체: 짧은 fade
- 비교서 열기: 높이/투명도 전환

제약:

- duration 120~220ms
- CSS `transition` 우선, JS 애니메이션 지양
- `transform`, `opacity`만 애니메이트 (layout 속성 금지)
- `@media (prefers-reduced-motion: reduce)`에서 전부 비활성화

#### 7.7.4 화면 사이즈 최적화

브레이크포인트 기준:

```text
~430px    모바일 (단일 컬럼, 지도 축소)
431~767px 큰 모바일
768~1023px 태블릿 (검색 + 지도 2단)
1024px~   데스크톱 (검색 / 지도 / 선택 패널 3단)
```

작업 방향:

- 고정 `px` 폭 제거, `clamp()` / `minmax()` 사용
- 지도 높이는 뷰포트 기준(`dvh`)으로 조정
- 데스크톱에서 검색 결과 리스트만 독립 스크롤
- 하단 비교 바는 모바일만 고정, 데스크톱은 인라인 배치
- 400px에서 가로 스크롤 0 유지

#### 7.7.5 검증

```text
node --check front/public/app.js
python front/build/37.check_wording.py
git diff --check
```

확인 폭: 390px / 430px / 768px / 1280px

확인 항목:

- 가로 스크롤 없음
- 애니메이션 중 레이아웃 흔들림 없음
- `prefers-reduced-motion`에서 전환 정지
- 포커스 링이 애니메이션에 가려지지 않음

#### 7.7.6 영향 파일

```text
front/public/styles.css   (주 변경)
front/public/index.html   (구조 조정 시)
front/public/app.js       (상태 클래스 토글 시)
```

#### 7.7.7 구현 결과

`styles.css` 전면 개편:

- `--space-1`~`--space-6`, `--fs-xs`~`--fs-xl`, `--shadow-1`/`--shadow-2`, `--ease`, `--t-fast`/`--t-base`/`--t-slow` 토큰 도입
- 패널 패딩 `16px` → `var(--space-4)`(12px), 768px 이상에서만 `var(--space-5)`(16px)
- 검색 결과/칩/fact 카드의 `border`를 `transparent`로 두고 배경 대비로 면 분리
- 그림자 3종 이상 혼용을 2단계로 축소
- 활성 필터 칩을 solid `--accent`로 바꿔 선택 상태를 강화

`index.html`:

- `#basket`의 `hidden` 속성을 `class="basket is-hidden"`으로 교체 (transition 동작을 위해)

`app.js`:

- `renderBasket()`이 `hidden` 대신 `is-hidden` 클래스를 토글
- `renderSelected()`에 `renderedDetailId` 가드 추가. 선택이 실제로 바뀔 때만 fade 재생
  (검색 입력마다 `renderSelected()`가 호출되므로 무조건 재생하면 깜빡인다.)

마이크로 애니메이션 적용 목록:

- 지도 마커: hover `scale(1.15)`, selected `scale(1.35)` + 링 확대
- 검색 결과: 배경 전환, `:active` 미세 축소
- 버튼: 배경/보더 전환, `:active` `scale(0.97)`
- 하단 비교 바: `translateY` slide + opacity + visibility
- 선택 패널: `fade-in`
- 비교서 카드: `rise-in` + 40ms 간격 stagger
- 전부 `transform`/`opacity`만 사용. `prefers-reduced-motion: reduce`에서 1ms로 무력화

계획과 달라진 점:

- 데스크톱(1024px+)에서 비교 바를 DOM 이동 없이 가운데 떠 있는 카드형 고정 바로 처리했다.
  `#basket`이 `<main>` 밖에 있어 인라인 배치는 DOM 구조 변경이 필요하고, 시각 효과 차이가 작다.

검증 결과:

- `node --check front/public/app.js` 통과
- `python front/build/37.check_wording.py` 통과 (표현/필드명/`source` 4종 분기)
- `git diff --check` 통과
- 브라우저 실측: 360 / 390 / 430 / 768 / 1024 / 1280px에서 `scrollWidth === clientWidth`, 가로 스크롤 없음
- 담기 → 비교 바 노출, `담김` 상태 반영 확인

## 8. 커밋 전 체크리스트

커밋 전 실행:

```text
node --check front/public/app.js
python front/build/37.check_wording.py
git diff --check
git status --short --branch
```

브라우저 확인:

```text
http://localhost:4173/
```

커밋 대상에 포함되어야 하는 파일:

```text
.gitignore
front/public/index.html
front/public/styles.css
front/public/app.js
```

커밋 대상에 포함되면 안 되는 파일:

```text
front/docs/*
front/public/data/*
```

## 9. 권장 커밋 메시지

```text
feat(front): add map-first search layout
```

## 10. Next.js 프로젝트 전환 (2026-09-13)

정적 HTML/CSS/JS 구조를 `plan.md` §122 스택으로 재구현했다.

### 10.1 스택

Next.js 15 (App Router) + TypeScript + Tailwind CSS v4 + shadcn/ui.
런타임 서버가 없으므로 `next.config.ts`에 `output: "export"`를 두고 정적 사이트로 빌드한다.

### 10.2 삭제한 파일

```text
front/public/index.html
front/public/app.js
front/public/styles.css
```

`front/public/`은 이제 Next.js의 정적 자산 디렉터리로만 쓴다.
`36.build_payload.py`의 출력 경로(`front/public/data/`)는 그대로 유효하다.

### 10.3 추가한 파일

```text
front/package.json           dev/build/start/preview/typecheck/lint/check/payload
front/tsconfig.json
front/next.config.ts         output: "export"
front/postcss.config.mjs     @tailwindcss/postcss
front/.gitignore             node_modules, .next, out, next-env.d.ts
front/app/layout.tsx
front/app/page.tsx           상태 소유
front/app/globals.css        디자인 토큰 + keyframes
front/components/search-panel.tsx
front/components/map-preview.tsx
front/components/detail-panel.tsx
front/components/basket-bar.tsx
front/components/compare-section.tsx
front/components/ui/{button,badge,card,input,section-heading}.tsx
front/lib/{types,data,format,use-basket,utils}.ts
```

### 10.4 함께 고친 것

- `front/vercel.json`: `framework: nextjs`, `buildCommand: next build`, `outputDirectory: out`
- `front/build/37.check_wording.py`:
  - `TARGETS`를 `front/public/index.html`·`app.js` 고정에서 `app/`·`components/`·`lib/`의 `.ts`/`.tsx`/`.css` 재귀 스캔으로 변경
  - 필드명 대조에서 `lib/types.ts` 제외 (스키마 선언 자체라 접근이 아님)
  - source 분기 정규식을 큰따옴표도 받도록 수정
- `front/README.md`: 명령어/구조/데이터/검사/배포/제약 재작성

### 10.5 이전 CSS와의 차이

디자인은 "처음부터 다시" 방침으로 Tailwind 유틸리티로 재작성했다.
다만 제품 결정은 그대로 유지했다.

- 포인트 컬러 `#4136E8` 하나에 선택·활성·주요 행동 집중
- 초록/주황/빨강 상태색 미사용
- radius 10~15px (`--radius-sm/md/lg`)
- 모바일 우선, 브레이크포인트 768 / 1024
- 애니메이션은 `transform`/`opacity`만, `prefers-reduced-motion`에서 비활성화

### 10.6 검증 결과

```text
npm run build      통과 (정적 export, / 17.2 kB, First Load JS 120 kB)
npm run typecheck  통과
37.check_wording.py 통과 (금지 표현 / 필드명 / source 4종)
serve out          HTTP 200, 마크업에 브랜드명 포함
```

브라우저 실측(`npm run dev`, http://localhost:3000):

- 360 / 390 / 430 / 768 / 1024 / 1280px 전부 `scrollWidth === clientWidth`, 가로 스크롤 없음
- 콘솔 에러 없음
- 데모 폴백 6곳 렌더, 담기 2곳 → 하단 비교 바 노출, `담김` 상태 반영
- 비교하기 클릭 시 payload가 없어 "단지 정보를 불러오지 못했습니다." 표시 (의도된 경로)

### 10.7 확인하지 못한 것

- **비교서 실제 렌더링.** `front/public/data/`가 없어 `ComplexPayload` 기반 카드(가격 4종 분기, 스파크라인, 유사 단지, 환경 표)를 실제 데이터로 그려보지 못했다. `npm run payload` 후 재확인 필요.
- **`.json.gz` 파싱.** `fetchComplex()`는 `.json` → `.json.gz` 순으로 시도한다. 서버가 `Content-Encoding: gzip`을 붙이지 않으면 깨진다. 로컬과 Vercel preview 양쪽에서 확인해야 한다.
- **Vercel 실제 배포.** 설정만 바꿨고 배포는 하지 않았다.

## 11. index 필드 확장 + payload 실검증 (2026-09-13)

§7.2 와 §10.7 을 함께 처리했다.

### 11.1 index.json 에 match_confidence 추가

`36.build_payload.py` 의 `index_rows` 에 `match_confidence` 를 넣되,
**`HIGH` 가 아닐 때만** 싣는다. 9,160행에 기본값을 매번 실을 이유가 없고,
프론트는 값이 없으면 `HIGH` 로 읽는다 (`lib/format.ts` 의 `matchConfidence()`).

`37.check_wording.py` 의 `SCHEMA["index"]` 에도 필드를 추가했다.

실제 분포:

```text
FAILED                3,112
LOW                   3,548
MEDIUM                1,759
(생략 = HIGH)           741
합계                  9,160
```

`index.json` gzip 207.6KB — 500KB 상한 안이다.
이제 지도와 검색 리스트의 `분석 가능` / `확인 필요` 구분이 상세 JSON 없이 정확하다.

### 11.2 payload 빌드 환경

`numpy`, `pandas` 가 없어 빌드가 실패했다. 저장소 루트에 `.venv` 를 만들고
`pandas 3.0.5`, `numpy 2.5.3` 을 설치했다. `.venv/` 는 이미 `.gitignore` 대상이다.

빌드 결과: 9,160개 단지, 총 15.7MB, 자체 검증 9개 항목 전부 PASS.
(README 에 적혀 있던 55.9MB 는 옛 수치라 15.7MB 로 고쳤다.)

### 11.3 .json.gz 파싱 — 위험이 실재했고 고쳤다

`next dev` 응답 헤더를 확인했다.

```text
Content-Type: application/gzip
(Content-Encoding 없음)
```

즉 브라우저가 압축을 풀지 않으므로 `res.json()` 이 그대로 깨진다.
`lib/data.ts` 의 `fetchComplex()` 가 표준 `DecompressionStream("gzip")` 으로
직접 풀도록 고쳤다. 서버가 `Content-Encoding: gzip` 을 붙여주는 환경이면
이미 풀린 상태이므로 헤더를 보고 그대로 쓴다.

빌드 산출물의 확장자를 바꾸거나 호스팅 헤더에 의존하는 대신 브라우저 표준
API를 쓴 이유는, dev / preview / 프로덕션에서 동작이 갈리지 않게 하기 위해서다.

### 11.4 검증 결과

`fetchComplex()` 와 동일한 경로를 Node에서 실행해 확인했다.

```text
index rows: 9,160  as_of: 2026-08-16
11110-100 MID그린(7동)     match=FAILED price=1(MODEL)                series=0 comps=7  env.sun=null
11110-152 롯데캐슬천지인    match=HIGH   price=9(CELL_LAST,COMPLEX_MEAN) series=3 comps=30 env.sun=5.42
11500-85  신도금잔디        match=LOW    price=12(COMPLEX_MEAN,CELL_LAST) series=4 comps=32 env.sun=8.05
실제 payload에서 확인된 source: CELL_LAST, COMPLEX_MEAN, EXCLUDED, MODEL
```

비교서가 분기하는 가격 source 4종이 실제 데이터에 모두 존재한다.

```text
npm run check   통과 (타입 + 금지 표현 + 필드명 + source 4종)
npm run build   통과 (/ 17.3 kB, First Load JS 120 kB)
```

### 11.5 확인하지 못한 것

- **브라우저 화면에서의 비교서 렌더링.** 인앱 브라우저가 이번 세션 후반부터
  `Frame with ID 0 is showing error page` 로 계속 실패했다 (`curl` 은 200).
  payload 파싱과 데이터 내용은 Node로 확인했지만, 카드 레이아웃·스파크라인·
  환경 표가 실제로 어떻게 보이는지는 사람 눈 확인이 필요하다.
  `npm run dev` 후 http://localhost:3000 에서 단지 2~4곳 담고 비교하기.
- **Vercel 실제 배포.** 설정만 바꿨고 배포는 하지 않았다.

### 11.6 남은 다음 작업

§7.3 VWorld 지도 연동 (API 키 대기), §7.4 지도 성능(cluster), §7.5 비교서 UX,
§7.6 접근성 정리.
