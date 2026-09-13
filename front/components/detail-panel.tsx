"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { SectionHeading } from "@/components/ui/section-heading";
import { builtYearLabel, isFailed, num } from "@/lib/format";
import type { IndexComplex } from "@/lib/types";

export function DetailPanel({
  item,
  picked,
  onTogglePick,
}: {
  item: IndexComplex | null;
  picked: boolean;
  onTogglePick: () => void;
}) {
  return (
    <Card
      className="grid content-start gap-3 p-3 md:col-start-2 lg:sticky lg:top-[62px] lg:col-auto md:p-4"
      aria-live="polite"
    >
      {item === null ? (
        <>
          <SectionHeading eyebrow="Selected" title="단지를 선택하세요" />
          <p className="text-[12.5px] text-muted-foreground">
            검색 결과나 지도 포인트를 선택하면 요약 정보가 표시됩니다.
          </p>
        </>
      ) : (
        // key 를 주면 선택이 바뀔 때만 fade 가 다시 재생된다.
        <div key={item.id} className="grid content-start gap-3 animate-fade-in">
          <SectionHeading eyebrow="Selected" title={item.n} />

          <p className="text-[12.5px] text-muted-foreground">
            {item.g} {item.u} · {builtYearLabel(item.y)}
          </p>

          <div className="flex flex-wrap items-center gap-1.5">
            <Badge variant={isFailed(item) ? "neutral" : "accent"}>
              {isFailed(item) ? "확인 필요" : "분석 가능"}
            </Badge>
            <Badge>{num(item.h, "세대")}</Badge>
          </div>

          {isFailed(item) && (
            <p className="rounded-md bg-neutral-soft px-3 py-2 text-[12.5px] text-neutral-strong">
              건물 footprint 미매칭으로 일조·조망 분석 불가
            </p>
          )}

          <dl className="grid grid-cols-[repeat(auto-fit,minmax(120px,1fr))] gap-1.5">
            <Fact label="구" value={item.g} />
            <Fact label="법정동" value={item.u} />
            <Fact label="위도" value={item.lat === null ? "정보 없음" : item.lat.toFixed(5)} />
            <Fact label="경도" value={item.lng === null ? "정보 없음" : item.lng.toFixed(5)} />
          </dl>

          <Button
            type="button"
            variant={picked ? "soft" : "default"}
            aria-pressed={picked}
            onClick={onTogglePick}
          >
            {picked ? "담김" : "비교에 담기"}
          </Button>
        </div>
      )}
    </Card>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-muted p-2">
      <dt className="text-[11px] text-muted-foreground">{label}</dt>
      <dd className="text-sm font-medium">{value}</dd>
    </div>
  );
}
