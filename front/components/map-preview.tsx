"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { Card } from "@/components/ui/card";
import { SectionHeading } from "@/components/ui/section-heading";
import { MAX_MARKERS, projectToMap } from "@/lib/data";
import { isFailed } from "@/lib/format";
import type { IndexComplex } from "@/lib/types";
import { cn } from "@/lib/utils";

type KakaoLatLng = {
  getLat: () => number;
  getLng: () => number;
};

type KakaoBounds = {
  extend: (position: KakaoLatLng) => void;
  getSouthWest: () => KakaoLatLng;
  getNorthEast: () => KakaoLatLng;
};

type KakaoMap = {
  setBounds: (bounds: KakaoBounds) => void;
  getBounds: () => KakaoBounds;
  setCenter: (position: KakaoLatLng) => void;
  setLevel: (level: number) => void;
  panTo: (position: KakaoLatLng) => void;
  relayout: () => void;
};

type KakaoCustomOverlay = {
  setMap: (map: KakaoMap | null) => void;
  setZIndex: (zIndex: number) => void;
};

type KakaoClusterer = {
  addMarkers: (markers: KakaoCustomOverlay[]) => void;
  clear: () => void;
};

type KakaoSdk = {
  maps: {
    load: (callback: () => void) => void;
    Map: new (container: HTMLElement, options: { center: KakaoLatLng; level: number }) => KakaoMap;
    LatLng: new (lat: number, lng: number) => KakaoLatLng;
    LatLngBounds: new () => KakaoBounds;
    CustomOverlay: new (options: {
      position: KakaoLatLng;
      content: HTMLElement;
      yAnchor: number;
      zIndex: number;
    }) => KakaoCustomOverlay;
    MarkerClusterer: new (options: {
      map: KakaoMap;
      averageCenter: boolean;
      minLevel: number;
      gridSize: number;
      calculator: number[];
      styles: Record<string, string>[];
    }) => KakaoClusterer;
    event: {
      addListener: (target: KakaoMap, type: string, handler: () => void) => void;
      removeListener: (target: KakaoMap, type: string, handler: () => void) => void;
    };
  };
};

declare global {
  interface Window {
    kakao?: KakaoSdk;
  }
}

type MarkerOverlay = {
  item: IndexComplex;
  marker: HTMLButtonElement;
  overlay: KakaoCustomOverlay;
  onClick: () => void;
};

// 이 레벨 이상(더 넓게 본 화면)에서만 가까운 단지를 숫자 묶음으로 합친다.
const CLUSTER_MIN_LEVEL = 6;
// 화면 가장자리에서 끊겨 보이지 않도록 보이는 영역보다 사방 20% 넓게 그린다.
const VIEWPORT_PADDING = 0.2;
const CLUSTER_STYLES = [36, 44, 52].map((size) => ({
  width: `${size}px`,
  height: `${size}px`,
  lineHeight: `${size}px`,
  borderRadius: "50%",
  background: "rgba(65, 54, 232, 0.86)",
  boxShadow: "0 0 0 4px rgba(65, 54, 232, 0.18)",
  color: "#fff",
  fontSize: "12px",
  fontWeight: "600",
  textAlign: "center",
}));

let kakaoSdkPromise: Promise<KakaoSdk> | null = null;

function markerClassName(item: IndexComplex, selected: boolean) {
  // relative: 흰 점(::before)의 기준을 overlay wrapper가 아닌 버튼으로 둔다.
  // block: inline 줄높이로 wrapper가 커져 점이 세로로 늘어나고 yAnchor가 어긋나는 것을 막는다.
  return cn(
    "relative block size-4 rounded-full transition-[transform,background-color,box-shadow] duration-200 ease-[var(--ease-out-soft)]",
    "before:absolute before:inset-1 before:rounded-full before:bg-white before:content-['']",
    "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
    isFailed(item)
      ? "bg-neutral-strong shadow-[0_0_0_3px_rgb(105_115_134/0.14),0_4px_10px_rgb(29_36_51/0.1)]"
      : "bg-primary shadow-[0_0_0_3px_rgb(65_54_232/0.14),0_4px_10px_rgb(29_36_51/0.14)]",
    !selected && "hover:scale-115",
    selected && "scale-135 bg-primary-hover shadow-[0_0_0_5px_rgb(65_54_232/0.2),0_8px_18px_rgb(29_36_51/0.2)]",
  );
}

function styleMarker({ item, marker, overlay }: MarkerOverlay, selected: boolean) {
  marker.className = markerClassName(item, selected);
  marker.setAttribute("aria-pressed", String(selected));
  overlay.setZIndex(selected ? 10 : 1);
}

function isInBounds(bounds: KakaoBounds, item: IndexComplex, padding: number) {
  const sw = bounds.getSouthWest();
  const ne = bounds.getNorthEast();
  const padLat = (ne.getLat() - sw.getLat()) * padding;
  const padLng = (ne.getLng() - sw.getLng()) * padding;
  const lat = item.lat as number;
  const lng = item.lng as number;
  return lat >= sw.getLat() - padLat && lat <= ne.getLat() + padLat && lng >= sw.getLng() - padLng && lng <= ne.getLng() + padLng;
}

function loadKakaoSdk(key: string): Promise<KakaoSdk> {
  if (window.kakao?.maps) return Promise.resolve(window.kakao);
  if (kakaoSdkPromise) return kakaoSdkPromise;

  kakaoSdkPromise = new Promise((resolve, reject) => {
    const scriptId = "kakao-maps-sdk";
    const existingScript = document.getElementById(scriptId) as HTMLScriptElement | null;
    const script = existingScript ?? document.createElement("script");
    let settled = false;
    const finish = (error?: Error) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timeout);
      if (window.kakao?.maps) resolve(window.kakao);
      else reject(error ?? new Error("Kakao Maps SDK를 찾을 수 없습니다."));
    };
    const timeout = window.setTimeout(
      () => finish(new Error("Kakao Maps SDK 로드 시간이 초과되었습니다.")),
      10_000,
    );

    script.addEventListener("load", () => finish(), { once: true });
    script.addEventListener("error", () => finish(new Error("Kakao Maps SDK를 불러오지 못했습니다.")), {
      once: true,
    });

    if (!existingScript) {
      script.id = scriptId;
      script.async = true;
      script.src = `https://dapi.kakao.com/v2/maps/sdk.js?appkey=${encodeURIComponent(key)}&autoload=false&libraries=clusterer`;
      document.head.appendChild(script);
    }
  });

  return kakaoSdkPromise;
}

/**
 * Kakao Maps JavaScript SDK로 지도를 표시한다.
 * 검색 결과 전체를 대상으로 하되, 확대·이동이 끝날 때마다(idle) 보이는 영역의 단지만
 * 클러스터러에 넣는다. 넓게 보면 숫자 묶음, 확대하면 개별 점이 된다.
 * 키가 없거나 SDK 로드에 실패하면 좌표 기반 미리보기를 그대로 표시한다.
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
  const mapElementRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<KakaoMap | null>(null);
  const sdkRef = useRef<KakaoSdk | null>(null);
  const clustererRef = useRef<KakaoClusterer | null>(null);
  // 마커 DOM은 단지별로 한 번만 만들고 확대·이동·검색 사이에 재사용한다.
  const overlayCacheRef = useRef(new Map<string, MarkerOverlay>());
  const onSelectRef = useRef(onSelect);
  const selectedIdRef = useRef(selectedId);
  const [mapStatus, setMapStatus] = useState<"loading" | "ready" | "fallback">("loading");
  const mappable = useMemo(() => items.filter((item) => item.lat !== null && item.lng !== null), [items]);
  const previewMarkers = useMemo(() => mappable.slice(0, MAX_MARKERS), [mappable]);

  useEffect(() => {
    onSelectRef.current = onSelect;
  }, [onSelect]);

  useEffect(() => {
    selectedIdRef.current = selectedId;
  }, [selectedId]);

  useEffect(() => {
    const key = process.env.NEXT_PUBLIC_KAKAO_JS_KEY;
    if (!key || !mapElementRef.current) {
      setMapStatus("fallback");
      return;
    }

    let disposed = false;
    let mapsLoadTimedOut = false;
    let mapsLoadTimeout: number | undefined;
    const overlayCache = overlayCacheRef.current;
    loadKakaoSdk(key)
      .then((sdk) => {
        mapsLoadTimeout = window.setTimeout(() => {
          mapsLoadTimedOut = true;
          if (!disposed) setMapStatus("fallback");
        }, 10_000);
        sdk.maps.load(() => {
          window.clearTimeout(mapsLoadTimeout);
          if (disposed || mapsLoadTimedOut || !mapElementRef.current) return;
          sdkRef.current = sdk;
          const map = new sdk.maps.Map(mapElementRef.current, {
            center: new sdk.maps.LatLng(37.5665, 126.978),
            level: 9,
          });
          mapRef.current = map;
          clustererRef.current = new sdk.maps.MarkerClusterer({
            map,
            averageCenter: true,
            minLevel: CLUSTER_MIN_LEVEL,
            gridSize: 60,
            calculator: [20, 100],
            styles: CLUSTER_STYLES,
          });
          setMapStatus("ready");
        });
      })
      .catch(() => {
        if (!disposed) setMapStatus("fallback");
      });
    // SDK 초기화 후 tile 요청 실패는 감지하지 않는다.

    return () => {
      disposed = true;
      window.clearTimeout(mapsLoadTimeout);
      clustererRef.current?.clear();
      clustererRef.current = null;
      overlayCache.forEach(({ marker, onClick, overlay }) => {
        marker.removeEventListener("click", onClick);
        overlay.setMap(null);
      });
      overlayCache.clear();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    const sdk = sdkRef.current;
    const clusterer = clustererRef.current;
    if (mapStatus !== "ready" || !map || !sdk || !clusterer) return;

    const overlayFor = (item: IndexComplex) => {
      const cached = overlayCacheRef.current.get(item.id);
      if (cached) return cached;
      const marker = document.createElement("button");
      marker.type = "button";
      marker.title = item.n;
      marker.setAttribute("aria-label", `${item.n} 선택`);
      const onClick = () => onSelectRef.current(item.id);
      marker.addEventListener("click", onClick);
      const entry: MarkerOverlay = {
        item,
        marker,
        onClick,
        overlay: new sdk.maps.CustomOverlay({
          position: new sdk.maps.LatLng(item.lat as number, item.lng as number),
          content: marker,
          yAnchor: 0.5,
          zIndex: 1,
        }),
      };
      styleMarker(entry, item.id === selectedIdRef.current);
      overlayCacheRef.current.set(item.id, entry);
      return entry;
    };

    // 선택된 단지는 화면 밖이어도 빠지지 않게 항상 포함한다.
    const renderVisible = () => {
      const bounds = map.getBounds();
      const visible = mappable.filter(
        (item) => item.id === selectedIdRef.current || isInBounds(bounds, item, VIEWPORT_PADDING),
      );
      clusterer.clear();
      clusterer.addMarkers(visible.map((item) => overlayFor(item).overlay));
    };

    if (mappable.length > 1) {
      const bounds = new sdk.maps.LatLngBounds();
      mappable.forEach((item) => bounds.extend(new sdk.maps.LatLng(item.lat as number, item.lng as number)));
      map.setBounds(bounds);
    } else if (mappable.length === 1) {
      map.setCenter(new sdk.maps.LatLng(mappable[0].lat as number, mappable[0].lng as number));
      map.setLevel(4);
    }

    sdk.maps.event.addListener(map, "idle", renderVisible);
    renderVisible();
    return () => sdk.maps.event.removeListener(map, "idle", renderVisible);
  }, [mapStatus, mappable]);

  useEffect(() => {
    const map = mapRef.current;
    const sdk = sdkRef.current;
    if (mapStatus !== "ready" || !map || !sdk) return;

    overlayCacheRef.current.forEach((entry) => styleMarker(entry, entry.item.id === selectedId));

    // 이미 보이는 단지를 고른 경우 사용자가 맞춰 둔 화면을 움직이지 않는다.
    const selected = mappable.find((item) => item.id === selectedId);
    if (selected && !isInBounds(map.getBounds(), selected, 0)) {
      map.panTo(new sdk.maps.LatLng(selected.lat as number, selected.lng as number));
    }
  }, [mapStatus, mappable, selectedId]);

  useEffect(() => {
    const map = mapRef.current;
    const element = mapElementRef.current;
    if (mapStatus !== "ready" || !map || !element) return;

    const observer = new ResizeObserver(() => map.relayout());
    observer.observe(element);
    return () => observer.disconnect();
  }, [mapStatus]);

  const fallback = mapStatus === "fallback";

  return (
    <Card className="relative overflow-hidden">
      <div className="pointer-events-none absolute inset-x-2 top-2 z-10 flex flex-col justify-between gap-1.5 rounded-md border border-white/60 bg-white/80 p-3 shadow-[var(--shadow-panel)] backdrop-blur-md sm:flex-row sm:items-start">
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

      {fallback ? (
        <div
          className="relative h-[clamp(300px,46dvh,560px)] overflow-hidden bg-[radial-gradient(circle_at_30%_34%,rgb(65_54_232/0.07),transparent_26%),linear-gradient(145deg,#ffffff,#f6f7f9)] md:h-[clamp(420px,62dvh,680px)]"
          role="group"
          aria-label="서울 아파트 위치 미리보기"
        >
          <p className="absolute inset-x-3 top-24 z-10 rounded-md bg-white/85 px-2 py-1 text-center text-[11px] text-muted-foreground shadow-sm">
            지도를 불러오지 못해 좌표 미리보기로 표시합니다
          </p>
          <div
            aria-hidden="true"
            className="absolute -inset-[20%] rotate-[-8deg] opacity-50 [background-image:linear-gradient(rgb(105_115_134/0.12)_1px,transparent_1px),linear-gradient(90deg,rgb(105_115_134/0.12)_1px,transparent_1px)] [background-size:54px_54px]"
          />
          <div
            aria-hidden="true"
            className="absolute -inset-x-[8%] top-[43%] h-[74px] rotate-[-10deg] bg-primary/[0.07]"
          />
          <div className="absolute inset-0">
            {previewMarkers.map((item) => {
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
      ) : (
        <div
          ref={mapElementRef}
          className="h-[clamp(300px,46dvh,560px)] bg-muted md:h-[clamp(420px,62dvh,680px)]"
          role="application"
          aria-label="서울 아파트 위치 지도"
        />
      )}
    </Card>
  );
}
