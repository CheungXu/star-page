# 生成页 JS 沙箱与安全（隔离优先）

面向"用 LLM 生成可分享展示页"这类产品：当页面从"纯静态 HTML"升级到"可跑 CSS/JS"时，如何在不牺牲安全的前提下放开交互。核心结论一句话——**不要试图清洗用户 JS，而是把每个生成页当成互联网上一个不受信任的网站，用浏览器原生隔离把它关进笼子**。

## 为什么不能靠"清洗 JS"或"模型/prompt 控制"兜底

- 攻击者**间接控制**生成结果：通过越狱 prompt、以及藏在**上传文件**里的指令（间接 prompt 注入），可诱导模型吐出任意 JS。
- LLM 输出**不是信任边界**：模型会幻觉、照搬训练数据、跟随被注入的指令。"模型生成的"≠"可信的"。
- prompt/模型控制、内容审核属于**检测（detection）**，应作为附加层；安全的**隔离（containment）层不能依赖模型表现**。

## 两道独立的墙（缺一不可）

1. `sandbox`（**不含** `allow-same-origin`）→ 页面进入**不透明 origin**，读不到主站 Cookie/localStorage。关掉"偷主站登录态"。
2. `connect-src 'none'` + `form-action 'none'` → 关掉"钓鱼/信标/把平台域名当分发渠道"。

关键认知：**sandbox 不限制 `fetch`**。即使进了不透明 origin，页面仍能 `fetch` 任意外部地址做钓鱼/信标/扫内网。这类滥用只能由 `connect-src` / `form-action` / `img-src` 拦，sandbox 管不到——所以"是否禁网"与"是否隔离 origin"是两件独立的事。

## 落地方式（无需新域名）

- 顶层直开的分享链接没有父 iframe，因此沙箱必须由**响应头**施加：`Content-Security-Policy: sandbox allow-scripts ...`。这样即便和主站同域，顶层文档也被强制塞进不透明 origin。
- 主站内预览再叠加 iframe 的 `sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox"` 属性。
- 本仓库统一 CSP（`code/backend/app/api/routes_pages.py:_build_page_csp`）：

```text
sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox;
default-src 'none';
script-src 'unsafe-inline' <可信CDN>;
style-src  'unsafe-inline' <可信CDN>;
img-src https: data:; font-src https: data:; media-src https: data:;
connect-src 'none'; form-action 'none'; base-uri 'none';
frame-ancestors 'self'
```

- 因进入不透明 origin，`'self'` 不再匹配任何东西，故脚本/样式用 `'unsafe-inline'` + 显式可信 CDN host（`GENERATED_PAGE_CDN_ALLOWLIST`，默认 jsdelivr/unpkg）。
- 预览缩放逻辑不读 `iframe.contentDocument`（按容器尺寸反算），所以加 `sandbox` 跨域不影响缩放。

## HTML 清洗的定位转变

从"删光 JS"转为"展示安全白名单"（`code/backend/app/services/html_sanitizer.py`）：

- 放行：内联 `<script>`、`on*` 事件属性、`<form>/<input>`（提交由 CSP `form-action 'none'` 拦）、canvas、svg。
- 移除：`<iframe>/<object>/<embed>/<base>`、`meta http-equiv=refresh`；外链 `<script src>` 仅留可信 CDN，其余丢弃。
- 清洗是纵深防御，真正的边界是 CSP+sandbox。

## 外部网络策略：默认禁网 + 未来 opt-in

- 展示页（替代 PPT/Word）数据写死在页面内，**不需要** live fetch；需要 live fetch 的恰是"变成应用"的场景。
- 因此默认 `connect-src 'none'`，靠可信 CDN 白名单满足图表/动画库等美观需求。
- 若将来确有 live 数据需求，做成**按页 opt-in + 指定域名白名单（scoped `connect-src`，不是 `*`）+ 审核**，而非默认全开。

## 适用边界与后续

- 适用：纯静态、客户端 JS 的展示页；OSS+CDN 托管。
- 更彻底的隔离（多文件 codebase、规模化后）：迁到**独立内容域名** `*.usercontent`（独立注册域，对标 `githubusercontent.com`），顶层文档不再仅靠 sandbox 头；公开页走 CDN、私有页签名/边缘鉴权。
- 配套合规：内容审核、举报入口、`pages.status=suspended` 下架。
