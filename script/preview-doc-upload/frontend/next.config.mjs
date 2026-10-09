const backendUrl = process.env.BACKEND_INTERNAL_URL ?? "http://127.0.0.1:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  // 仅开发环境生效：允许通过 127.0.0.1 / 局域网 IP 访问 dev server 的 HMR 等资源，生产构建无影响。
  allowedDevOrigins: ["127.0.0.1", "localhost", "0.0.0.0"],
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${backendUrl}/api/:path*` },
      { source: "/p/:path*", destination: `${backendUrl}/p/:path*` },
    ];
  },
};

export default nextConfig;
