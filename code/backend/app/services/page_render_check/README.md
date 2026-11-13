# page_render_check：生成页发布前无头浏览器渲染检查

在 HTML 清洗完成后、上传发布之前，用独立受限子进程拉起无头 Chromium + Playwright 对页面做一次与线上完全同构的沙箱渲染自检，捕获**未处理脚本异常（`js_error`）**、**首屏白屏（`blank_page`）**与**主线程死循环无响应（`hang`）**。

## 核心设计原则

1. **绝不阻断页面发布**：
   - 浏览器路径缺失、并发等待队列满（`PAGE_RENDER_CHECK_MAX_QUEUE`）、子进程冷启动超时或意外崩溃，一律平滑返回 `status="skipped"` 并照常发布。
   - `repair` 档位下发现问题时最多触发 **1 次**带报错行号与堆栈的模型修复（优先用 `SEARCH/REPLACE` 局部编辑块）并复检一次；若复检仍有问题，照常发布并在结果卡标注「可能有显示问题」（`render_issue`）。
2. **线上同款沙箱 CSP 与外链隔离**：
   - 通过 Playwright `context.route("**/*", ...)` 拦截主文档 `http://starpage.invalid/`，返回和线上 `/p/` 网关一致的 `build_page_csp()` 响应头（不带 `allow-same-origin`，`connect-src 'none'`）。
   - 仅放行 `GENERATED_PAGE_CDN_ALLOWLIST` 白名单内的外部 CDN 主机，其余外链一律立即 `route.abort()`，既还原真实安全限制又防止慢外链拖满检查时间。
3. **一次性子进程隔离与零残留回收**：
   - 父进程通过 `subprocess.Popen(..., start_new_session=True)` 启动独立子进程，使用 `os.pidfd_open` + `loop.add_reader` 在事件循环零线程等待退出，再以 `os.wait4` 采集峰值内存 `ru_maxrss`。
   - Playwright 内部会为 Chromium 再建独立进程组，因此父进程把每次检查的 `TMPDIR` 指向私有目录 `/tmp/sp-render-ipc-*`，退出/超时/取消时扫描 `/proc` 匹配该目录令牌，将关联进程组全部 `SIGKILL` 并清理临时目录，保证 0 僵尸进程、0 `/tmp` 泄漏。
   - **严禁在 Chromium 子进程内设 `RLIMIT_AS`**：V8 / Chromium 启动即预留数十 GB `PROT_NONE` 虚拟地址空间，设 `RLIMIT_AS` 会直接启动崩溃；内存由 `--js-flags=--max-old-space-size=256`、15 秒硬超时强杀与 worker 单元的 `MemoryMax=3G` 兜底。
4. **防误报白屏与跨境 CDN 慢加载容错**：
   - 白屏判定在页面内遍历可见文本节点包围盒与非零尺寸 `svg/canvas/img/video`，同时识别 `display:none`、`visibility:hidden`、祖先链累积 `opacity < 0.02`、零高 `overflow:hidden` 容器剪裁，并兼容 `position:fixed/absolute` 全屏幻灯片（`body` 文档流高度为 0 但画面正常可见）。
   - 当白名单 CDN 请求偶发缓慢导致 `goto(wait_until="load")` 超时时，先探测主线程能否在 1.2 秒内正常执行 `page.evaluate(...)`；主线程存活且首屏可见则不误判为死循环 `hang`。

## 模块结构

| 文件 | 职责 |
| --- | --- |
| `types.py` | `RenderIssue`、`RenderCheckResult` 数据结构；落库 JSONB（`to_dict`）、前端提示（`to_render_issue` / `render_issue_from_stored`）与修复轮提示词（`format_for_repair_prompt`）转换 |
| `runner.py` | 父进程异步调度器：Chromium 路径探测、并发信号量（默认 2）与排队上限熔断（默认 8）、`pidfd + wait4` 回收、进程树清扫、运行指标 `render_check_stats()` |
| `child.py` | 一次性检查子进程入口：Playwright 路由拦截、CSP 注入、未捕获异常收集、首屏白屏评估与时间预算分段 |

## 三档开关与配置项

- `PAGE_RENDER_CHECK_MODE`：
  - `off`（代码默认）：不运行无头浏览器检查。
  - `observe`（灰度观察档）：正常执行渲染检查并将结果写入 `page_versions.render_check`（标记 `"mode": "observe", "expose_issue": false`），**不触发模型修复、不向前端下发 `render_issue` 标签**。用于评估线上误报率，切到 `repair` 后历史 `observe` 记录也不会被回溯打标。
  - `repair`（自动修复档）：检测到问题自动让模型修复 1 次并复检；复检仍失败时照常发布，并在 `completed` 事件和会话详情中下发 `render_issue`。复检问题数没有减少时退回修前页面发布（`render_check` 记 `repair_applied: false, repair_reverted: true`）；修复轮上游报错或用户中途停止，同样发布修前页面。
  - 停止请求：渲染检查（含复检）期间页面已完整生成，用户点停止只中断检查（`status: skipped, skip_reason: cancelled`），页面照常发布并按成功结算。
- `PAGE_RENDER_CHECK_CONCURRENCY`：单 worker 同时运行的渲染检查子进程上限（默认 `2`，4 核双 worker 机器上勿盲目调大）。
- `PAGE_RENDER_CHECK_TIMEOUT_SECONDS`：单次检查硬超时秒数（默认 `15.0`）。
- `PAGE_RENDER_CHECK_MAX_QUEUE`：单 worker 最大排队等待数，超限直接 `skipped(queue_full)`（默认 `8`）。
- `PAGE_RENDER_CHECK_SETTLE_MS`：`load` 完成后额外观察入场动画与异步脚本报错的毫秒数（默认 `1300`）。
- `PAGE_RENDER_CHECK_CHROMIUM_PATH`：显式指定 Chromium 可执行文件路径；未配置或设为 `auto` 时自动探测 `/root/.cache/ms-playwright/chromium-*/chrome-linux64/chrome` 及系统浏览器。
