"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { Card } from "@/components/ui/card";
import { SectionHeading } from "@/components/ui/section-heading";
import { MAX_MARKERS, projectToMap } from "@/lib/data";
import { isFailed } from "@/lib/format";
import type { IndexComplex } from "@/lib/types";
import { cn } from "@/lib/utils";

type KakaoLatLng = object;
type KakaoBounds = object;

type KakaoMap = {
  setBounds: (bounds: KakaoBounds) => void;
  setCenter: (position: KakaoLatLng) => void;
  setLevel: (level: number) => void;
  panTo: (position: KakaoLatLng) => void;
  relayout: () => void;
};

type KakaoCustomOverlay = {
  setMap: (map: KakaoMap | null) => void;
  setZIndex: (zIndex: number) => void;
};

type KakaoSdk = {
  maps: {
    load: (callback: () => void) => void;
    Map: new (container: HTMLElement, options: { center: KakaoLatLng; level: number }) => KakaoMap;
    LatLng: new (lat: number, lng: number) => KakaoLatLng;
    LatLngBounds: new () => KakaoBounds & { extend: (position: KakaoLatLng) => void };
    CustomOverlay: new (options: {
      position: KakaoLatLng;
      content: HTMLElement;
      yAnchor: number;
      zIndex: number;
    }) => KakaoCustomOverlay;
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

let kakaoSdkPromise: Promise<KakaoSdk> | null = null;

function markerClassName(item: IndexComplex, selected: boolean) {
  return cn(
    "size-4 rounded-full transition-[transform,background-color,box-shadow] duration-200 ease-[var(--ease-out-soft)]",
    "before:absolute before:inset-1 before:rounded-full before:bg-white before:content-['']",
    "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
    isFailed(item)
      ? "bg-neutral-strong shadow-[0_0_0_3px_rgb(105_115_134/0.14),0_4px_10px_rgb(29_36_51/0.1)]"
      : "bg-primary shadow-[0_0_0_3px_rgb(65_54_232/0.14),0_4px_10px_rgb(29_36_51/0.14)]",
    !selected && "hover:scale-115",
    selected && "scale-135 bg-primary-hover shadow-[0_0_0_5px_rgb(65_54_232/0.2),0_8px_18px_rgb(29_36_51/0.2)]",
  );
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
      script.src = `https://dapi.kakao.com/v2/maps/sdk.js?appkey=${encodeURIComponent(key)}&autoload=false`;
      document.head.appendChild(script);
    }
  });

  return kakaoSdkPromise;
}

/**
 * Kakao Maps JavaScript SDK로 지도를 표시한다.
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
  const overlaysRef = useRef<MarkerOverlay[]>([]);
  const onSelectRef = useRef(onSelect);
  const [mapStatus, setMapStatus] = useState<"loading" | "ready" | "fallback">("loading");
  const markers = useMemo(
    () => items.filter((item) => item.lat !== null && item.lng !== null).slice(0, MAX_MARKERS),
    [items],
  );

  useEffect(() => {
    onSelectRef.current = onSelect;
  }, [onSelect]);

  useEffect(() => {
    const key = process.env.NEXT_PUBLIC_KAKAO_JS_KEY;
    if (!key || !mapElementRef.current) {
      setMapStatus("fallback");
      return;
    }

    let disposed = false;
    let mapsLoadTimedOut = false;
    let mapsLoadTimeout: number | undefined;
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
          mapRef.current = new sdk.maps.Map(mapElementRef.current, {
            center: new sdk.maps.LatLng(37.5665, 126.978),
            level: 9,
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
      overlaysRef.current.forEach(({ marker, onClick, overlay }) => {
        marker.removeEventListener("click", onClick);
        overlay.setMap(null);
      });
      overlaysRef.current = [];
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    const sdk = sdkRef.current;
    if (mapStatus !== "ready" || !map || !sdk) return;

    const bounds = new sdk.maps.LatLngBounds();
    const overlays = markers.map((item) => {
      const position = new sdk.maps.LatLng(item.lat as number, item.lng as number);
      const marker = document.createElement("button");
      marker.type = "button";
      marker.title = item.n;
      marker.setAttribute("aria-label", `${item.n} 선택`);
      marker.setAttribute("aria-pressed", "false");
      marker.className = markerClassName(item, false);
      const onClick = () => onSelectRef.current(item.id);
      marker.addEventListener("click", onClick);
      bounds.extend(position);
      return {
        item,
        marker,
        onClick,
        overlay: new sdk.maps.CustomOverlay({
          position,
          content: marker,
          yAnchor: 0.5,
          zIndex: 1,
        }),
      };
    });
    overlaysRef.current = overlays;
    overlays.forEach(({ overlay }) => overlay.setMap(map));

    if (markers.length > 1) map.setBounds(bounds);
    else if (markers.length === 1) {
      map.setCenter(new sdk.maps.LatLng(markers[0].lat as number, markers[0].lng as number));
      map.setLevel(5);
    }

    return () => {
      overlays.forEach(({ marker, onClick, overlay }) => {
        marker.removeEventListener("click", onClick);
        overlay.setMap(null);
      });
      if (overlaysRef.current === overlays) overlaysRef.current = [];
    };
  }, [mapStatus, markers]);

  useEffect(() => {
    const map = mapRef.current;
    const sdk = sdkRef.current;
    const selected = markers.find((item) => item.id === selectedId);
    if (mapStatus !== "ready" || !map || !sdk) return;

    overlaysRef.current.forEach(({ item, marker, overlay }) => {
      const isSelected = item.id === selectedId;
      marker.className = markerClassName(item, isSelected);
      marker.setAttribute("aria-pressed", String(isSelected));
      overlay.setZIndex(isSelected ? 10 : 1);
    });

    if (selected) {
      map.panTo(new sdk.maps.LatLng(selected.lat as number, selected.lng as number));
    }
  }, [mapStatus, markers, selectedId]);

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
