"use client";

import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { SectionHeading } from "@/components/ui/section-heading";
import { bandLabel, num, ratio, toEok, ymLabel } from "@/lib/format";
import type { ComplexPayload, EnvMetrics, FloorBand, PriceEntry, RegulationSummary, SeriesEntry } from "@/lib/types";
import { cn } from "@/lib/utils";

const ALL_FLOORS = "ALL";
type FloorChoice = FloorBand | typeof ALL_FLOORS;

const ENV_ROWS: { label: string; read: (env: EnvMetrics) => string }[] = [
  { label: "일조 (동지 08~16시)", read: (env) => num(env.sun_hours_avg, "시간") },
  { label: "조망 개방도", read: (env) => num(env.view_open_avg, "도") },
  { label: "한강 조망", read: (env) => `관측점 기준 한강 조망 비율 ${ratio(env.river_view_ratio)}` },
  { label: "지하철 출입구까지", read: (env) => num(env.station_dist_m, "m") },
  { label: "역까지", read: (env) => `추정 도보 ${num(env.station_walk_min_est, "분")}` },
  { label: "초등학교까지", read: (env) => num(env.elem_school_m, "m") },
  {
    label: "초등학교 직선 경로",
    read: (env) => env.elem_safe_route === null ? "정보 없음" : env.elem_safe_route
      ? "초등학교까지 직선 경로가 간선도로와 교차하지 않음(근사)"
      : "초등학교까지 직선 경로가 간선도로와 교차함(근사)",
  },
  { label: "종합병원까지", read: (env) => num(env.general_hosp_m, "m") },
  { label: "공원까지", read: (env) => num(env.park_m, "m") },
  { label: "편의점 (500m)", read: (env) => num(env.cvs_500m, "개") },
  { label: "음식점 (500m)", read: (env) => num(env.restaurant_500m, "개") },
  { label: "간선도로 중심선까지", read: (env) => num(env.road_arterial_dist_m, "m") },
];

export function CompareSection({ payloads, loading, error, regulation, regulationReady }: {
  payloads: ComplexPayload[];
  loading: boolean;
  error: string | null;
  regulation: RegulationSummary | null;
  regulationReady: boolean;
}) {
  return (
    <Card id="compare-section" className="scroll-mt-16 p-3 md:p-4">
      <SectionHeading eyebrow="Compare" title="비교서" />
      {loading && <p className="mt-3 text-[12.5px] text-muted-foreground">불러오는 중입니다.</p>}
      {error !== null && <p className="mt-3 text-[12.5px] text-muted-foreground">{error}</p>}
      {!loading && error === null && <>
        <div className="mt-3 grid gap-3 md:grid-cols-[repeat(auto-fit,minmax(280px,1fr))]">
          {payloads.map((data, index) => <ComplexCard key={data.id} data={data} index={index} regulation={regulation} regulationReady={regulationReady} />)}
        </div>
        <EnvironmentComparison payloads={payloads} />
      </>}
    </Card>
  );
}

function ComplexCard({ data, index, regulation, regulationReady }: {
  data: ComplexPayload;
  index: number;
  regulation: RegulationSummary | null;
  regulationReady: boolean;
}) {
  const pricedAreas = useMemo(
    () => [...new Set(data.price.map((entry) => entry.area_type).filter((area): area is number => area !== null))].sort((a, b) => a - b),
    [data.price],
  );
  const defaultArea = useMemo(() => mostTradedArea(data.price, pricedAreas), [data.price, pricedAreas]);
  const [area, setArea] = useState<number | null>(defaultArea);
  const [floor, setFloor] = useState<FloorChoice>(() => defaultFloor(data.price, defaultArea));
  const availableFloors = useMemo(() => floorsForArea(data.price, area), [data.price, area]);
  const hasUnbanded = useMemo(() => hasUnbandedEntry(data.price, area), [data.price, area]);
  const selectedPrice = useMemo(() => priceForSelection(data.price, area, floor), [data.price, area, floor]);
  const selectedSeries = useMemo(() => area === null ? undefined : data.series.find((series) => series.area_type === area), [area, data.series]);
  const selectedComparables = useMemo(() => area === null ? [] : data.comparables.filter((comparable) => comparable.area_type === area), [area, data.comparables]);
  const displayedRegulation = regulationReady
    ? regulation === null
      ? data.regulation?.land_permit_zone ? { as_of: data.regulation.as_of } : null
      : regulation.seoul_apartment_permit_zone ? { as_of: regulation.as_of } : null
    : data.regulation?.land_permit_zone ? { as_of: data.regulation.as_of } : null;

  return (
    <article className="min-w-0 rounded-lg bg-muted p-3 animate-rise-in" style={{ animationDelay: `${index * 40}ms` }}>
      <h3 className="text-lg font-bold break-words">{data.name}</h3>
      <p className="text-[12.5px] text-muted-foreground">
        {data.gu} {data.umd_name} · {data.built_year === null ? "준공연도 정보 없음" : `${data.built_year}년`} · {num(data.households, "세대")}
      </p>
      {displayedRegulation && <Flag>토지거래허가구역 ({displayedRegulation.as_of} 기준) · 실거주 목적만 매수 가능, 2년 실거주 의무</Flag>}
      {data.redevelop?.type && <Flag>{redevelopLabel(data.redevelop.type, data.redevelop.stage)}</Flag>}

      <SubHeading>가격 비교 기준</SubHeading>
      {area === null ? <Empty>가격 정보가 없습니다.</Empty> : (
        <div className="grid grid-cols-2 gap-2">
          <label className="text-[12.5px] text-muted-foreground">면적타입
            <select className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1 text-foreground" value={area} onChange={(event) => {
              const nextArea = Number(event.target.value);
              setArea(nextArea);
              setFloor(defaultFloor(data.price, nextArea));
            }}>
              {pricedAreas.map((value) => <option key={value} value={value}>{value}㎡</option>)}
            </select>
          </label>
          <label className="text-[12.5px] text-muted-foreground">층대
            <select className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1 text-foreground" value={floor} onChange={(event) => setFloor(event.target.value as FloorChoice)}>
              {hasUnbanded && <option value={ALL_FLOORS}>층 정보 없음</option>}
              {availableFloors.map((value) => <option key={value} value={value}>{bandLabel(value)}</option>)}
            </select>
          </label>
        </div>
      )}

      <SubHeading>가격 요약</SubHeading>
      {selectedPrice ? <PriceSummary entry={selectedPrice} /> : <Empty>선택한 면적·층대의 가격 정보가 없습니다.</Empty>}

      <details className="mt-4 border-t border-border pt-2">
        <summary className="cursor-pointer text-[12.5px] text-muted-foreground">상세 거래 추이·비교사례</summary>
        {selectedSeries ? <Sparkline series={selectedSeries} /> : <Empty>선택한 면적의 거래 추이가 없습니다.</Empty>}
        <SubHeading>비슷한 조건의 다른 단지</SubHeading>
        {selectedComparables.length === 0 ? <Empty>비교할 만한 유사 단지를 찾지 못했습니다.</Empty> : <>
          <ul>{selectedComparables.slice(0, 5).map((comparable) => (
            <li key={`${comparable.comp_id}-${comparable.rank}`} className="flex flex-col border-b border-border py-1.5">
              <strong className="text-sm font-bold break-words">{comparable.name}</strong>
              <span className="text-[12.5px] text-muted-foreground">
                {ymLabel(comparable.deal_ym)} {toEok(comparable.price_manwon)} · {num(Math.round(comparable.dist_m), "m")} · {" "}{comparable.adj_reason}
              </span>
            </li>
          ))}</ul>
          <Empty>보정가격은 실제로 체결된 가격이 아닙니다.</Empty>
        </>}
      </details>

      <SubHeading>현장에서 확인할 것</SubHeading>
      {data.unknowns.length === 0 ? <Empty>정보 없음</Empty> : (
        <ul className="list-disc pl-4 text-[12.5px] text-muted-foreground">
          {data.unknowns.map((text) => <li key={text}>{text}</li>)}
        </ul>
      )}
    </article>
  );
}

function EnvironmentComparison({ payloads }: { payloads: ComplexPayload[] }) {
  const failed = payloads.filter((data) => data.match_confidence === "FAILED");
  return (
    <section className="mt-5 border-t border-border pt-4" aria-labelledby="environment-comparison-title">
      <h3 id="environment-comparison-title" className="text-base font-bold">환경 비교</h3>
      {failed.length > 0 && <Flag>{failed.map((data) => data.name).join(", ")}: 건물 매칭 실패로 일조·조망 분석 불가</Flag>}
      <div className="mt-2 overflow-x-auto">
        <table className="min-w-full border-collapse text-[12.5px]">
          <thead><tr className="border-b border-border">
            <th scope="col" className="sticky left-0 bg-card px-2 py-2 text-left font-normal text-muted-foreground">지표</th>
            {payloads.map((data) => <th key={data.id} scope="col" className="min-w-44 px-2 py-2 text-right align-top font-bold">
              <span className="block break-words">{data.name}</span>
              {data.match_confidence === "LOW" && <span className="mt-1 block text-[11px] font-normal text-neutral-strong">동 배정 신뢰도 낮음</span>}
            </th>)}
          </tr></thead>
          <tbody>{ENV_ROWS.map((row) => <tr key={row.label} className="border-b border-border/70">
            <th scope="row" className="sticky left-0 min-w-28 bg-card px-2 py-2 text-left font-normal text-muted-foreground">{row.label}</th>
            {payloads.map((data) => <td key={data.id} className="px-2 py-2 text-right align-top">{row.read(data.env)}</td>)}
          </tr>)}</tbody>
        </table>
      </div>
    </section>
  );
}

function mostTradedArea(entries: PriceEntry[], areas: number[]): number | null {
  return areas.reduce<number | null>((best, area) => {
    if (best === null) return area;
    const total = (target: number) => entries.filter((entry) => entry.area_type === target).reduce((sum, entry) => sum + (entry.n_trades_24m ?? 0), 0);
    return total(area) > total(best) ? area : best;
  }, null);
}

const FLOOR_ORDER: FloorBand[] = ["LOW", "MID", "HIGH"];

function floorsForArea(entries: PriceEntry[], area: number | null): FloorBand[] {
  if (area === null) return [];
  const bands = new Set(entries
    .filter((entry) => entry.area_type === area && entry.floor_band !== null && entry.floor_band !== "UNKNOWN")
    .map((entry) => entry.floor_band));
  return FLOOR_ORDER.filter((band) => bands.has(band));
}
function hasUnbandedEntry(entries: PriceEntry[], area: number | null): boolean {
  return area !== null && entries.some((entry) => entry.area_type === area && (entry.floor_band === null || entry.floor_band === "UNKNOWN"));
}
/**
 * 가격은 층대별로만 들어 있고 층대를 합친 "전체" 항목은 없다.
 * 기본값을 "전체"로 두면 층 정보가 있는 단지는 담자마자 가격이 비어 보인다.
 * 그 면적에서 최근 2년 거래가 가장 많은 층대를 고르고, 층 정보가 없는 단지만 ALL 로 둔다.
 */
function defaultFloor(entries: PriceEntry[], area: number | null): FloorChoice {
  const bands = floorsForArea(entries, area);
  if (bands.length === 0) return ALL_FLOORS;
  const trades = (band: FloorBand) => entries.find((entry) => entry.area_type === area && entry.floor_band === band)?.n_trades_24m ?? 0;
  return bands.reduce((best, band) => (trades(band) > trades(best) ? band : best), bands[0]);
}

function priceForSelection(entries: PriceEntry[], area: number | null, floor: FloorChoice): PriceEntry | undefined {
  if (area === null) return entries.find((entry) => entry.source === "EXCLUDED");
  return entries.find((entry) => entry.area_type === area && (floor === ALL_FLOORS ? entry.floor_band === "UNKNOWN" || entry.floor_band === null : entry.floor_band === floor));
}

/** 가격 문구는 source별로 분기하고, 총액과 원단가의 단위를 섞지 않는다. */
function PriceSummary({ entry }: { entry: PriceEntry }) {
  const totalFromUnit = (value: number | null) => value === null || entry.area_type === null ? "정보 없음" : toEok(value * entry.area_type);
  if (entry.source === "CELL_LAST") return <>
    <Value>{ymLabel(entry.last_deal_ym)} 실거래 {toEok(entry.last_price_manwon)}</Value>
    <div className="text-[12.5px] text-muted-foreground">최근 2년 매매 거래 {num(entry.n_trades_24m, "건")}</div>
  </>;
  if (entry.source === "AREA_LAST") return <>
    <Value>이 층대 최근 2년 매매 거래 없음 · 같은 면적 {bandLabel(entry.area_last_floor_band)} {ymLabel(entry.last_deal_ym)} 실거래 {toEok(entry.last_price_manwon)}</Value>
  </>;
  if (entry.source === "COMPLEX_MEAN") return <>
    <Value>이 면적·층대의 최근 2년 매매 거래 없음 · 단지 평균 {totalFromUnit(entry.mean_price_per_m2_24m)}</Value>
    <div className="text-[12.5px] text-muted-foreground">원단가 {num(entry.mean_price_per_m2_24m, "만원/㎡")}</div>
  </>;
  if (entry.source === "MODEL") return <>
    <Value accent>추정 {totalFromUnit(entry.est_low)}~{totalFromUnit(entry.est_high)} · 최근 2년 매매 거래가 없어 추정했습니다</Value>
    <div className="text-[12.5px] text-muted-foreground">원단가 {num(entry.est_low, "만원/㎡")}~{num(entry.est_high, "만원/㎡")}</div>
    <Badge className="mt-1" variant={entry.est_confidence === "HIGH" ? "accent" : "neutral"}>신뢰도 {entry.est_confidence ?? "정보 없음"}</Badge>
  </>;
  return <div className="text-[12.5px] text-muted-foreground">임대 관련 명칭으로 추정 대상에서 제외했습니다</div>;
}

function redevelopLabel(type: string, stage: string | null): string {
  const label = type.includes("재건축") ? "재건축 추진 중" : type.includes("재개발") ? "재개발 구역 포함" : "정비사업 구역";
  return `${label} (${stage ?? "정보 없음"} 단계)`;
}

function Sparkline({ series }: { series: SeriesEntry }) {
  const points = series.points;
  if (points.length < 2) return null;
  const width = 140;
  const height = 34;
  const pad = 2;
  const values = points.map((point) => point[1]);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const step = (width - pad * 2) / (points.length - 1);
  const coords = points.map((point, pointIndex) => {
    const x = pad + pointIndex * step;
    const y = height - pad - ((point[1] - min) / span) * (height - pad * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  return <div className="my-2 flex items-center gap-2">
    <span className="text-[12.5px] text-muted-foreground">{series.area_type}㎡ 5년 추이</span>
    <svg viewBox={`0 0 ${width} ${height}`} className="h-[34px] w-[140px] shrink-0" role="img" aria-label={`${points[0][0]}부터 ${points[points.length - 1][0]}까지 가격 추세`}>
      <polyline points={coords} fill="none" stroke="var(--primary)" strokeWidth="1.7" />
    </svg>
  </div>;
}

function SubHeading({ children }: { children: React.ReactNode }) {
  return <h4 className="mt-4 mb-2 border-b border-border pb-1 text-[12.5px] tracking-wide text-muted-foreground">{children}</h4>;
}

function Flag({ children }: { children: React.ReactNode }) {
  return <p className="my-2 rounded-md bg-neutral-soft px-3 py-2 text-[12.5px] text-neutral-strong">{children}</p>;
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="py-1 text-[12.5px] text-muted-foreground">{children}</p>;
}

function Value({ children, accent }: { children: React.ReactNode; accent?: boolean }) {
  return <div className={cn("text-[15px] font-bold", accent && "text-primary-hover")}>{children}</div>;
}
