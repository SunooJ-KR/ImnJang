// ============================================================================
// front/build/36.build_payload.py 산출 스키마.
// 필드를 늘리거나 지우면 37.check_wording.py 의 SCHEMA 도 같은 PR에서 고친다.
// ============================================================================

export type MatchConfidence = "HIGH" | "MEDIUM" | "LOW" | "FAILED";

/** index.json 은 용량 때문에 키를 1글자로 줄였다. */
export interface IndexComplex {
  id: string;
  /** 단지명 */
  n: string;
  /** 구 */
  g: string;
  /** 법정동 */
  u: string;
  lat: number | null;
  lng: number | null;
  /** 준공연도 */
  y: number | null;
  /** 세대수 */
  h: number | null;
  match_confidence?: MatchConfidence;
}

export interface IndexPayload {
  as_of: string;
  complexes: IndexComplex[];
}

export type PriceSource = "CELL_LAST" | "AREA_LAST" | "COMPLEX_MEAN" | "MODEL" | "EXCLUDED";
export type FloorBand = "HIGH" | "MID" | "LOW" | "UNKNOWN";
export type EstConfidence = "HIGH" | "MEDIUM" | "LOW";

export interface PriceEntry {
  area_type: number | null;
  floor_band: FloorBand | null;
  source: PriceSource;
  n_trades_24m: number | null;
  last_deal_ym: string | null;
  last_price_manwon: number | null;
  mean_price_per_m2_24m: number | null;
  area_last_floor_band: FloorBand | null;
  est_price_per_m2: number | null;
  est_low: number | null;
  est_high: number | null;
  est_confidence: EstConfidence | null;
  reason: string | null;
}

export interface SeriesEntry {
  area_type: number;
  /** [YYYY-MM, 만원/㎡] */
  points: [string, number][];
}

export interface Comparable {
  area_type: number;
  target_source: PriceSource;
  rank: number;
  comp_id: string;
  name: string;
  deal_ym: string;
  price_manwon: number;
  price_per_m2: number;
  adj_price_per_m2: number;
  adj_reason: string;
  dist_m: number;
}

export interface EnvMetrics {
  sun_hours_avg: number | null;
  sun_hours_best: number | null;
  view_open_avg: number | null;
  river_view_ratio: number | null;
  road_centerline_m: number | null;
  road_arterial_dist_m: number | null;
  road_secondary_dist_m: number | null;
  rail_centerline_m: number | null;
  station_dist_m: number | null;
  station_walk_min_est: number | null;
  station_ridership_daily: number | null;
  station_congestion_peak: number | null;
  elem_school_m: number | null;
  elem_safe_route: boolean | null;
  mid_school_m: number | null;
  high_school_m: number | null;
  tertiary_hosp_m: number | null;
  general_hosp_m: number | null;
  clinic_1km: number | null;
  pediatric_1km: number | null;
  mart_m: number | null;
  dept_store_m: number | null;
  supermarket_m: number | null;
  cvs_500m: number | null;
  restaurant_500m: number | null;
  park_m: number | null;
  park_area_m2: number | null;
  nightlife_300m: number | null;
}

export interface Regulation {
  land_permit_zone: boolean;
  as_of: string;
  note: string;
}

/** /regulation.json (front/public, 주간 워크플로가 갱신). 단지 payload의 regulation보다 우선하는 최신 지정 상태다. */
export interface RegulationSummary {
  as_of: string;
  seoul_apartment_permit_zone: boolean;
}

export interface Redevelop {
  type: string | null;
  stage: string | null;
}

export interface ComplexPayload {
  id: string;
  name: string;
  gu: string;
  umd_name: string;
  lat: number | null;
  lng: number | null;
  built_year: number | null;
  households: number | null;
  match_confidence: MatchConfidence;
  env: EnvMetrics;
  floors: number | null;
  price: PriceEntry[];
  series: SeriesEntry[];
  comparables: Comparable[];
  regulation: Regulation | null;
  redevelop: Redevelop | null;
  unknowns: string[];
}

export interface BasketItem {
  id: string;
  name: string;
}

export type QuickFilter = "all" | "ready" | "review";
