// 用服务器上的 Playwright Chromium 给三套方案截图自查，并实测胶片条滚轮横滑。
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

const ports = { a: 3011, b: 3012, c: 3013 };
const shareSelector = { a: ".tb-btn.is-share", b: ".tb-tile.is-share", c: ".tb-chip.is-share" };
const only = process.env.ONLY ? process.env.ONLY.split(",") : Object.keys(ports);
const tag = `${width}x${height}`;

const browser = await chromium.launch({ args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader"] });
const context = await browser.newContext({ viewport: { width, height }, permissions: ["clipboard-read", "clipboard-write"] });
const page = await context.newPage();

async function open(url) {
  await page.goto(url, { waitUntil: "load" });
  await page.waitForSelector(".vb-results", { timeout: 60_000 });
  await page.waitForTimeout(3500);
}

for (const variant of only) {
  const base = `http://127.0.0.1:${ports[variant]}`;
  await open(`${base}/?demo=9`);

  const stage = await page.$(".vb-stage-view");
  const box = stage && (await stage.boundingBox());
  if (box) await page.mouse.move(box.x + box.width / 2, box.y + box.height * 0.8);
  await page.waitForTimeout(500);
  await page.screenshot({ path: path.join(outDir, `${variant}-stage-${tag}.png`) });
  if (process.env.STAGE_ONLY) continue;

  if (variant === "b") {
    await page.keyboard.press("ArrowRight");
    await page.waitForTimeout(700);
    await page.screenshot({ path: path.join(outDir, `b-switch-flash-${tag}.png`) });
    await page.keyboard.press("ArrowLeft");
    await page.waitForTimeout(400);
  }

  const film = await page.$(".vb-film");
  const filmBox = film && (await film.boundingBox());
  if (filmBox) {
    await page.mouse.move(filmBox.x + filmBox.width / 2, filmBox.y + filmBox.height / 2);
    for (let i = 0; i < 4; i += 1) {
      await page.mouse.wheel(0, 120);
      await page.waitForTimeout(60);
    }
    await page.waitForTimeout(700);
    const scrollLeft = await page.$eval(".vb-film", (el) => Math.round(el.scrollLeft));
    const max = await page.$eval(".vb-film", (el) => el.scrollWidth - el.clientWidth);
    console.log(`${variant}: 滚轮 4 格后胶片条 scrollLeft=${scrollLeft} / 最大 ${max}`);
    await page.screenshot({ path: path.join(outDir, `${variant}-film-wheel-${tag}.png`) });
    await page.$eval(".vb-film", (el) => el.scrollTo({ left: 0 }));
  }

  await page.click(shareSelector[variant]);
  await page.waitForSelector(".share-dialog.is-ready", { timeout: 10_000 });
  await page.waitForTimeout(1800);
  await page.screenshot({ path: path.join(outDir, `${variant}-share-${tag}.png`) });
  await page.click(".share-copy");
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(outDir, `${variant}-share-copied-${tag}.png`) });
  await page.keyboard.press("Escape");
  await page.waitForTimeout(300);

  console.log(`${variant}: 完成`);
}

if (!process.env.ONLY && !process.env.STAGE_ONLY) {
  await open(`http://127.0.0.1:3011/?demo=2`);
  await page.screenshot({ path: path.join(outDir, `two-models-${tag}.png`) });
  await page.click(".link-icon-button.is-share");
  await page.waitForSelector(".share-dialog.is-ready", { timeout: 10_000 });
  await page.waitForTimeout(1800);
  await page.screenshot({ path: path.join(outDir, `two-models-share-${tag}.png`) });
}

await browser.close();
