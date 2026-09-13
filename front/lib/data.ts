import type { ComplexPayload, IndexComplex, IndexPayload } from "@/lib/types";

export const MAX_BASKET = 4;
export const MAX_RESULTS = 30;
export const MAX_MARKERS = 220;

/** 서울 경계 대략 범위. 지도 미리보기 좌표 투영에만 쓴다. */
export const SEOUL_BOUNDS = {
  minLng: 126.74,
  maxLng: 127.2,
  minLat: 37.42,
  maxLat: 37.72,
};

/** payload가 아직 없을 때 화면을 확인하기 위한 데모 데이터. */
export const DEMO_COMPLEXES: IndexComplex[] = [
  { id: "demo-1", n: "래미안대치팰리스", g: "강남구", u: "대치동", lat: 37.4977, lng: 127.0563, y: 2015, h: 1608, match_confidence: "HIGH" },
  { id: "demo-2", n: "마포래미안푸르지오", g: "마포구", u: "아현동", lat: 37.5551, lng: 126.9548, y: 2014, h: 3885, match_confidence: "MEDIUM" },
  { id: "demo-3", n: "헬리오시티", g: "송파구", u: "가락동", lat: 37.4979, lng: 127.1072, y: 2018, h: 9510, match_confidence: "HIGH" },
  { id: "demo-4", n: "DMC래미안e편한세상", g: "서대문구", u: "북가좌동", lat: 37.5743, lng: 126.9102, y: 2012, h: 3293, match_confidence: "LOW" },
  { id: "demo-5", n: "관악푸르지오", g: "관악구", u: "봉천동", lat: 37.4842, lng: 126.9454, y: 2004, h: 2104, match_confidence: "FAILED" },
  { id: "demo-6", n: "이펜하우스2단지", g: "양천구", u: "신정동", lat: 37.5129, lng: 126.8336, y: 2011, h: 944, match_confidence: "HIGH" },
];

/**
 * 인덱스를 불러온다. payload가 없으면 데모 데이터로 폴백한다.
 * isDemo 로 호출부가 안내 문구를 바꿀 수 있게 한다.
 */
export async function loadIndex(): Promise<{ payload: IndexPayload; isDemo: boolean }> {
  try {
    const res = await fetch("/data/index.json");
    if (!res.ok) throw new Error("index");
    const payload = (await res.json()) as IndexPayload;
    return { payload, isDemo: false };
  } catch {
    return { payload: { as_of: "demo", complexes: DEMO_COMPLEXES }, isDemo: true };
  }
}

/**
 * 단지 payload를 불러온다.
 *
 * 36.build_payload.py 는 .json.gz 로만 쓴다. 정적 호스팅은 이 파일을
 * Content-Type: application/gzip 으로 그냥 내려주기 때문에 브라우저가
 * 압축을 풀지 않는다. 그래서 DecompressionStream 으로 직접 푼다.
 * 서버가 Content-Encoding: gzip 을 붙여줬다면 이미 풀린 상태이므로 그대로 쓴다.
 */
export async function fetchComplex(id: string): Promise<ComplexPayload> {
  const plain = await fetch(`/data/complex/${id}.json`);
  if (plain.ok) return (await plain.json()) as ComplexPayload;

  const gz = await fetch(`/data/complex/${id}.json.gz`);
  if (!gz.ok) throw new Error(`payload 없음: ${id}`);

  const decodedByServer = gz.headers.get("content-encoding")?.includes("gzip") ?? false;
  if (decodedByServer || gz.body === null) return (await gz.json()) as ComplexPayload;

  const unzipped = gz.body.pipeThrough(new DecompressionStream("gzip"));
  return (await new Response(unzipped).json()) as ComplexPayload;
}

/** lat/lng 를 지도 미리보기의 % 좌표로 투영한다. VWorld 연동 시 교체 지점. */
export function projectToMap(lat: number, lng: number): { x: number; y: number } {
  const x = ((lng - SEOUL_BOUNDS.minLng) / (SEOUL_BOUNDS.maxLng - SEOUL_BOUNDS.minLng)) * 100;
  const y = (1 - (lat - SEOUL_BOUNDS.minLat) / (SEOUL_BOUNDS.maxLat - SEOUL_BOUNDS.minLat)) * 100;
  return {
    x: Math.max(4, Math.min(96, x)),
    y: Math.max(12, Math.min(94, y)),
  };
}
