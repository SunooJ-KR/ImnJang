"use client";

import { Button } from "@/components/ui/button";
import { MAX_BASKET } from "@/lib/data";
import type { BasketItem } from "@/lib/types";

export function BasketBar({
  basket,
  full,
  onCompare,
  onClear,
}: {
  basket: BasketItem[];
  full: boolean;
  onCompare: () => void;
  onClear: () => void;
}) {
  if (basket.length === 0) return null;

  return (
    <div className="fixed inset-x-0 bottom-0 z-30 flex flex-col items-stretch gap-1.5 border-t border-border bg-card/95 px-3 pt-2 pb-[calc(0.5rem+env(safe-area-inset-bottom,0px))] shadow-[var(--shadow-float)] backdrop-blur-md animate-slide-up sm:flex-row sm:items-center sm:gap-3 lg:inset-x-auto lg:bottom-4 lg:left-1/2 lg:w-[min(1320px,calc(100%-3rem))] lg:-translate-x-1/2 lg:rounded-lg lg:border lg:px-4 lg:py-2.5">
      <p className="min-w-0 flex-1 text-[12.5px] break-words">
        담은 단지 {basket.length}곳: {basket.map((entry) => entry.name).join(", ")}
        {full && (
          <span className="ml-1 text-muted-foreground">
            (최대 {MAX_BASKET}곳까지 담을 수 있습니다)
          </span>
        )}
      </p>
      <div className="flex shrink-0 gap-1.5">
        <Button type="button" className="flex-1" onClick={onCompare}>
          비교하기
        </Button>
        <Button type="button" variant="outline" className="flex-1" onClick={onClear}>
          비우기
        </Button>
      </div>
    </div>
  );
}
