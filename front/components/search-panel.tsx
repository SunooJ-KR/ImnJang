"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { SectionHeading } from "@/components/ui/section-heading";
import { MAX_RESULTS } from "@/lib/data";
import { builtYearLabel, isFailed } from "@/lib/format";
import type { IndexComplex, QuickFilter } from "@/lib/types";
import { cn } from "@/lib/utils";

const FILTERS: { value: QuickFilter; label: string }[] = [
  { value: "all", label: "전체" },
  { value: "ready", label: "분석 가능" },
  { value: "review", label: "확인 필요" },
];

export function SearchPanel({
  query,
  onQueryChange,
  filter,
  onFilterChange,
  results,
  status,
  selectedId,
  onSelect,
  isPicked,
  onTogglePick,
}: {
  query: string;
  onQueryChange: (value: string) => void;
  filter: QuickFilter;
  onFilterChange: (value: QuickFilter) => void;
  results: IndexComplex[];
  status: string;
  selectedId: string | null;
  onSelect: (id: string) => void;
  isPicked: (id: string) => boolean;
  onTogglePick: (item: IndexComplex) => void;
}) {
  return (
    // md 이상에서만 sticky + 높이 제한이다.
    // 위 62px 은 sticky 헤더, 아래 88px 은 하단 비교 바와 여백 몫이다.
    // 이만큼 빼야 패널 아래쪽이 바에 가리거나 화면 밖으로 잘리지 않는다.
    // max-h 만 걸면 내용이 박스를 넘어 이웃 위로 그려지므로 overflow-hidden 을 함께 건다.
    <Card
      id="search-section"
      className="flex flex-col gap-3 p-3 md:sticky md:top-[62px] md:max-h-[calc(100dvh-150px)] md:overflow-hidden md:p-4"
    >
      {/* 패널 높이가 max-h 에 걸리면 flex 자식이 전부 줄어든다.
          스크롤을 맡는 결과 리스트만 flex-1 로 두고 나머지는 shrink-0 으로 고정한다. */}
      <SectionHeading as="h1" eyebrow="Search" title="관심 단지를 지도에서 고르기" className="shrink-0" />

      <label htmlFor="q" className="sr-only">
        단지명 또는 법정동
      </label>
      <div className="flex min-h-11 shrink-0 items-center gap-2 rounded-md border border-transparent bg-input px-3 transition-[background-color,border-color,box-shadow] duration-150 ease-[var(--ease-out-soft)] focus-within:border-primary/40 focus-within:bg-card focus-within:shadow-[0_0_0_3px_rgb(65_54_232/0.12)]">
        <SearchIcon />
        <Input
          id="q"
          type="search"
          placeholder="단지명 또는 동 이름"
          autoComplete="off"
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
        />
      </div>

      <div className="flex shrink-0 gap-1.5 overflow-x-auto pb-0.5 [scrollbar-width:none]" aria-label="빠른 조건">
        {FILTERS.map((option) => (
          <Button
            key={option.value}
            type="button"
            size="sm"
            variant={filter === option.value ? "default" : "ghost"}
            aria-pressed={filter === option.value}
            className={cn(filter !== option.value && "bg-input")}
            onClick={() => onFilterChange(option.value)}
          >
            {option.label}
          </Button>
        ))}
      </div>

      <p className="min-h-[18px] shrink-0 text-[12.5px] text-muted-foreground" role="status">
        {status}
      </p>

      <ul className="grid min-h-0 flex-1 gap-1.5 overflow-y-auto overscroll-contain" aria-label="검색 결과">
        {results.slice(0, MAX_RESULTS).map((item) => (
          <li key={item.id}>
            <ResultRow
              item={item}
              selected={selectedId === item.id}
              picked={isPicked(item.id)}
              onSelect={() => onSelect(item.id)}
              onTogglePick={() => onTogglePick(item)}
            />
          </li>
        ))}
      </ul>
    </Card>
  );
}

function ResultRow({
  item,
  selected,
  picked,
  onSelect,
  onTogglePick,
}: {
  item: IndexComplex;
  selected: boolean;
  picked: boolean;
  onSelect: () => void;
  onTogglePick: () => void;
}) {
  const failed = isFailed(item);

  return (
    <div
      className={cn(
        "grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 rounded-md border border-transparent bg-muted px-3 py-2 transition-[background-color,border-color] duration-150 ease-[var(--ease-out-soft)] hover:bg-primary-soft",
        selected && "border-primary/40 bg-primary-soft",
      )}
    >
      <button
        type="button"
        aria-current={selected ? "true" : undefined}
        onClick={onSelect}
        className="flex min-w-0 flex-col items-start gap-0.5 text-left focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
      >
        <strong className="text-[15px] font-bold break-words">{item.n}</strong>
        <span className="flex flex-wrap items-center gap-1.5">
          <span className="text-[12.5px] text-muted-foreground">
            {item.g} {item.u} · {builtYearLabel(item.y)}
          </span>
          <Badge variant={failed ? "neutral" : "accent"}>{failed ? "확인 필요" : "분석 가능"}</Badge>
        </span>
      </button>

      <Button
        type="button"
        size="sm"
        variant={picked ? "soft" : "default"}
        onClick={onTogglePick}
        aria-pressed={picked}
      >
        {picked ? "담김" : "담기"}
      </Button>
    </div>
  );
}

function SearchIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      className="size-4 shrink-0 text-muted-foreground"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
    >
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </svg>
  );
}
