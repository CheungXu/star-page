# 首页「文档转网页」v1.1 升级预览

## 1. 版本定义

- **v1.0（当前线上生产版本）**：
  - 基于 Chat-first 的文本 Prompt 生成体系；
  - 上传文档作为辅助入口，位于左下角紧凑小按钮；
  - 部署运行在端口 `3000`（前端 Next.js 服务）与 `8000`（后端 FastAPI 服务）。
- **v1.1（本次升级版本）**：
  - 核心心智转型为 **Document-to-Web（文档转网页）**；
  - 首页主卡片顶部置入专属高质感文档投放主舱（高度 74px）；
  - 输入框收缩为单行紧凑微调条（高度 44px），兼顾无文档时直接敲字与有文档时输入补充要求；
  - 100% 保持 v1.0 的原版侧边栏、标题、灵感 Chips、用户信息与底层设计令牌。

---

## 2. 预览运行方式

当前运行在独立端口 `3001`：

```bash
cd script/preview-doc-upload/frontend
npx next dev -H 0.0.0.0 -p 3001
```

- 访问地址：`http://localhost:3001/`；
- 所有 `/api` 请求通过 `next.config.mjs` 原生反向代理至 `http://127.0.0.1:8000`，读取真实后端数据；
- 生产环境（3000 与 8000）完全独立运行，不受任何影响。

---

## 3. 合并到生产代码（`code/frontend/`）的变更清单

确认 v1.1 体验无误后，只需将两处改动直接迁回 `code/frontend/` 即可平滑上线：

1. **`app/page.tsx`**：
   - 在 `renderPromptForm`（非 compact 首屏）中，在 `textarea` 上方加入 `.hero-dropzone` 节点：
     - 空态渲染 Attachment 图标盒、主标题“上传文档，一键转为网页”、副文本“支持 Word · Excel · PDF · PPT”与“选择文件”按钮；
     - 挂载态就地展示已选文件胶囊（文件名、大小、删除与重新选择）。
   - 将首屏 `textarea` 的占位符改为随是否已有文档自适应切换。
   - 移除首屏工具栏左下角的原 `composer-upload` 按钮（因已升格至上方大舱，工作区 compact 续写态仍保留）。
2. **`app/globals.css`**：
   - 将 `.hero-prompt textarea` 的 `min-height` 设为 `44px`；
   - 新增 `.hero-dropzone`、`.hero-dropzone-empty`、`.hero-dropzone-icon` 等相关样式。
