# 网站 ICP 备案号展示（首页页脚）

面向中国大陆合规站点：首页底部展示备案号，并链接至工信部系统 `https://beian.miit.gov.cn`。

## 合规要点

- 展示位置：**网站首页**底部显著位置（行业惯例为页脚）。
- 文案：与备案号完全一致，例如 `粤ICP备2026071100号`。
- 必须可点击，且指向 `https://beian.miit.gov.cn`。
- 用户 UGC 子页面、独立 App 内页规则不同；主站合规通常只要求主站首页（App 则放在设置/关于）。

## 不影响美观的放置策略

| 方案 | 适用 | 风险 |
| --- | --- | --- |
| `position: fixed` 全站底栏 | 传统内容站 | 易挡 Hero 下方引导、工作区底部输入、切断背景光晕 |
| 侧边栏底部一行 | 有持久侧栏的应用 | 侧栏收起时不够「显著」，且易被当成账号区附属信息 |
| **idle 首页静态页脚 + flex 沉底** | SPA 首页 + 工作区分离 | 推荐：合规覆盖首页，工作区零干扰 |

推荐布局：

```text
.home-shell .page-shell {
  display: flex;
  flex-direction: column;
  min-height: calc(100vh - 垂直 padding);
}
.site-footer { margin-top: auto; }
```

页脚放在 Hero 内容之后、仍在 `.page-shell` 内，用 `margin-top: auto` 自然落到视口底部，无需 fixed。

## 视觉

- 字号 11–12px，用最弱文本色 token（如 `--color-text-muted`）。
- 默认近乎隐形，hover/focus-visible 才略增强；不加背景条。
- 备案号写入独立配置文件，便于后续追加公安备案号。

## 本仓库落地

- 配置：`code/frontend/app/site-config.ts`
- 组件：`code/frontend/app/components/SiteFooter.tsx`
- 挂载：仅 `page.tsx` idle 首屏
- 实施记录：`doc/20260610/icp-filing-footer-implementation.md`
