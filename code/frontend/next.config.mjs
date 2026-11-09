const backendUrl = process.env.BACKEND_INTERNAL_URL ?? "http://127.0.0.1:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  // 方案对比时同一份代码起多个 dev 服务，每个服务用独立的构建目录。
  distDir: process.env.NEXT_DIST_DIR || ".next",
  // 仅开发环境生效：允许通过 127.0.0.1 / 局域网 IP 访问 dev server 的 HMR 等资源，生产构建无影响。
  allowedDevOrigins: ["127.0.0.1", "localhost", "0.0.0.0", "8.138.118.232"],
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${backendUrl}/api/:path*` },
      { source: "/p/:path*", destination: `${backendUrl}/p/:path*` },
      { source: "/u/:path*", destination: `${backendUrl}/u/:path*` },
      { source: "/assets/:path*", destination: `${backendUrl}/assets/:path*` },
    ];
  },
};

export default nextConfig;
