# ICP 备案号首页展示实施记录

## 背景

`stars-page.com` ICP 备案已通过（粤ICP备2026071100号）。按工信部要求，网站需在首页底部显著位置展示备案号，并链接至 `https://beian.miit.gov.cn`。

## 设计取舍

- **仅 idle 首屏展示**：合规口径指向「网站首页」；工作区是登录后的应用操作台，不展示，避免干扰三栏布局与底部 compact 输入框。
- **静态文档流页脚，不用 fixed**：避免压住 Hero 下方灵感 chips、切断 `hero-wrap` 品牌辉光、与工作区输入区重叠。
- **视觉克制**：11px、`--color-text-muted`，hover 才略加深；不加背景条/边框。

## 实现

| 文件 | 说明 |
| --- | --- |
| `code/frontend/app/site-config.ts` | 备案号与链接集中配置 |
| `code/frontend/app/components/SiteFooter.tsx` | 首页页脚组件 |
| `code/frontend/app/page.tsx` | idle 的 `.page-shell` 内 Hero 下方挂载 |
| `code/frontend/app/globals.css` | `.page-shell` flex 列 + `margin-top: auto` 沉底 |

## 上线

```bash
cd code/frontend && npm run build
systemctl restart star-page-frontend.service
```

验证：首页 HTML 含备案号；`/_next/static/*.css` 返回 `200 text/css`。

## 后续

- 若新增公安备案号，在 `site-config.ts` 同一行追加即可。
- 用户生成的 `/p/...` 页面无需加主站备案号。
