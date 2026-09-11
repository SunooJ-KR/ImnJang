// ============================================================================
// app.js — 임앤장 프론트엔드
// ============================================================================
// Author:      yjkim
// Purpose:     검색(S1) → 담기(S2) → 비교서(S3) 동선을 정적 JSON만으로 구현한다
// Description: 런타임 서버가 없다. front/public/data/ 의 정적 JSON이 곧 API다.
//              표기 규칙은 docs/product-identity.md §6을 그대로 따른다.
//              특히 price[].source가 화면 문구를 가르는 축이며 절대 섞지 않는다.
//              일조·조망은 가격과 연결해 말하지 않는다 — 측정 결과 예측력이
//              없었다(§5.3). 환경은 환경대로만 보여준다.
// ============================================================================

'use strict';

var MAX_BASKET = 4;
var STORAGE_KEY = 'imnjang.basket.v1';
var MAX_RESULTS = 30;

var indexData = null;
var basket = loadBasket();

// ---------------------------------------------------------------------------
// 유틸
// ---------------------------------------------------------------------------

/** 만원 단위를 억 표기로. 13500 -> "1.35억" */
function toEok(manwon) {
  if (manwon === null || manwon === undefined) return null;
  return (manwon / 10000).toFixed(2).replace(/\.?0+$/, '') + '억';
}

/** "2026-07" -> "2026년 7월" */
function ymLabel(ym) {
  if (!ym) return null;
  var parts = String(ym).split('-');
  if (parts.length !== 2) return String(ym);
  return parts[0] + '년 ' + Number(parts[1]) + '월';
}

/** null이면 "정보 없음". 0은 값이므로 그대로 표기한다 */
function orUnknown(value, suffix) {
  if (value === null || value === undefined) return '정보 없음';
  return value + (suffix || '');
}

function el(tag, className, text) {
  var node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

// ---------------------------------------------------------------------------
// S2 담은 목록 (localStorage)
// ---------------------------------------------------------------------------

function loadBasket() {
  try {
    var raw = window.localStorage.getItem(STORAGE_KEY);
    var parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.slice(0, MAX_BASKET) : [];
  } catch (error) {
    return [];   // 사생활 보호 모드 등에서 localStorage가 막힐 수 있다
  }
}

function saveBasket() {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(basket));
  } catch (error) {
    /* 저장 실패해도 이번 세션 비교는 동작해야 한다 */
  }
}

function inBasket(id) {
  return basket.some(function (item) { return item.id === id; });
}

function toggleBasket(entry) {
  if (inBasket(entry.id)) {
    basket = basket.filter(function (item) { return item.id !== entry.id; });
  } else {
    if (basket.length >= MAX_BASKET) {
      window.alert('최대 ' + MAX_BASKET + '곳까지 담을 수 있습니다.');
      return;
    }
    basket.push(entry);
  }
  saveBasket();
  renderBasket();
  renderResults(document.getElementById('q').value);
}

function renderBasket() {
  var bar = document.getElementById('basket');
  var label = document.getElementById('basket-label');
  if (basket.length === 0) {
    bar.hidden = true;
    return;
  }
  bar.hidden = false;
  label.textContent = '담은 단지 ' + basket.length + '곳: ' +
    basket.map(function (item) { return item.name; }).join(', ');
}

// ---------------------------------------------------------------------------
// S1 검색
// ---------------------------------------------------------------------------

function renderResults(query) {
  var list = document.getElementById('results');
  var status = document.getElementById('search-status');
  list.textContent = '';
  var term = (query || '').trim();

  if (!indexData) return;
  if (term.length === 0) {
    status.textContent = '단지 ' + indexData.complexes.length.toLocaleString() +
      '곳 중에서 찾습니다. 단지명이나 동 이름을 입력하세요.';
    return;
  }

  var matched = indexData.complexes.filter(function (item) {
    return item.n.indexOf(term) !== -1 || item.u.indexOf(term) !== -1;
  });

  if (matched.length === 0) {
    status.textContent = '"' + term + '"에 해당하는 단지를 찾지 못했습니다.';
    return;
  }
  status.textContent = matched.length.toLocaleString() + '곳 중 ' +
    Math.min(matched.length, MAX_RESULTS) + '곳 표시';

  matched.slice(0, MAX_RESULTS).forEach(function (item) {
    var row = el('li', 'result');
    var info = el('div', 'result-info');
    info.appendChild(el('strong', null, item.n));
    info.appendChild(el('span', 'muted',
      item.g + ' ' + item.u + ' · ' + item.y + '년 · ' + item.h.toLocaleString() + '세대'));
    row.appendChild(info);

    var button = el('button', inBasket(item.id) ? 'picked' : null,
      inBasket(item.id) ? '담김' : '비교에 담기');
    button.type = 'button';
    button.addEventListener('click', function () {
      toggleBasket({ id: item.id, name: item.n });
    });
    row.appendChild(button);
    list.appendChild(row);
  });
}

// ---------------------------------------------------------------------------
// S3 비교서 — ① 가격
// ---------------------------------------------------------------------------

/** price[].source가 문구를 가른다. 실거래와 추정을 절대 섞지 않는다 */
function priceLine(entry) {
  var head = entry.area_type + '㎡ · ' + bandLabel(entry.floor_band);
  var wrap = el('div', 'price-row');
  wrap.appendChild(el('div', 'price-head', head));

  if (entry.source === 'CELL_LAST') {
    wrap.appendChild(el('div', 'price-value',
      ymLabel(entry.last_deal_ym) + ' 실거래 ' + toEok(entry.last_price_manwon)));
    wrap.appendChild(el('div', 'muted', '최근 2년 거래 ' + entry.n_trades_24m + '건'));
  } else if (entry.source === 'COMPLEX_MEAN') {
    wrap.appendChild(el('div', 'price-value',
      '이 면적 거래 없음 · 단지 평균 ' + entry.mean_price_per_m2_24m + '만원/㎡'));
  } else if (entry.source === 'MODEL') {
    var range = '추정 ' + entry.est_low + '~' + entry.est_high + '만원/㎡';
    wrap.appendChild(el('div', 'price-value estimated', range));
    wrap.appendChild(el('div', 'muted', '이 단지는 최근 2년 거래가 없습니다'));
    var badge = el('span', 'badge badge-' + String(entry.est_confidence).toLowerCase(),
      '신뢰도 ' + entry.est_confidence);
    wrap.appendChild(badge);
  } else if (entry.source === 'EXCLUDED') {
    wrap.appendChild(el('div', 'price-value muted',
      '임대 전용 단지로 매매 거래가 없습니다'));
  }
  return wrap;
}

function bandLabel(band) {
  if (band === 'HIGH') return '고층';
  if (band === 'MID') return '중층';
  if (band === 'LOW') return '저층';
  return '층 정보 없음';
}

/** 추세선만 그린다. 축·범례 없음. 2점 미만이면 그리지 않는다 */
function sparkline(points) {
  if (!points || points.length < 2) return null;
  var width = 160, height = 36, pad = 2;
  var values = points.map(function (p) { return p[1]; });
  var min = Math.min.apply(null, values);
  var max = Math.max.apply(null, values);
  var span = (max - min) || 1;
  var step = (width - pad * 2) / (points.length - 1);

  var coords = points.map(function (p, i) {
    var x = pad + i * step;
    var y = height - pad - ((p[1] - min) / span) * (height - pad * 2);
    return x.toFixed(1) + ',' + y.toFixed(1);
  }).join(' ');

  var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
  svg.setAttribute('class', 'spark');
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label',
    points[0][0] + '부터 ' + points[points.length - 1][0] + '까지 가격 추세');
  var line = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
  line.setAttribute('points', coords);
  svg.appendChild(line);
  return svg;
}

// ---------------------------------------------------------------------------
// S3 비교서 — ② 환경
// ---------------------------------------------------------------------------

var ENV_ROWS = [
  ['일조 (동지 08~16시)', function (d) { return fmt(d.env.sun_hours_avg, '시간'); }],
  ['조망 개방도', function (d) { return fmt(d.env.view_open_avg, '도'); }],
  ['한강 조망 세대 비율', function (d) { return ratio(d.env.river_view_ratio); }],
  ['지하철 출입구까지', function (d) { return fmt(d.env.station_dist_m, 'm'); }],
  ['추정 도보', function (d) { return fmt(d.env.station_walk_min_est, '분'); }],
  ['초등학교까지', function (d) { return fmt(d.env.elem_school_m, 'm'); }],
  ['초품아 (직선 기준 근사)', function (d) { return bool(d.env.elem_safe_route); }],
  ['종합병원까지', function (d) { return fmt(d.env.general_hosp_m, 'm'); }],
  ['공원까지', function (d) { return fmt(d.env.park_m, 'm'); }],
  ['편의점 (500m)', function (d) { return fmt(d.env.cvs_500m, '개'); }],
  ['음식점 (500m)', function (d) { return fmt(d.env.restaurant_500m, '개'); }],
  ['간선도로까지', function (d) { return fmt(d.env.road_arterial_dist_m, 'm'); }]
];

function fmt(value, suffix) {
  if (value === null || value === undefined) return '정보 없음';
  return Number(value).toLocaleString() + suffix;
}
function ratio(value) {
  if (value === null || value === undefined) return '정보 없음';
  return Math.round(value * 100) + '%';
}
function bool(value) {
  if (value === null || value === undefined) return '정보 없음';
  return value ? '예' : '아니오';
}

// ---------------------------------------------------------------------------
// S3 비교서 — 조립
// ---------------------------------------------------------------------------

function renderComplexCard(data) {
  var card = el('article', 'card');
  card.appendChild(el('h3', null, data.name));
  card.appendChild(el('p', 'muted',
    data.gu + ' ' + data.umd_name + ' · ' + data.built_year + '년 · ' +
    (data.households === null ? '세대수 정보 없음' : data.households.toLocaleString() + '세대')));

  if (data.regulation && data.regulation.land_permit_zone) {
    card.appendChild(el('p', 'flag',
      '토지거래허가구역 (' + data.regulation.as_of + ' 기준) · ' + data.regulation.note));
  }
  if (data.redevelop && data.redevelop.type) {
    card.appendChild(el('p', 'flag',
      '재건축 추진 중 (' + data.redevelop.stage + ' 단계)'));
  }

  // ① 가격
  card.appendChild(el('h4', null, '가격'));
  if (!data.price || data.price.length === 0) {
    card.appendChild(el('p', 'muted', '가격 정보가 없습니다.'));
  } else {
    data.price.forEach(function (entry) { card.appendChild(priceLine(entry)); });
  }

  if (data.series && data.series.length > 0) {
    data.series.forEach(function (s) {
      var chart = sparkline(s.points);
      if (!chart) return;
      var box = el('div', 'series');
      box.appendChild(el('span', 'muted', s.area_type + '㎡ 5년 추이'));
      box.appendChild(chart);
      card.appendChild(box);
    });
  }

  // 비교사례
  card.appendChild(el('h4', null, '비슷한 조건의 다른 단지'));
  if (!data.comparables || data.comparables.length === 0) {
    card.appendChild(el('p', 'muted', '비교할 만한 유사 단지를 찾지 못했습니다.'));
  } else {
    var list = el('ul', 'comps');
    data.comparables.slice(0, 5).forEach(function (c) {
      var item = el('li');
      item.appendChild(el('strong', null, c.name));
      item.appendChild(el('span', 'muted',
        ymLabel(c.deal_ym) + ' ' + toEok(c.price_manwon) +
        ' · ' + Math.round(c.dist_m).toLocaleString() + 'm · ' + c.adj_reason));
      list.appendChild(item);
    });
    card.appendChild(list);
    card.appendChild(el('p', 'muted',
      '보정가격은 실제로 체결된 가격이 아닙니다.'));
  }

  // ② 환경
  card.appendChild(el('h4', null, '환경'));
  if (data.match_confidence === 'FAILED') {
    card.appendChild(el('p', 'flag', '건물 매칭 실패로 일조·조망 분석 불가'));
  }
  var table = el('table', 'env');
  ENV_ROWS.forEach(function (row) {
    var tr = el('tr');
    tr.appendChild(el('th', null, row[0]));
    tr.appendChild(el('td', null, row[1](data)));
    table.appendChild(tr);
  });
  card.appendChild(table);

  // ③ 현장 확인
  card.appendChild(el('h4', null, '현장에서 확인할 것'));
  var ul = el('ul', 'unknowns');
  (data.unknowns || []).forEach(function (u) { ul.appendChild(el('li', null, u)); });
  card.appendChild(ul);

  return card;
}

function renderCompare() {
  var section = document.getElementById('compare-section');
  var host = document.getElementById('compare');
  host.textContent = '';
  section.hidden = false;
  host.appendChild(el('p', 'status', '불러오는 중입니다…'));

  Promise.all(basket.map(function (item) {
    return fetch('data/complex/' + item.id + '.json').then(function (res) {
      if (!res.ok) throw new Error(item.id);
      return res.json();
    });
  })).then(function (all) {
    host.textContent = '';
    all.forEach(function (data) { host.appendChild(renderComplexCard(data)); });
    section.scrollIntoView({ behavior: 'smooth' });
  }).catch(function () {
    host.textContent = '';
    host.appendChild(el('p', 'status', '단지 정보를 불러오지 못했습니다.'));
  });
}

// ---------------------------------------------------------------------------
// 시작
// ---------------------------------------------------------------------------

function init() {
  document.getElementById('q').addEventListener('input', function (event) {
    renderResults(event.target.value);
  });
  document.getElementById('btn-compare').addEventListener('click', renderCompare);
  document.getElementById('btn-clear').addEventListener('click', function () {
    basket = [];
    saveBasket();
    renderBasket();
    document.getElementById('compare-section').hidden = true;
    renderResults(document.getElementById('q').value);
  });

  renderBasket();

  fetch('data/index.json').then(function (res) {
    if (!res.ok) throw new Error('index');
    return res.json();
  }).then(function (data) {
    indexData = data;
    renderResults('');
  }).catch(function () {
    document.getElementById('search-status').textContent =
      '검색 인덱스를 불러오지 못했습니다.';
  });
}

document.addEventListener('DOMContentLoaded', init);
