import type { FloorBand, IndexComplex, MatchConfidence } from "@/lib/types";

export function toEok(manwon: number | null): string {
  if (manwon === null || manwon === undefined) return "정보 없음";
  return `${(manwon / 10000).toFixed(2).replace(/\.?0+$/, "")}억`;
}

export function ymLabel(ym: string | null): string {
  if (!ym) return "정보 없음";
  const parts = String(ym).split("-");
  if (parts.length !== 2) return String(ym);
  return `${parts[0]}년 ${Number(parts[1])}월`;
}

export function bandLabel(band: FloorBand | null): string {
  if (band === "HIGH") return "고층";
  if (band === "MID") return "중층";
  if (band === "LOW") return "저층";
  return "층 정보 없음";
}

export function num(value: number | null, suffix = ""): string {
  if (value === null || value === undefined) return "정보 없음";
  return `${Number(value).toLocaleString()}${suffix}`;
}

export function ratio(value: number | null): string {
  if (value === null || value === undefined) return "정보 없음";
  return `${Math.round(value * 100)}%`;
}

export function yesNo(value: boolean | null): string {
  if (value === null || value === undefined) return "정보 없음";
  return value ? "예" : "아니오";
}

export function matchConfidence(item: IndexComplex): MatchConfidence {
  return item.match_confidence ?? "HIGH";
}

export function isFailed(item: IndexComplex): boolean {
  return matchConfidence(item) === "FAILED";
}

export function builtYearLabel(year: number | null): string {
  return year === null || year === undefined ? "준공연도 정보 없음" : `${year}년`;
}
