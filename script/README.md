# script

脚本目录，存放临时支持任务的脚本。

## 逐个重启后端 web 实例

`restart_web.sh` 依次重启所有正在运行的 `star-page-web@<端口>`（现在是 8000、8001、8002）：重启一个、等它健康检查通过、再等过 Nginx 的 `fail_timeout`（5 秒），才动下一个，对外服务不中断。不会重启生成 worker。

```bash
script/restart_web.sh          # 默认：所有在跑的 web 实例，按端口从小到大
script/restart_web.sh 8001     # 只重启一个
```

不要手动连着重启两个实例：前一个刚恢复的 5 秒内 Nginx 仍把它当作不可用，第二个一停就可能全部不可用。2026-11-12 上线时间隔 4 秒手动重启过一次，场景页的服务端渲染因此返回了约 1.5 秒的 404。

## 逐个重启生成 worker

`restart_worker.sh` 依次重启所有正在运行的 `star-page-worker@<编号>`（现在是 1、2）。每个 worker 收到停止信号后先排空：不领新任务，等手上的生成跑完再退出（最多 6 分钟），这期间其他 worker 照常领新任务；等它重新启动完成（日志出现 `[worker] 已启动`）再重启下一个。脚本会把每个 worker 是「已排空」还是「排空超时」打出来；超时的任务交给其他 worker 从头重跑一次。

```bash
script/restart_worker.sh       # 默认：所有在跑的 worker
script/restart_worker.sh 2     # 只重启一个
```

手上有长任务时一个 worker 要等几分钟，`systemctl restart` 会一直阻塞到它退出，这是正常的。

## 数据库连接测试

`check_postgres_connection.sh` 用于从当前服务器验证 PostgreSQL/RDS 连接。

使用前准备：

1. 复制 `config/db.env.example` 为 `config/db.env`。
2. 在 `config/db.env` 中填写真实 RDS 连接信息。
3. 确认 `config/db.env` 已被 `config/.gitignore` 忽略。

执行：

```bash
bash script/check_postgres_connection.sh
```

脚本会优先使用 `DATABASE_URL`。如果没有填写 `DATABASE_URL`，则使用 `PGHOST`、`PGPORT`、`PGDATABASE`、`PGUSER`、`PGPASSWORD`。

## RDS 业务库授权准备

`prepare_rds_database.sql` 用于在应用账号缺少建表权限时，由高权限数据库账号先创建业务库并授予应用账号建表权限。

当前 MVP 后端迁移需要应用账号能在业务数据库的 `public` schema 下创建表：

```sql
GRANT USAGE, CREATE ON SCHEMA public TO stars_page_demo;
```

建议业务表放在独立数据库 `stars_page`，不要长期放在默认 `postgres` 数据库中。

## Logo 透明化处理

### 完整版（带扫描伪影清理）：`process_logo.py`

`process_logo.py` 把 `image/stars-page-logo.png`（原始带白底）处理成透明背景 + 自动裁剪白边的版本，输出到：

- `image/stars-page-logo-transparent.png`：仓库内归档版本
- `code/frontend/public/stars-page-logo.png`：前端实际加载的版本（直接覆盖原文件）

处理步骤：

1. **清理近白色伪影**：阈值 235，把 R/G/B 三通道最小值都 ≥ 235 的像素一律置为纯白，消除原图边缘那条很浅的灰色伪影线（实测原图最右侧有一列 RGB≈228 的扫描伪影线，否则会撑大裁剪边界）。
2. **GIMP 风格 color-to-alpha**：参考色为纯白，按比例反推前景纯色 + alpha，确保抗锯齿边缘是带 alpha 的纯蓝色（视觉上无白雾、无锯齿）。
3. **自动裁剪**：依据 alpha 通道找出非透明像素的 bounding box（阈值 32 避免半透明伪影撑大边界），保留 16px 内边距。

使用：

```bash
pip install Pillow numpy --break-system-packages
python3 script/process_logo.py
```

执行后需要重新 `npm run build && systemctl restart star-page-frontend.service`，因为 systemd 服务用的是 `.next/standalone/public/` 中复制后的版本。

⚠️ 这是一次性的预处理脚本，源 PNG 一旦换新设计稿需要重新运行一次。如果将来获取到 SVG 矢量原图，建议直接用 SVG 替换 PNG，不再需要此脚本。

### 轻量版（纯白底无伪影）：`preview-logo/process_simple_logo.py`

针对**白底干净、无扫描伪影**的源图，约 30 行代码完成"透明化 + 自动裁剪"，无需 color-to-alpha：

- 单参数亮度阈值法：lum ≥ 245 → alpha 0；lum ≤ 220 → alpha 255；中间线性过渡。
- 基于 alpha 通道 `getbbox()` 自动裁剪 + 16px padding。

输出 `script/preview-logo/stars-page-logo-simple.png`，作为生产首页中央 Hero logo（54px）+ 侧边栏底板内 logo（26px）使用。两个版本互不冲突。

## 多端口 Logo 方案对比预览：`preview-logo/`

`preview-logo/` 目录是一套独立的"前端方案 A/B/C 对比预览"基础设施：

| 文件 | 用途 |
|---|---|
| `preview-a.html` | 方案一：简化并缩小（44px / 56px 单星） |
| `preview-b.html` | 方案二：中央不放 Logo |
| `preview-c.html` | 方案三：原 Logo 转化为氛围水印 |
| `_styles.css` | 三方案公用的精简样式（从生产 globals.css 抽取首屏所需片段） |
| `serve.py` | 多端口 Python `http.server`，同时绑定 3001 / 3002 / 3003 |
| `process_simple_logo.py` | 简化版 logo 透明化脚本 |
| `stars-page-logo-simple.png` | 处理后的简化版 logo |

启动：

```bash
cd script/preview-logo
python3 serve.py        # 同时占用 3001/3002/3003
# 浏览器分别打开 http://localhost:3001/ /3002/ /3003/ 对比

# 后台常驻
nohup python3 serve.py > /tmp/preview-logo.log 2>&1 &

# 停止
pkill -f preview-logo/serve.py
```

⚠️ 仅作视觉对比用，无后端调用、无真实交互。方案选定后需要把"通用优化"（padding / 阴影 / 字号等非方案差异项）双向同步到生产 `globals.css` 与本目录的 `_styles.css`。

跨项目可复用方法见 `wiki/multi-port-static-preview-for-design-variants.md`。

## LLM 模型连通性探测：`probe-llm-models.py`

对 `config/llm.models.json` 中每个模型发最小 chat 请求，并拉取百炼/方舟 models API 列出同系列 ID，用于上线前验证与排查「产品未开通 / model ID 写错」。

```bash
python3 script/probe-llm-models.py
```

加 `--capabilities` 改为实测每个模型能否读图（问一张纯色图的颜色）以及哪种写法能关闭思考（按候选写法依次试，思考量为零才算），结果用来填 `llm.models.json` 的 `supports_vision` 和 `extraction_extra_body`。可用 `--only=key` 只测一个模型：

```bash
python3 script/probe-llm-models.py --capabilities --only=doubao
```

探测结果直接输出到终端。换代时的判断顺序见 `wiki/llm-provider-abstraction.md` 的「模型换代检查清单」。2026-10 的结果见 `doc/20261010/llm-model-catalog-update.md`，2026-11 的结果见 `doc/20261110/llm-model-catalog-update.md`。

## 多端口衔接动画方案对比预览：`preview-transition/`

`preview-transition/` 把上面的「多端口静态对比」模式扩展到**交互动画 / 多状态过渡**，
用于「首页 ↔ 生成页」衔接动画选型。与 `preview-logo/` 的区别：不再各写一份静态 HTML，
而是公共骨架 `app.js` 用与生产一致的 className 重建 hero / workspace 两态 DOM + 状态机，
顶部「导演控制条」反复重播「生成 / 返回 / 历史进入」三种切换并带「模拟 reduced-motion」
勾选；各端口的 `variant-*.js` 只注入各自的 `transition()` 实现。

| 文件 | 用途 |
|---|---|
| `app.js` | 公共骨架：重建两态 DOM + 导演控制条 + 状态机，委托 `variant-*.js` 注入过渡 |
| `variant-css.js` | 方案一：纯 CSS 交叉过渡（3001） |
| `variant-vt.js` | 方案二：原生 View Transitions API（3002） |
| `variant-motion.js` | 方案三：motion 库命令式 FLIP / 编排（3003） |
| `director.css` | 原型专用样式（导演条 + 舞台，`sp-` 命名空间） |
| `globals.css` | 从生产复制，保证视觉 1:1（临时双维护，选定即弃） |
| `demo-page.html` | 完成态预览 iframe 里的占位结果页 |
| `serve.py` | 三端口静态服务（3001/3002/3003） |

```bash
cd script/preview-transition
python3 serve.py        # 占用 3001/3002/3003，勿与 preview-logo 同时启动
pkill -f preview-transition/serve.py
```

**最终选型**：仅方案三（motion）落地生产，加载失败 / reduced-motion 时兜底直接切换；
曾评估的方案二（View Transitions）/ 方案一（纯 CSS）因维护成本放弃，完整三级版留档在
git 分支 `full-animation-mode`。决策见 `wiki/frontend-home-workspace-transition.md` 与
`doc/20260529/frontend-transition-animation-plan.md`。原型三套实现保留作选型留档。

## 预览工具栏方案对比（已归档）

结果预览工具栏与分享改版时用的多端口对比脚本 `preview-toolbar-variants/`（`serve.sh` 起 3011 / 3013 / 3014，`shots.mjs` Playwright 自查）已随方案选定从主线删除，完整保留在 git 标签 `alt/preview-toolbar-v1`。需要重新并排对比时按 `alternatives/preview-toolbar/README.md` 的「怎么复现」操作。

## 小红书公开热词

`growth/fetch_xhs_keywords.py` 拉取小红书当前公开热词，结果写到 `data/growth/xhs/`。热词榜不需要账号。同一版榜单不会重复写入。

按关键词搜公开笔记需要 `config/dataxapi.env` 里的 `DATAXAPI_API_KEY`（模板见 `config/dataxapi.env.example`）。每个词约 0.19 元，失败不收费。已成功的词会跳过。

```bash
python3 script/growth/fetch_xhs_keywords.py
python3 script/growth/fetch_xhs_keywords.py --search
```

`growth/fetch_xhs_login_search.py` 用本机已扫码的小红书登录态，按收藏量再搜一页。服务需先在本机 18060 端口启动。已成功的「关键词 + 排序」会跳过。

```bash
python3 script/growth/fetch_xhs_login_search.py
```

## 小红书文案封面图生成

`growth/generate_xhs_cover.py` 调用火山方舟 Seedream 5.0 生图模型（`doubao-seedream-5-0-pro-260628`），按提示词生成 3:4 竖图封面并下载保存。密钥从 `config/llm.env` 读取。

```bash
python3 script/growth/generate_xhs_cover.py "<英文提示词>" "<文件名.png>"
```

## 场景案例生成与挑选

`scene-showcase/` 用于给首页「别人做的页面」准备案例，只在测试环境使用。

1. `generate_variants.py`：四个场景各用多个模型并行生成一份候选页，结果写入 JSON。已成功的场景会跳过，可以断点续跑。`--models` 指定模型，默认通义 Max、豆包 Pro、GLM-5.3、Kimi K3。
2. `shoot_variants.py`：把候选页截成手机和桌面两张图，并按场景拼成对比图，供人工挑选。

挑中后调用 `POST /api/publications` 发布固定链接，再用 `POST /api/admin/scenes/showcases` 设为案例。

```bash
cd code/backend && . .venv/bin/activate
python ../../script/scene-showcase/generate_variants.py --token-file /tmp/sp-test-session.txt
python ../../script/scene-showcase/shoot_variants.py --variants /tmp/scene-audit/variants.json
```

## 预览库同步首页案例

分支预览用独立库 `stars_page_preview`（建库只跑迁移，没有业务数据），首页「别人做的页面」会是空的。`sync_preview_showcases.py` 从生产库只读复制 `scene_showcases` 及其页面链路（用户、会话、批次、页面、版本），按 id 幂等，可重复执行。作者账号不复制手机号、邮箱和密码；目标库名必须以 `_preview` 结尾。页面 HTML 在共享 OSS，复制后即可打开。

```bash
set -a && . config/db.env && set +a
code/backend/.venv/bin/python script/sync_preview_showcases.py
```

## 宣传物料包

`pack_promo_kits.py` 把五套图文打成推广人可直接发的压缩包，输出到 `data/文案/202610/发包/`。视频不放进去。压缩包已 gitignore。

```bash
python3 script/pack_promo_kits.py
```

## 宣传素材：真实界面截图

`promo-capture/` 截取线上 https://stars-page.com 的真实界面，输出到 `data/文案/202610/00-界面截图/`。都用后端虚拟环境里的 Playwright 和服务器缓存的 Chromium。

1. `shoot_site.py`：首页、场景页、四个案例页，桌面 1440×900@2x、手机 390×844@3x。已存在的图片会跳过。
2. `shoot_generation.py`：用全新匿名身份真实跑一次「网页简历」，截填写、生成中、一版完成、并排对比四个阶段。每次运行都会消耗一次匿名免费额度，并留下一条线上生成记录，约 4～5 分钟。
3. `compose_xhs_images.py`：把截图拼成小红书 3:4 配图，只用真实截图，不往图上加字。

```bash
cd code/backend && . .venv/bin/activate
python ../../script/promo-capture/shoot_site.py
python ../../script/promo-capture/shoot_generation.py
python ../../script/promo-capture/compose_xhs_images.py
```

注意：

- 服务器要装 `fonts-noto-color-emoji`，否则场景按钮里的 emoji 会截成方块。
- 屏幕外的 iframe 不跑入场动画，整页截图需先把视口拉到整页高度，脚本已处理。
- 截图里的坐标按 CSS 像素算，不要按缩小后的预览图量。

## 重跑卡住的生成（推荐）

生成 worker 每 5 秒检查一次续期过期（45 秒没续期）的 `running` 任务：最近活动在 120 分钟内、还没自动重跑过的从头重跑一次，其余标失败（见 `code/backend/README.md` 的「后台生成」）。`requeue_stuck_generations.py` 用来把更早卡住的批次重新交给这个检查：给指定批次里没完成的任务各写一条 `requeued` 事件，把最近活动刷成现在。

```bash
code/backend/.venv/bin/python script/requeue_stuck_generations.py --batch f8433bce --batch c3522807 --dry-run   # 先预演
code/backend/.venv/bin/python script/requeue_stuck_generations.py --batch f8433bce --batch c3522807
# 不用重启 worker：几秒内就会被接手重跑。卡在 pending 的任务本来就会被认领。
```

批次 id 可写前 8 位。已经自动重跑过一次的 `running` 任务会被提示，扫尾仍会把它标失败。重跑会再消耗一次模型 token。

## 合并历史思考碎片

`compact_generation_events.py` 把已经结束、并且结束时间早于 10 分钟的任务收短。连续的思考记录合成一行，文字按原顺序保留。输出过程中连着记的「正在输出」进度收成最后一行，带上最后一次的 token 数；思考完成、输出完成、上传完成这些节点因为状态不同，各自保留。默认只打印会删掉多少行，加上 `--apply` 才改库，改完做一次 `VACUUM (ANALYZE)`，不锁死整张表。还在跑、或刚结束不到 10 分钟的任务不动。

```bash
code/backend/.venv/bin/python script/compact_generation_events.py
code/backend/.venv/bin/python script/compact_generation_events.py --apply
```

## 在脚本进程里补跑（备用）

`resume_stuck_generations.py` 在脚本进程里直接调用模型，把某个批次里仍是 `running`、还没写完页面的任务再跑一遍，结果写回原来的任务和页面。只在后端没法接手时用，不要对后端正在跑的任务用。

```bash
code/backend/.venv/bin/python script/resume_stuck_generations.py --batch-id <批次 uuid>
```

已成功的任务会跳过。某一路失败会自动再试一次。

## 宣传视频：hyperframes

`promo-video/` 配合 [hyperframes](https://github.com/heygen-com/hyperframes)（HTML 写画面、GSAP 动画、无头 Chrome 逐帧渲染）做宣传视频。工程在 `data/视频/202610/星页一分钟讲解/`。

1. `build_voice.py`：读工程里的 `src/script.json`，逐句调用百炼 `qwen3-tts-flash` 合成中文配音（默认音色 Cherry），拼成 `assets/voice.wav`，写出每句实际时长 `src/timings.json`。已合成的句子会跳过。
2. `build_index.py`：把时长填进 `src/index.html.tpl`，生成 `index.html`。每段画面从这段第一句配音开始，字幕逐句出现。

```bash
cd code/backend && . .venv/bin/activate
python ../../script/promo-video/build_voice.py --project "data/视频/202610/星页一分钟讲解"
cd ../.. && python3 script/promo-video/build_index.py --project "data/视频/202610/星页一分钟讲解"
cd "data/视频/202610/星页一分钟讲解"
npx --yes hyperframes@0.8.150 lint
npx --yes hyperframes@0.8.150 snapshot --at 3,14,24,34,47,55 --no-end
npx --yes hyperframes@0.8.150 render --quality looks --output renders/星页一分钟讲解.mp4
```

注意：

- 改台词：删掉 `assets/voice-parts/` 里对应的 wav 再跑 `build_voice.py`。只改画面：改 `src/index.html.tpl` 后重跑 `build_index.py`。不要直接改 `index.html`，下次生成会覆盖。
- 模板必须放在 `src/` 里。工程根目录出现第二个带 `data-composition-id` 的 html，lint 会报 `multiple_root_compositions`。
- 当前服务器只有 2 核、3GB 内存，hyperframes 自动走低内存模式，单进程逐帧截图。`snapshot` 每帧都重新加载页面，约 1 分钟一帧。`check` 跑 10 分钟也没结束，只用 `lint` 加 `snapshot` 检查。
- 不登录 HeyGen 就没有它的曲库，当前视频没有背景音乐。
