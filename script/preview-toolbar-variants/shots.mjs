// 用服务器上的 Playwright Chromium 给各方案截图自查，并实测胶片条滚轮横滑和分享浮层。
// 用法：node script/preview-toolbar-variants/shots.mjs [输出目录] [宽x高]
import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";
import path from "node:path";

const require = createRequire(import.meta.url);
const corePath = process.env.PLAYWRIGHT_CORE || "/root/.npm/_npx/e41f203b7505f1fb/node_modules/playwright-core";
const { chromium } = require(corePath);

const outDir = process.argv[2] || "/tmp/preview-toolbar-shots";
const [width, height] = (process.argv[3] || "1600x900").split("x").map(Number);
mkdirSync(outDir, { recursive: true });

const ports = { a: 3011, c1: 3013, c2: 3014 };
const shareSelector = { a: ".tb-btn.is-share", c1: ".tb-act.is-share", c2: ".tb-act.is-share" };
const only = process.env.ONLY ? process.env.ONLY.split(",") : Object.keys(ports);
const tag = `${width}x${height}`;
const shot = (name, options = {}) => page.screenshot({ path: path.join(outDir, `${name}-${tag}.png`), timeout: 90_000, ...options });

const browser = await chromium.launch({ args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader"] });
const context = await browser.newContext({ viewport: { width, height }, permissions: ["clipboard-read", "clipboard-write"] });
const page = await context.newPage();

async function open(url) {
  await page.goto(url, { waitUntil: "load" });
  await page.waitForSelector(".vb-results", { timeout: 60_000 });
  await page.waitForTimeout(3500);
}

async function openShare(selector) {
  await page.$eval(selector, (el) => el.click());
  await page.waitForSelector(".share-pop.is-ready", { timeout: 15_000 });
  await page.waitForTimeout(1200);
}

for (const variant of only) {
  await open(`http://127.0.0.1:${ports[variant]}/?demo=9`);
  await shot(`${variant}-stage`);
  if (process.env.STAGE_ONLY) continue;

  if (variant === "a") {
    await page.hover(".tb-address");
    await page.waitForTimeout(400);
    await shot("a-chrome-hover", { clip: { x: 0, y: 0, width, height: 160 } });
  }

  const film = await page.$(".vb-film");
  const box = film && (await film.boundingBox());
  if (box) {
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    for (let i = 0; i < 4; i += 1) {
      await page.mouse.wheel(0, 120);
      await page.waitForTimeout(60);
    }
    await page.waitForTimeout(800);
    const scrollLeft = await page.$eval(".vb-film", (el) => Math.round(el.scrollLeft));
    console.log(`${variant}: 滚轮 4 格后胶片条 scrollLeft=${scrollLeft}`);
    await page.$eval(".vb-film", (el) => el.scrollTo({ left: 0 }));
    await page.waitForTimeout(300);
  }

  await openShare(shareSelector[variant]);
  await page.$eval(".share-pop-copy", (el) => el.click());
  await page.waitForTimeout(300);
  await shot(`${variant}-share`);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(300);
  console.log(`${variant}: 完成，浮层剩余 ${await page.$$eval(".share-pop", (els) => els.length)} 个`);
}

if (!process.env.ONLY && !process.env.STAGE_ONLY) {
  await open("http://127.0.0.1:3011/?demo=2");
  await shot("two-models");
  await openShare(".link-icon-button.is-share");
  await shot("two-models-share");
}

await browser.close();
