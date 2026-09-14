# 프론트엔드 구성 초안

> 이 문서는 프론트 작업 중 빠르게 갱신하는 로컬 초안이다.
> `front/docs/`는 Git 추적에서 제외한다.

## 1. 목표

서울 아파트 단지를 실제 지도 위에서 정확한 위치로 찾고, 관심 단지를 담아 같은 기준으로 비교한다.

프론트는 계산 엔진이 아니다. 이미 생성된 정적 JSON과 좌표를 읽어 검색, 지도 표시, 담기, 비교서 렌더링만 담당한다.

## 2. 기본 원칙

- 지도는 탐색 보조 수단이다.
- 지도 SDK가 실패해도 검색, 담기, 비교는 동작해야 한다.
- 런타임에서 실거래 API, 가격 모델, LLM을 호출하지 않는다.
- 좌표는 `front/public/data/index.json`의 `lat`, `lng`를 그대로 사용한다.
- 단지 상세 데이터는 사용자가 클릭하거나 비교할 때만 로드한다.
- `match_confidence=FAILED` 단지는 숨기지 않고 회색 마커와 사유로 표시한다.
- `null`은 0이 아니라 정보 없음으로 표시한다.

## 2.1 디자인 토큰

- 포인트 컬러: `#4136E8`
- 음영 컬러: `#F6F7F9`
- 활성/선택/주요 행동은 포인트 컬러 하나로 통일한다.
- 상태 구분은 추가 강조색보다 포인트 컬러의 농도, 회색 톤, 텍스트 라벨을 우선한다.
- 일반 버튼의 border radius는 10~15px 범위로 둔다.
- 지도 포인트는 위치 표시라는 성격 때문에 원형을 유지한다.

## 3. 지도 기술 방향

### 3.1 1차 구현

VWorld 2D 지도 API와 OpenLayers를 사용한다.

- 배경지도: VWorld WMTS `Base`
- 지도 엔진: OpenLayers
- 단지 위치: `index.json` 기반 point feature
- 줌아웃: cluster layer
- 클릭: 하단 시트 또는 결과 패널에 단지 요약 표시

VWorld WMTS URL 형태:

```text
https://api.vworld.kr/req/wmts/1.0.0/{VWORLD_KEY}/Base/{z}/{y}/{x}.png
```

위성/하이브리드가 필요하면 별도 레이어로 확장한다.

```text
Satellite: jpeg
Hybrid: png
```

### 3.2 인증키 처리

브라우저 지도 키는 완전히 숨길 수 없다.

- VWorld 콘솔에서 사용 도메인을 제한한다.
- 로컬 개발용 도메인과 배포 도메인을 따로 등록한다.
- 코드에는 키를 직접 박지 않고 별도 설정 파일 또는 빌드 치환값을 둔다.
- 키가 없으면 지도 영역 대신 리스트 검색 UI를 보여준다.

정적 앱 기준 후보:

```js
window.IMNJANG_CONFIG = {
  vworldKey: '...'
};
```

## 4. 화면 구조

### 4.1 검색 화면

```text
검색 입력
조건 필터 요약
조건 만족 단지 수
지도
검색 결과 리스트
하단 비교 바
```

검색 입력은 현재 `#q`를 유지한다. 지도와 리스트는 같은 필터 결과를 공유한다.

### 4.2 지도 영역

초기 상태:

- 서울 중심 좌표
- 적정 줌 레벨
- 단지 cluster 표시
- 검색어가 없을 때는 전체 단지를 가볍게 표시

검색어 입력 후:

- 검색 결과만 강조
- 지도 bounds를 결과 범위에 맞춤
- 결과가 너무 많으면 bounds 이동은 하지 않고 상태 문구만 갱신

단지 클릭:

- 단지명, 구/동, 준공연도, 세대수
- 매칭 신뢰도
- `비교에 담기` 버튼
- `FAILED`이면 일조/조망 분석 불가 사유 표시

### 4.3 비교서

기존 비교서 구조를 유지한다.

표시 순서:

1. 단지명, 구/동, 준공연도, 세대수
2. 규제/재건축 플래그
3. 가격
4. 5년 추이
5. 유사 거래 사례
6. 환경 지표
7. 현장 확인 항목

지도 클릭은 비교서로 바로 이동시키지 않고, 먼저 후보 요약을 보여준다.

## 5. 데이터 구조

### 5.1 현재 사용 가능

`front/public/data/index.json`

```json
{
  "complexes": [
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
  ]
}
```

이 데이터만으로 지도 마커와 검색 결과는 구현할 수 있다.

### 5.2 지도용 추가 후보

지도 성능과 상태 표현을 위해 인덱스에 다음 필드를 추가할 수 있다.

```json
{
  "m": "HIGH",
  "pm": true
}
```

- `m`: `match_confidence`
- `pm`: `polygon_matched`

이 필드가 있어야 상세 JSON을 열지 않고도 `FAILED` 마커를 회색으로 칠할 수 있다.

## 6. 모듈 구성안

현재는 단일 `app.js` 구조다. 1차 구현은 파일 분리를 최소화한다.

```text
front/public/
  index.html
  styles.css
  app.js
```

`app.js` 내부 섹션:

```text
config
utils
basket
search
map
compare
init
```

지도 코드가 길어지면 다음 단계에서 분리한다.

```text
front/public/map.js
front/public/app.js
```

## 7. 지도 구현 의사코드

```js
function initMap() {
  if (!window.ol || !config.vworldKey) {
    renderMapFallback();
    return;
  }

  map = new ol.Map({
    target: 'map',
    layers: [createVworldBaseLayer()],
    view: new ol.View({
      center: ol.proj.fromLonLat([126.978, 37.5665]),
      zoom: 11
    })
  });

  markerSource = new ol.source.Vector();
  clusterSource = new ol.source.Cluster({
    distance: 36,
    source: markerSource
  });

  map.addLayer(createClusterLayer(clusterSource));
  bindMapClick();
}

function syncMapResults(items) {
  markerSource.clear();
  markerSource.addFeatures(items.map(toComplexFeature));
}
```

## 8. 성능 기준

- 초기 로딩은 `index.json` 1회만 허용한다.
- 단지 상세 JSON은 클릭/비교 시점까지 미룬다.
- 9,000개 이상 point는 cluster source로 표시한다.
- 검색어 입력마다 feature 전체를 다시 만들지 않도록 debounce 또는 diff 갱신을 검토한다.
- 모바일에서 지도 높이는 화면을 다 먹지 않게 제한한다.

권장 시작값:

```css
.map {
  height: min(56vh, 520px);
  min-height: 320px;
}
```

## 9. 접근성 및 모바일

- 지도만으로 단지를 선택하게 만들지 않는다.
- 지도 아래 리스트 결과를 항상 제공한다.
- 마커 클릭 결과는 키보드 조작 가능한 패널에도 노출한다.
- 색상만으로 정상/FAILED 상태를 전달하지 않는다.
- 400px 폭에서 가로 스크롤이 생기지 않게 한다.
- 검색 input은 16px 이상을 유지한다.

## 10. 단계별 작업 순서

1. `index.html`에 지도 컨테이너와 설정 스크립트 위치 추가
2. OpenLayers CSS/JS 로드
3. VWorld base tile layer 생성
4. `index.json` 좌표를 vector feature로 변환
5. cluster layer 스타일 작성
6. 지도 클릭 시 단지 요약 패널 표시
7. 검색 결과와 지도 feature 동기화
8. `FAILED` 상태 표시를 위해 payload 인덱스 확장 검토
9. 모바일 레이아웃 검증
10. 문구 검사 실행

## 11. 검증 항목

- 키가 없을 때 검색과 비교가 동작한다.
- VWorld 키가 있을 때 서울 지도와 단지 마커가 보인다.
- 검색어 입력 시 리스트와 지도 결과가 일치한다.
- 단지 클릭 시 정확한 단지명이 뜬다.
- `FAILED` 단지가 회색 또는 별도 스타일로 보인다.
- 상세 JSON은 클릭 전에는 로드하지 않는다.
- 400px 폭에서 레이아웃이 깨지지 않는다.
- 금지 문구가 추가되지 않는다.

## 12. 열어둘 결정

- VWorld 키 주입 방식: 전역 config 파일 vs HTML inline config
- 마커 색상 기준: 매칭 신뢰도 우선 vs 검색/필터 점수 우선
- 지도와 리스트의 화면 비율
- 행정구역 polygon overlay를 v1에 포함할지 여부
- `index.json`에 `match_confidence`를 추가할지 여부
