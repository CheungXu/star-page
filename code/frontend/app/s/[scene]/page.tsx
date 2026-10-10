import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { SceneLanding, type SceneDetail } from "./SceneLanding";

type Props = { params: Promise<{ scene: string }> };

async function loadScene(key: string): Promise<SceneDetail | null> {
  const base = process.env.BACKEND_INTERNAL_URL ?? "http://127.0.0.1:8000";
  try {
    const response = await fetch(`${base}/api/scenes/${encodeURIComponent(key)}`, { cache: "no-store" });
    if (!response.ok) return null;
    return (await response.json()) as SceneDetail;
  } catch {
    return null;
  }
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { scene } = await params;
  const data = await loadScene(scene);
  const title = data?.seo_title || "场景 · 星页 StarPage";
  const description = data?.seo_description || "用星页把一份资料变成可分享的网页。";
  return {
    title,
    description,
    openGraph: { title, description, type: "website" },
  };
}

export default async function ScenePage({ params }: Props) {
  const { scene } = await params;
  const data = await loadScene(scene);
  if (!data) notFound();
  return <SceneLanding sceneKey={scene} initialScene={data} />;
}
