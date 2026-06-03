import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: "星页 StarPage · 一句话生成可分享的网页",
  description: "星页 StarPage —— 用一句话或一份文档，把你的想法变成一个可分享的精致网页。",
  applicationName: "星页 StarPage",
  icons: {
    icon: "/stars-page-logo-simple.png",
    shortcut: "/stars-page-logo-simple.png",
    apple: "/stars-page-logo-simple.png",
  },
  keywords: ["星页", "StarPage", "AI 网页生成", "一句话生成网页", "HTML 落地页", "可分享网页"],
  openGraph: {
    title: "星页 StarPage · 一句话生成可分享的网页",
    description: "用一句话或一份文档，把你的想法变成一个可分享的精致网页。",
    siteName: "星页 StarPage",
    type: "website",
  },
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
