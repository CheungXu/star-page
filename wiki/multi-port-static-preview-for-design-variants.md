# 多端口静态预览：前端方案 A/B/C 对比模式

设计师 / 产品经理 / 老板让你"把三种方案都做一下、我来选"时，最常见的两种错误做法：

1. 复制三份 Next.js / Vite 项目，每份改一处代码 → 维护噩梦、build 慢、CSS / 资源会重复编译。
2. 用同一份代码 + URL 查询参数 (`/?v=A`) 切换 → 不能并排对比、容易看走眼、不直观。

本条沉淀一套**真正轻量的多端口静态预览模式**：每个方案一个 self-contained HTML，用单进程 Python `http.server` 同时绑定多个端口、每个端口默认入口指向对应方案，浏览器并排打开三个 tab 即可对比。

适用于"视觉方案对比"（颜色、Logo、布局、间距、阴影），不适合需要真实后端调用的功能对比。

## 目录结构

```
script/preview-logo/                  # 视具体场景命名
├── _styles.css                       # 公用精简样式（从生产 globals.css 抽取首屏所需片段）
├── preview-a.html                    # 方案 A：self-contained HTML
├── preview-b.html                    # 方案 B
├── preview-c.html                    # 方案 C
├── stars-page-logo-simple.png        # 静态资源（按需）
├── process_simple_logo.py            # 一次性预处理脚本（按需）
└── serve.py                          # 多端口服务入口
```

每个 `preview-*.html` 都引用同一份 `_styles.css`，只在 `<style>` 标签内覆盖少量"方案特化样式"。这样能**在保证视觉基础一致的前提下，让差异点凸显出来**，对比公平。

## 核心：多端口 Python 服务

```python
"""三端口静态预览服务

- 3001 -> 方案一
- 3002 -> 方案二
- 3003 -> 方案三

每个端口独立 http.server，根路径 / 默认返回对应方案 HTML，
其它静态资源（_styles.css / *.png）按相对路径访问。
"""
from __future__ import annotations

import http.server
import socketserver
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent

PORT_MAPPING = {
    3001: ("preview-a.html", "方案一"),
    3002: ("preview-b.html", "方案二"),
    3003: ("preview-c.html", "方案三"),
}


def make_handler(default_file: str):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(ROOT), **kwargs)

        def do_GET(self):  # noqa: N802
            if self.path in ("/", ""):
                self.path = "/" + default_file
            return super().do_GET()

        def log_message(self, format, *args):  # noqa: A002
            sys.stdout.write(
                f"[端口 {self.server.server_address[1]}] {self.address_string()} - {format % args}\n"
            )
            sys.stdout.flush()

    return Handler


class ReusableThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve_port(port: int, default_file: str, label: str) -> None:
    handler = make_handler(default_file)
    try:
        with ReusableThreadingTCPServer(("0.0.0.0", port), handler) as httpd:
            print(f"[启动] http://0.0.0.0:{port}/  ->  {default_file}  ({label})")
            sys.stdout.flush()
            httpd.serve_forever()
    except OSError as exc:
        print(f"[错误] 端口 {port} 启动失败：{exc}")


def main() -> None:
    threads = [
        threading.Thread(
            target=serve_port,
            args=(port, default_file, label),
            daemon=True,
            name=f"preview-{port}",
        )
        for port, (default_file, label) in PORT_MAPPING.items()
    ]
    for t in threads:
        t.start()

    print("=" * 60)
    print("多端口预览服务已启动，按 Ctrl+C 停止")
    print("=" * 60)
    sys.stdout.flush()

    try:
        while True:
            for t in threads:
                t.join(timeout=1)
    except KeyboardInterrupt:
        print("\n[停止] 收到中断信号，退出预览服务")


if __name__ == "__main__":
    main()
```

## 关键设计点

### 1. ThreadingTCPServer + 多线程同时绑定

`http.server.HTTPServer` 默认是单线程阻塞，必须用 `ThreadingTCPServer` 配合 `daemon_threads = True` 才能同时支持多个端口同时响应。每个端口一个线程，互不阻塞。

`allow_reuse_address = True` 让重启服务时能立即复用端口（避免 `Address already in use` 等待 TIME_WAIT 超时）。

### 2. 每个端口独立的默认入口

通过 `make_handler(default_file)` 闭包给每个端口生成不同的 `Handler`，重写 `do_GET`：当请求是 `/` 或空路径时，重写 `self.path` 为对应方案的 HTML。这样：

- `http://localhost:3001/` → 方案一
- `http://localhost:3001/_styles.css` → 公用样式（按相对路径正常访问）
- `http://localhost:3001/stars-page-logo-simple.png` → 资源

无需为每个方案建立独立子目录、无需复制资源文件。

### 3. 顶部"方案标签"胶囊

每个 HTML 顶部加一个固定胶囊式标签，明确标注当前是"方案一/二/三"。避免对比时打开多个 tab 后看不出哪个是哪个：

```html
<div class="variant-tag" aria-label="当前预览">
  <span class="variant-dot"></span>
  方案一 · 简化并缩小
</div>
```

```css
.variant-tag {
  position: fixed;
  top: 16px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 5;
  padding: 6px 14px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.85);
  backdrop-filter: blur(8px);
  font-size: 12px;
  font-weight: 600;
  box-shadow: var(--shadow-sm);
}
```

## 与生产首页的同步

每个方案选定后，需要把"通用优化"（不属于方案差异、属于全局精修，如 padding、阴影、字号微调）**同时同步到 `_styles.css` 和生产 `globals.css`**，让其它两个未选中的方案也享受改进。否则下一次再做对比时基线会"过时"。

实务做法：每次精修迭代后，在 commit message 里明确标注 `[preview]` 或 `[both]` 区分仅预览还是双向同步。

## 启动 / 停止

```bash
cd script/preview-logo
python3 serve.py    # 前台运行，所有日志统一输出

# 后台常驻
nohup python3 serve.py > /tmp/preview.log 2>&1 &

# 停止
pkill -f preview-logo/serve.py
```

不需要 Node.js / Next.js / 任何构建步骤，仅依赖 Python 3 标准库（无第三方库）。

## 适用边界

| 场景 | 是否适用 |
|---|---|
| Logo / 配色 / 字体 / 间距视觉对比 | ✅ |
| Hero 区 / Landing Page / 海报视觉对比 | ✅ |
| 表单交互、状态切换、键盘快捷键测试 | ❌（无 React 状态、无后端） |
| API 调用、登录态、SSE 事件流 | ❌（用真实开发环境对比） |
| 动效复杂度（多状态过渡） | ✅ 见下「交互动画对比变体」：公共状态机 + 命令式 motion ESM 也能并排对比 |

如果方案对比涉及到状态切换或后端联动，应该用 Storybook / Ladle 或在真实项目里加 feature flag。本模式的最大优势是**零依赖、零编译、零构建**，5 分钟从想法到上线对比页。

## 进阶：交互动画对比变体（带状态机 + 导演控制条）

本模式不止能比「静态视觉」，加一层公共状态机后也能并排比**交互动画 / 多状态过渡**。
本仓库 `script/preview-transition/` 就用它对比了三套「首页 ↔ 生成页」衔接动画
（纯 CSS / View Transitions / motion），扩展点有三个：

1. **公共骨架 `app.js`**：用与生产一致的 className 重建两态 DOM（hero / workspace），
   并维护一个状态机；各方案不再各写一份 HTML，而是共用骨架。
2. **导演控制条**（仅原型用）：顶部放几个按钮触发「生成 / 返回 / 历史进入」等切换，
   可**反复重播**同一种过渡来回看细节；再加一个「模拟 reduced-motion」勾选框，不改系统
   设置就能验证无障碍降级。
3. **`variant-*.js` 注入 `transition()`**：骨架把「怎么过渡」开放给每个端口的 variant
   注入，`SPApp.init({ transition })`。三套方案共享 DOM 与状态机，只换过渡实现，对比公平。

关键：让原型里 motion 的写法（命令式 ESM `animate` DOM）与最终生产集成保持**同一种
范式**，原型结论才能直接落地，而不只是「视觉目标」。详见
`frontend-home-workspace-transition.md`。

## 进阶二：Next.js 真实组件多端口并行对比（同一 Worktree + 独立构建目录 + 确定性 Demo 参数）

当对比方案涉及复杂 React 状态、Context 联动、拖拽分隔条、多轮历史回看时（如 `doc/20261109/generation-progress-compare-variants.md` 的生成工作区三套方案 A/B/C），纯静态 HTML 不够用，而复制三份 Next.js 目录又会导致维护和编译灾难。此时可用**同一份代码 + 环境变量切插槽 + 独立 `distDir` + URL 确定性模拟参数**在三个端口同时跑 `next dev`：

1. **独立构建目录防互踩**：在 `next.config.mjs` 中配置 `distDir: process.env.NEXT_DIST_DIR || ".next"`，启动脚本分别以：
   - `NEXT_PUBLIC_UX_VARIANT=A NEXT_DIST_DIR=.next-a npx next dev -p 3011`
   - `NEXT_PUBLIC_UX_VARIANT=B NEXT_DIST_DIR=.next-b npx next dev -p 3012`
   - `NEXT_PUBLIC_UX_VARIANT=C NEXT_DIST_DIR=.next-c npx next dev -p 3013`
   启动三个进程，共享源码但各自写独立的 `.next-*` 缓存。
2. **Git Worktree 下 `node_modules` 的 Turbopack 陷阱**：Next.js 16（Turbopack）安全检查会拒绝指向项目根目录外的符号链接（`ln -s` 会报错 `Symlink node_modules is invalid, it points out of the filesystem root`）。用硬链接拷贝 `cp -al /path/to/main/code/frontend/node_modules ./node_modules`，秒级完成、零额外磁盘占用，且能通过 Turbopack 校验。
3. **确定性 Demo URL 参数（不调后端、不扣费、可定点截图）**：支持 `?demo=7&rounds=3&at=12`：
   - `demo=N`：模拟 N 个模型并行（含完成、进行中、失败态）；
   - `rounds=R`：自动生成前 `R-1` 轮历史归档与第 `R` 轮当前轮；
   - `at=S`：将模拟时钟冻结在第 `S` 秒，方便 Playwright 在不同视口（如 `1600×900` 宽屏与 `1366×640` 矮屏）下对同一时刻定点截图对比。
4. **Turbopack 开发期中文字符边界 Panic 防范**：当多个 `next dev` 同时监听同一份源码时，若分多次编辑让 JSX 处于半闭合语法错误状态且错误行含中文字符，Turbopack 打印源码片段可能因字节切片落在中文字符中间而触发 Rust panic 退出。编辑大型 JSX 应一次完整替换，并用启停脚本管理进程。
5. **选定方案后的上线收口清单**：
   - 删除未选方案组件、方案切换角标、demo 模拟模块与演示静态页；
   - 移除 `next.config.mjs` 的临时 `NEXT_DIST_DIR` 并停止对比端口进程；
   - 将模拟字段（如 `simulatedElapsedMs`）替换为真实后端字段（如 `durationMs`），用真实账号会话跑通端到端验收；
   - 提交前执行 `git checkout code/frontend/tsconfig.json` 还原 `next dev` 自动改写的配置。

## 进阶三：选定后的收口与备选方案库

多轮对比之后通常只上线一套，其它的要「留得住、找得回」：

1. **先打标签再清理**：在删除对比工具（方案角标、演示数据、多端口脚本、临时 `distDir`）之前，给包含全部方案的版本打带说明的 git 标签（如 `alt/preview-toolbar-v1`），以后 `git worktree add ... <tag>` 就能原样复现多端口对比。
2. **主线只留有 AB 计划的方案**：确定要做 AB 的备选保留在代码里、用一个构建 / 运行开关切换（共享组件照常接后端）；细节变体、已淘汰方案只留在标签里，避免长期维护负担。
3. **写进备选方案库**：本仓库 `alternatives/{项目}/README.md` 记录线上方案与理由、每个备选的做法 / 优缺点 / 截图、代码位置（主线开关 + 标签）、复现命令和 AB 指标建议；方案状态变化时补一行结论。

## 坑：同一文件混用编辑器工具和外部脚本写盘

在 Cursor 里，同一个文件既用编辑器的替换工具改、又用外部脚本（如 Python 读写）直接写盘时，编辑器缓存里的旧版本有可能在之后被写回，悄悄抹掉脚本写入的改动（本仓库一次 CSS 改动因此回退并被提交）。做法：同一文件固定用一种方式修改；提交前用 `grep` 逐项核对关键改动仍在文件里；用脚本按「起止标记」截取替换时，结束标记要选整段唯一的行，避免截在半行里留下残缺规则。

## 相关条目

- `frontend-design-tokens-and-prompt-card.md`：精修后的设计 token 和模式可以反向同步到本预览基础设施。
- `frontend-home-workspace-transition.md`：用本模式选型的「首页 ↔ 工作区衔接过渡」落地案例（含降级层级取舍）。
- `png-logo-transparent-and-trim.md`：方案对比常涉及 logo 处理，可与本模式串联使用。
