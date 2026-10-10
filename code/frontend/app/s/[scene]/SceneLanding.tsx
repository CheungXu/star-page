"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { track } from "../../lib/analytics";

type SceneCase = {
  showcase_id: string;
  page_id: string;
  title: string;
  summary: string | null;
  page_url: string;
  remix_prompt: string;
};

export type SceneDetail = {
  key: string;
  name: string;
  emoji: string;
  priority: string;
  tagline: string;
  description: string;
  seo_title?: string;
  seo_description?: string;
  prompt_template: string;
  accepts_documents: boolean;
  cases: SceneCase[];
};

export function SceneLanding({
  sceneKey,
  initialScene = null,
}: {
  sceneKey: string;
  initialScene?: SceneDetail | null;
}) {
  const [scene, setScene] = useState<SceneDetail | null>(initialScene);
  const [missing, setMissing] = useState(false);
  const [utm, setUtm] = useState("");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const source = params.get("utm_source") || "";
    setUtm(source);
    track("scene_landing_view", { scene_key: sceneKey, utm_source: source });
    let cancelled = false;
    void (async () => {
      try {
        const response = await fetch(`/api/scenes/${encodeURIComponent(sceneKey)}`);
        if (!response.ok) {
          if (!cancelled && !initialScene) setMissing(true);
          return;
        }
        const data = (await response.json()) as SceneDetail;
        if (!cancelled) setScene(data);
      } catch {
        if (!cancelled && !initialScene) setMissing(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sceneKey]);

  const startHref = `/?scene=${encodeURIComponent(sceneKey)}${utm ? `&utm_source=${encodeURIComponent(utm)}` : ""}`;

  return (
    <main className="scene-landing">
      <div className="hero-aurora" aria-hidden="true">
        <span className="aurora-blob aurora-blob-1" />
        <span className="aurora-blob aurora-blob-2" />
        <span className="aurora-blob aurora-blob-3" />
        <span className="aurora-grid" />
      </div>
      <header className="scene-landing-bar">
        <Link href="/">星页 StarPage</Link>
        <nav>
          <Link href="/s/resume" aria-current={sceneKey === "resume" ? "page" : undefined}>简历</Link>
          <Link href="/s/creator-home" aria-current={sceneKey === "creator-home" ? "page" : undefined}>主页</Link>
          <Link href="/s/landing" aria-current={sceneKey === "landing" ? "page" : undefined}>落地页</Link>
          <Link href="/s/invitation" aria-current={sceneKey === "invitation" ? "page" : undefined}>邀请函</Link>
        </nav>
      </header>
      {missing && (
        <section className="scene-landing-hero">
          <h1>没有这个场景</h1>
          <p>回到首页，直接描述你想要的页面。</p>
          <Link className="scene-landing-primary" href="/">回首页</Link>
        </section>
      )}
      {!missing && !scene && <p className="scene-landing-loading">正在打开场景…</p>}
      {scene && (
        <>
          <section className="scene-landing-hero">
            <h1>
              <span aria-hidden="true">{scene.emoji} </span>
              {scene.name}
            </h1>
            <p className="scene-landing-tagline">{scene.tagline}</p>
            <p>{scene.description}</p>
            <div className="scene-landing-actions">
              <Link className="scene-landing-primary" href={startHref}>
                {scene.accepts_documents ? "上传资料，开始做" : "开始做"}
              </Link>
              <Link className="scene-landing-secondary" href={`/?scene=${encodeURIComponent(scene.key)}`}>先填资料</Link>
            </div>
          </section>
          <section className="scene-landing-cases" aria-label="案例">
            <h2>案例</h2>
            {scene.cases.length === 0 && (
              <p className="scene-landing-empty">这个场景还没有公开案例。你做好并发布之后，别人可以从这里打开，或一键做同款。</p>
            )}
            <div className="scene-case-grid">
              {scene.cases.map((item) => (
                <article className="scene-case-card" key={item.showcase_id}>
                  <a className="scene-case-frame" href={item.page_url} target="_blank" rel="noreferrer" aria-label={`打开${item.title}`}>
                    <iframe title={item.title} src={item.page_url} sandbox="allow-scripts" tabIndex={-1} />
                  </a>
                  <h3>{item.title}</h3>
                  {item.summary && <p>{item.summary}</p>}
                  <div className="scene-case-actions">
                    <a href={item.page_url} target="_blank" rel="noreferrer">打开</a>
                    <Link
                      href={`/?scene=${encodeURIComponent(scene.key)}&remix=${encodeURIComponent(item.page_id)}${utm ? `&utm_source=${encodeURIComponent(utm)}` : ""}`}
                      onClick={() => track("scene_remix_click", { scene_key: scene.key, page_id: item.page_id })}
                    >
                      做同款
                    </Link>
                  </div>
                </article>
              ))}
            </div>
          </section>
        </>
      )}
    </main>
  );
}
