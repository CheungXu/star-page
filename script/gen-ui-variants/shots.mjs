// 用服务器上的 Playwright Chromium 给三套方案截图自查。
// 用法：node script/gen-ui-variants/shots.mjs [输出目录] [宽x高]
import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";
import path from "node:path";

const require = createRequire(import.meta.url);
const corePath = process.env.PLAYWRIGHT_CORE || "/root/.npm/_npx/e41f203b7505f1fb/node_modules/playwright-core";
const { chromium } = require(corePath);

const outDir = process.argv[2] || "/tmp/gen-ui-shots";
const [width, height] = (process.argv[3] || "1600x900").split("x").map(Number);
mkdirSync(outDir, { recursive: true });

const ports = { a: 3011, b: 3012, c: 3013 };
const cases = (process.env.CASES || "7@6,7@12,7@40,2@8,2@40,1@8").split(",");
const only = process.env.ONLY ? process.env.ONLY.split(",") : Object.keys(ports);

const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || undefined });
const page = await browser.newPage({ viewport: { width, height } });
for (const variant of only) {
  for (const item of cases) {
    const [count, at] = item.split("@");
    const url = `http://127.0.0.1:${ports[variant]}/?demo=${count}&at=${at}`;
    await page.goto(url, { waitUntil: "networkidle" });
    await page.waitForTimeout(1200);
    const file = path.join(outDir, `${variant}-n${count}-t${at}.png`);
    await page.screenshot({ path: file });
    console.log(file);
  }
}
await browser.close();
