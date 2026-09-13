"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { BasketBar } from "@/components/basket-bar";
import { CompareSection } from "@/components/compare-section";
import { DetailPanel } from "@/components/detail-panel";
import { MapPreview } from "@/components/map-preview";
import { SearchPanel } from "@/components/search-panel";
import { MAX_RESULTS, fetchComplex, loadIndex } from "@/lib/data";
import { isFailed } from "@/lib/format";
import { useBasket } from "@/lib/use-basket";
import { cn } from "@/lib/utils";
import type { ComplexPayload, IndexComplex, QuickFilter } from "@/lib/types";

export default function Page() {
  const [complexes, setComplexes] = useState<IndexComplex[]>([]);
  const [indexStatus, setIndexStatus] = useState("인덱스를 불러오는 중입니다.");
  const [isDemo, setIsDemo] = useState(false);

  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<QuickFilter>("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const [payloads, setPayloads] = useState<ComplexPayload[]>([]);
  const [compareOpen, setCompareOpen] = useState(false);
  const [compareLoading, setCompareLoading] = useState(false);
  const [compareError, setCompareError] = useState<string | null>(null);
  const [revealKey, setRevealKey] = useState(0);

  const { basket, has, toggle, clear, full } = useBasket();
  const compareRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let active = true;
    loadIndex().then(({ payload, isDemo: demo }) => {
      if (!active) return;
      setComplexes(payload.complexes);
      setIsDemo(demo);
    });
    return () => {
      active = false;
    };
  }, []);

  const results = useMemo(() => {
    const term = query.trim();
    return complexes.filter((item) => {
      if (filter === "ready" && isFailed(item)) return false;
      if (filter === "review" && !isFailed(item)) return false;
      if (term.length === 0) return true;
      return item.n.includes(term) || item.u.includes(term) || item.g.includes(term);
    });
  }, [complexes, query, filter]);

  // 결과가 바뀌어 선택이 사라지면 첫 항목으로 되돌린다.
  useEffect(() => {
    if (results.length === 0) {
      setSelectedId(null);
      return;
    }
    setSelectedId((current) =>
      current !== null && results.some((item) => item.id === current) ? current : results[0].id,
    );
  }, [results]);

  useEffect(() => {
    if (complexes.length === 0) return;
    if (results.length === 0) {
      setIndexStatus("조건에 맞는 단지를 찾지 못했습니다.");
      return;
    }
    const shown = Math.min(results.length, MAX_RESULTS);
    const prefix = isDemo ? "데모 데이터 " : "";
    setIndexStatus(`${prefix}${results.length.toLocaleString()}곳 중 ${shown}곳 표시`);
  }, [results, complexes, isDemo]);

  const selected = useMemo(
    () => results.find((item) => item.id === selectedId) ?? null,
    [results, selectedId],
  );

  const togglePick = useCallback(
    (item: IndexComplex) => toggle({ id: item.id, name: item.n }),
    [toggle],
  );

  const openCompare = useCallback(() => {
    setCompareOpen(true);
    setCompareLoading(true);
    setCompareError(null);
    // key 를 바꿔 다시 mount 시킨다. 두 번째 이후에도 등장 애니메이션과 링이 재생된다.
    setRevealKey((current) => current + 1);

    // 데이터를 기다리지 않고 먼저 내려간다. 섹션은 이미 '불러오는 중'으로 자리를 잡는다.
    requestAnimationFrame(() => {
      compareRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    });

    Promise.all(basket.map((entry) => fetchComplex(entry.id)))
      .then((all) => {
        setPayloads(all);
        setCompareLoading(false);
      })
      .catch(() => {
        setPayloads([]);
        setCompareLoading(false);
        setCompareError("단지 정보를 불러오지 못했습니다.");
      });
  }, [basket]);

  const clearBasket = useCallback(() => {
    clear();
    setCompareOpen(false);
    setPayloads([]);
  }, [clear]);

  return (
    // 하단 비교 바가 fixed 라 그만큼 바닥을 비워야 비교서와 푸터가 가려지지 않는다.
    // 담은 게 없으면 바도 없으므로 그때는 여백을 주지 않는다.
    <div className={cn("px-3 md:px-4", basket.length > 0 ? "pb-32 lg:pb-24" : "pb-6")}>
      <header className="sticky top-0 z-20 mx-auto flex w-[min(1320px,100%)] items-center justify-between gap-3 bg-muted/85 py-2 backdrop-blur-md">
        <a href="#search-section" className="inline-flex min-w-0 items-center gap-2">
          <span
            aria-hidden="true"
            className="size-[30px] rounded-md bg-gradient-to-br from-primary to-primary/75 shadow-[0_6px_16px_rgb(65_54_232/0.24)]"
          />
          <span className="min-w-0">
            <strong className="block text-[15px] leading-tight">임앤장</strong>
            <small className="block text-[11px] text-muted-foreground">서울 아파트 비교</small>
          </span>
        </a>
        <nav className="flex items-center gap-1 text-[12.5px]" aria-label="주요 화면">
          <a
            href="#search-section"
            className="rounded-sm px-2 py-1.5 text-muted-foreground transition-colors duration-150 hover:bg-primary-soft hover:text-primary-hover"
          >
            탐색
          </a>
          <a
            href="#compare-section"
            className="rounded-sm px-2 py-1.5 text-muted-foreground transition-colors duration-150 hover:bg-primary-soft hover:text-primary-hover"
          >
            비교서
          </a>
        </nav>
      </header>

      <main className="mx-auto grid w-[min(1320px,100%)] grid-cols-1 items-start gap-3 md:grid-cols-[minmax(260px,340px)_minmax(0,1fr)] lg:grid-cols-[minmax(280px,360px)_minmax(0,1fr)_minmax(260px,320px)]">
        <SearchPanel
          query={query}
          onQueryChange={setQuery}
          filter={filter}
          onFilterChange={setFilter}
          results={results}
          status={indexStatus}
          selectedId={selectedId}
          onSelect={setSelectedId}
          isPicked={has}
          onTogglePick={togglePick}
        />

        <MapPreview items={results} selectedId={selectedId} onSelect={setSelectedId} />

        <DetailPanel
          item={selected}
          picked={selected !== null && has(selected.id)}
          onTogglePick={() => selected !== null && togglePick(selected)}
        />
      </main>

      {/* 비교서는 grid 밖에 둔다. 검색 패널이 sticky 라 같은 grid 안에 있으면
          행 경계에서 겹쳐 보인다. 여기로 빼면 항상 main 아래에 전체 폭으로 놓인다. */}
      {compareOpen && (
        <div
          key={revealKey}
          ref={compareRef}
          className="animate-reveal-up reveal-ring mx-auto mt-3 w-[min(1320px,100%)] scroll-mt-16"
        >
          <CompareSection payloads={payloads} loading={compareLoading} error={compareError} />
        </div>
      )}

      <BasketBar
        basket={basket}
        full={full}
        loading={compareLoading}
        opened={compareOpen}
        onCompare={openCompare}
        onClear={clearBasket}
      />

      <footer className="mx-auto mt-6 w-[min(1320px,100%)] border-t border-border pt-4 text-[11px] text-muted-foreground">
        <p className="my-1">
          실거래가는 국토교통부 공개 자료입니다. 추정값은 모델 산출이며 실제 거래가가 아닙니다.
        </p>
        <p className="my-1">
          향, 호수, 실내 상태, 실제 소음은 데이터로 알 수 없습니다. 현장 확인이 필요합니다.
        </p>
      </footer>
    </div>
  );
}
