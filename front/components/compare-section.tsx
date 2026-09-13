"use client";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { SectionHeading } from "@/components/ui/section-heading";
import { bandLabel, num, ratio, toEok, yesNo, ymLabel } from "@/lib/format";
import type { ComplexPayload, EnvMetrics, PriceEntry, SeriesEntry } from "@/lib/types";
import { cn } from "@/lib/utils";

const ENV_ROWS: { label: string; read: (env: EnvMetrics) => string }[] = [
  { label: "일조 (동지 08~16시)", read: (env) => num(env.sun_hours_avg, "시간") },
  { label: "조망 개방도", read: (env) => num(env.view_open_avg, "도") },
  { label: "한강 조망 세대 비율", read: (env) => ratio(env.river_view_ratio) },
  { label: "지하철 출입구까지", read: (env) => num(env.station_dist_m, "m") },
  { label: "추정 도보", read: (env) => num(env.station_walk_min_est, "분") },
  { label: "초등학교까지", read: (env) => num(env.elem_school_m, "m") },
  { label: "초품아 (직선 기준 근사)", read: (env) => yesNo(env.elem_safe_route) },
  { label: "종합병원까지", read: (env) => num(env.general_hosp_m, "m") },
  { label: "공원까지", read: (env) => num(env.park_m, "m") },
  { label: "편의점 (500m)", read: (env) => num(env.cvs_500m, "개") },
  { label: "음식점 (500m)", read: (env) => num(env.restaurant_500m, "개") },
  { label: "간선도로 중심선까지", read: (env) => num(env.road_arterial_dist_m, "m") },
];

export function CompareSection({
  payloads,
  loading,
  error,
}: {
  payloads: ComplexPayload[];
  loading: boolean;
  error: string | null;
}) {
  return (
    <Card id="compare-section" className="col-span-full scroll-mt-20 p-3 md:p-4">
      <SectionHeading eyebrow="Compare" title="비교서" />

      {loading && <p className="mt-3 text-[12.5px] text-muted-foreground">불러오는 중입니다.</p>}
      {error !== null && <p className="mt-3 text-[12.5px] text-muted-foreground">{error}</p>}

      {!loading && error === null && (
        <div className="mt-3 grid gap-3 md:grid-cols-[repeat(auto-fit,minmax(280px,1fr))]">
          {payloads.map((data, index) => (
            <ComplexCard key={data.id} data={data} index={index} />
          ))}
        </div>
      )}
    </Card>
  );
}

function ComplexCard({ data, index }: { data: ComplexPayload; index: number }) {
  return (
    <article
      className="min-w-0 rounded-lg bg-muted p-3 animate-rise-in"
      style={{ animationDelay: `${index * 40}ms` }}
    >
      <h3 className="text-lg font-bold break-words">{data.name}</h3>
      <p className="text-[12.5px] text-muted-foreground">
        {data.gu} {data.umd_name} · {data.built_year}년 · {num(data.households, "세대")}
      </p>

      {data.regulation?.land_permit_zone && (
        <Flag>
          토지거래허가구역 ({data.regulation.as_of} 기준) · {data.regulation.note}
        </Flag>
      )}
      {data.redevelop?.type && <Flag>재건축 추진 중 ({data.redevelop.stage} 단계)</Flag>}

      <SubHeading>가격</SubHeading>
      {data.price.length === 0 ? (
        <Empty>가격 정보가 없습니다.</Empty>
      ) : (
        data.price.map((entry, entryIndex) => <PriceRow key={entryIndex} entry={entry} />)
      )}

      {data.series.map((s, seriesIndex) => (
        <Sparkline key={seriesIndex} series={s} />
      ))}

      <SubHeading>비슷한 조건의 다른 단지</SubHeading>
      {data.comparables.length === 0 ? (
        <Empty>비교할 만한 유사 단지를 찾지 못했습니다.</Empty>
      ) : (
        <>
          <ul>
            {data.comparables.slice(0, 5).map((c) => (
              <li key={c.comp_id} className="flex flex-col border-b border-border py-1.5">
                <strong className="text-sm font-bold break-words">{c.name}</strong>
                <span className="text-[12.5px] text-muted-foreground">
                  {ymLabel(c.deal_ym)} {toEok(c.price_manwon)} · {num(Math.round(c.dist_m), "m")} ·{" "}
                  {c.adj_reason}
                </span>
              </li>
            ))}
          </ul>
          <Empty>보정가격은 실제로 체결된 가격이 아닙니다.</Empty>
        </>
      )}

      <SubHeading>환경</SubHeading>
      {data.match_confidence === "FAILED" && <Flag>건물 매칭 실패로 일조·조망 분석 불가</Flag>}
      <table className="w-full border-collapse text-[12.5px]">
        <tbody>
          {ENV_ROWS.map((row) => (
            <tr key={row.label}>
              <th className="w-[55%] py-1 text-left font-normal text-muted-foreground">
                {row.label}
              </th>
              <td className="py-1 text-right">{row.read(data.env)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <SubHeading>현장에서 확인할 것</SubHeading>
      <ul className="list-disc pl-4 text-[12.5px] text-muted-foreground">
        {data.unknowns.map((text) => (
          <li key={text}>{text}</li>
        ))}
      </ul>
    </article>
  );
}

/** 가격 문구는 source별로 분기한다. 서로 다른 근거를 한 문장으로 섞지 않는다. */
function PriceRow({ entry }: { entry: PriceEntry }) {
  return (
    <div className="border-b border-border py-2">
      <div className="text-[12.5px] text-muted-foreground">
        {entry.area_type}㎡ · {bandLabel(entry.floor_band)}
      </div>

      {entry.source === "CELL_LAST" && (
        <>
          <Value>
            {ymLabel(entry.last_deal_ym)} 실거래 {toEok(entry.last_price_manwon)}
          </Value>
          <div className="text-[12.5px] text-muted-foreground">
            최근 2년 거래 {num(entry.n_trades_24m, "건")}
          </div>
        </>
      )}

      {entry.source === "COMPLEX_MEAN" && (
        <Value>
          이 면적 거래 없음 · 단지 평균 {num(entry.mean_price_per_m2_24m, "만원/㎡")}
        </Value>
      )}

      {entry.source === "MODEL" && (
        <>
          <Value accent>
            추정 {entry.est_low}~{entry.est_high}만원/㎡
          </Value>
          <div className="text-[12.5px] text-muted-foreground">
            이 단지는 최근 2년 거래가 없습니다
          </div>
          <Badge className="mt-1" variant={entry.est_confidence === "HIGH" ? "accent" : "neutral"}>
            신뢰도 {entry.est_confidence}
          </Badge>
        </>
      )}

      {entry.source === "EXCLUDED" && (
        <div className="text-[12.5px] text-muted-foreground">
          임대 전용 단지로 매매 거래가 없습니다
        </div>
      )}
    </div>
  );
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
  const coords = points
    .map((point, pointIndex) => {
      const x = pad + pointIndex * step;
      const y = height - pad - ((point[1] - min) / span) * (height - pad * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  return (
    <div className="my-1.5 flex items-center gap-2">
      <span className="text-[12.5px] text-muted-foreground">{series.area_type}㎡ 5년 추이</span>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="h-[34px] w-[140px] shrink-0"
        role="img"
        aria-label={`${points[0][0]}부터 ${points[points.length - 1][0]}까지 가격 추세`}
      >
        <polyline points={coords} fill="none" stroke="var(--primary)" strokeWidth="1.7" />
      </svg>
    </div>
  );
}

function SubHeading({ children }: { children: React.ReactNode }) {
  return (
    <h4 className="mt-4 mb-2 border-b border-border pb-1 text-[12.5px] tracking-wide text-muted-foreground">
      {children}
    </h4>
  );
}

function Flag({ children }: { children: React.ReactNode }) {
  return (
    <p className="my-2 rounded-md bg-neutral-soft px-3 py-2 text-[12.5px] text-neutral-strong">
      {children}
    </p>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="py-1 text-[12.5px] text-muted-foreground">{children}</p>;
}

function Value({ children, accent }: { children: React.ReactNode; accent?: boolean }) {
  return (
    <div className={cn("text-[15px] font-bold", accent && "text-primary-hover")}>{children}</div>
  );
}
