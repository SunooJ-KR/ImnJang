import type { NextConfig } from "next";

// 런타임 서버가 없다. 정적 export 결과(out/)를 Vercel이 그대로 서빙한다.
const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  trailingSlash: true,
};

export default nextConfig;
