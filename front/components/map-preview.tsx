"use client";

import { Card } from "@/components/ui/card";
import { SectionHeading } from "@/components/ui/section-heading";
import { MAX_MARKERS, projectToMap } from "@/lib/data";
import { isFailed } from "@/lib/format";
import type { IndexComplex } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * 지도 API 키가 아직 없다. 실제 배경지도 타일이 아니라 좌표 기반 미리보기다.
 * VWorld 연동 시 이 컴포넌트를 OpenLayers adapter로 교체한다.
 */
export function MapPreview({
  items,
  selectedId,
  onSelect,
}: {
  items: IndexComplex[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const markers = items
    .filter((item) => item.lat !== null && item.lng !== null)
    .slice(0, MAX_MARKERS);

  return (
    <Card className="relative overflow-hidden">
      <div className="absolute inset-x-2 top-2 z-10 flex flex-col justify-between gap-1.5 rounded-md border border-white/60 bg-white/80 p-3 shadow-[var(--shadow-panel)] backdrop-blur-md sm:flex-row sm:items-start">
        <SectionHeading eyebrow="Map" title="서울 위치 탐색" />
        <div
          className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground"
          aria-label="지도 범례"
        >
          <span className="inline-flex items-center gap-1">
            <i className="size-[7px] rounded-full bg-primary" />
            분석 가능
          </span>
          <span className="inline-flex items-center gap-1">
            <i className="size-[7px] rounded-full bg-neutral-strong" />
            확인 필요
          </span>
        </div>
      </div>

      <div
        className="relative h-[clamp(300px,46dvh,560px)] overflow-hidden bg-[radial-gradient(circle_at_30%_34%,rgb(65_54_232/0.07),transparent_26%),linear-gradient(145deg,#ffffff,#f6f7f9)] md:h-[clamp(420px,62dvh,680px)]"
        role="img"
        aria-label="서울 아파트 위치 미리보기"
      >
        <div
          aria-hidden="true"
          className="absolute -inset-[20%] rotate-[-8deg] opacity-50 [background-image:linear-gradient(rgb(105_115_134/0.12)_1px,transparent_1px),linear-gradient(90deg,rgb(105_115_134/0.12)_1px,transparent_1px)] [background-size:54px_54px]"
        />
        <div
          aria-hidden="true"
          className="absolute -inset-x-[8%] top-[43%] h-[74px] rotate-[-10deg] bg-primary/[0.07]"
        />

        <div className="absolute inset-0">
          {markers.map((item) => {
            const point = projectToMap(item.lat as number, item.lng as number);
            const selected = selectedId === item.id;
            const failed = isFailed(item);

            return (
              <button
                key={item.id}
                type="button"
                title={item.n}
                aria-label={`${item.n} 선택`}
                aria-pressed={selected}
                onClick={() => onSelect(item.id)}
                style={{ left: `${point.x}%`, top: `${point.y}%` }}
                className={cn(
                  "absolute size-4 -translate-x-1/2 -translate-y-1/2 rounded-full transition-[transform,background-color,box-shadow] duration-200 ease-[var(--ease-out-soft)]",
                  "before:absolute before:inset-1 before:rounded-full before:bg-white before:content-['']",
                  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
                  failed
                    ? "bg-neutral-strong shadow-[0_0_0_3px_rgb(105_115_134/0.14),0_4px_10px_rgb(29_36_51/0.1)]"
                    : "bg-primary shadow-[0_0_0_3px_rgb(65_54_232/0.14),0_4px_10px_rgb(29_36_51/0.14)]",
                  !selected && "hover:-translate-x-1/2 hover:-translate-y-1/2 hover:scale-115",
                  selected &&
                    "z-10 scale-135 bg-primary-hover shadow-[0_0_0_5px_rgb(65_54_232/0.2),0_8px_18px_rgb(29_36_51/0.2)]",
                )}
              />
            );
          })}
        </div>
      </div>
    </Card>
  );
}
