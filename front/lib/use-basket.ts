"use client";

import { useCallback, useEffect, useState } from "react";

import { MAX_BASKET } from "@/lib/data";
import type { BasketItem } from "@/lib/types";

const STORAGE_KEY = "imnjang.basket.v1";

function read(): BasketItem[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? (parsed as BasketItem[]).slice(0, MAX_BASKET) : [];
  } catch {
    return [];
  }
}

export function useBasket() {
  const [basket, setBasket] = useState<BasketItem[]>([]);
  const [full, setFull] = useState(false);

  // 정적 export라 첫 렌더는 서버에서 만들어진다. localStorage 는 mount 후에만 읽는다.
  useEffect(() => {
    setBasket(read());
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(basket));
    } catch {
      // 저장에 실패해도 이번 세션의 화면 상태는 유지한다.
    }
  }, [basket]);

  const has = useCallback(
    (id: string) => basket.some((entry) => entry.id === id),
    [basket],
  );

  const toggle = useCallback((entry: BasketItem) => {
    setBasket((current) => {
      if (current.some((existing) => existing.id === entry.id)) {
        setFull(false);
        return current.filter((existing) => existing.id !== entry.id);
      }
      if (current.length >= MAX_BASKET) {
        setFull(true);
        return current;
      }
      setFull(false);
      return [...current, entry];
    });
  }, []);

  const clear = useCallback(() => {
    setBasket([]);
    setFull(false);
  }, []);

  return { basket, has, toggle, clear, full };
}
