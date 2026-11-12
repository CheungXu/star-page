# 后端进程模型

## 背景与线上方案

原来后端只有一个 FastAPI 进程：接请求、推事件流、调用模型、排队名额全在里面。只能用一个核，一次同步计算会卡住所有人，每次发版都会打断在途生成。

**线上方案：方案一，多个生成 worker + 多个 web 实例，用 PostgreSQL `LISTEN/NOTIFY` 通信。** 2026-11-12 上线时 worker 只能开一个；2026-11-13 把排队名额搬进数据库、任务加续期、worker 退出前排空，改成可以多开。

- `star-page-web@8000`、`@8001`、`@8002`：`APP_ROLE=web`，只接请求，Nginx `upstream` 在它们之间转发，可逐个重启（`script/restart_web.sh`）。
- `star-page-worker@1`、`@2`：`APP_ROLE=worker`，`python -m app.worker`。名额按库里在跑的任务数算，任务记着归属和续期时间，续期超时才被别的 worker 接手；重启时先跑完手上的任务（`script/restart_worker.sh`）。
- 三个频道：派发（含「空出名额」）、停止、批次有新事件。通知丢了靠 worker 每 5 秒扫库、web 每 2 秒轮询兜底。

选它的原因：只用现有的 RDS，不新增服务和费用；排队名额、文件解析、启动扫尾这些代码几乎原样保留，改动集中在派发、停止和唤醒三处；一台机器上已经能用满多个核，发版改接口时不打断生成。

做法与细节见 `code/backend/README.md`「后台生成」、`wiki/sse-background-job-streaming.md` 第 2.5、2.6 节、上线记录 `doc/20261112/backend-web-worker-split.md` 和 `doc/20261113/multi-worker-and-share-page-speedup.md`。

## 备选方案

### 方案二：Redis 队列 + 多机 worker

- **做法**：
  - 任务派发和排队名额搬到 Redis，例如 `arq` 或 Redis Stream 消费组。个人和全站名额用 Lua 原子计数，带过期时间，防止进程死后名额不还。
  - 模型吐出的思考和进度写进 Redis Stream，设几小时过期；数据库只写开始、阶段切换和结束。事件流用 `XREAD BLOCK` 读，`Last-Event-ID` 直接对应 Stream 的 id。
  - 停止用 `SET cancel:{task_id}` 加发布订阅；同批次的资料解析用 `SET NX EX` 做锁。
  - worker 可以开多个、放在多台机器上，web 无状态，不需要会话保持。
- **优点**：生成能力可以横向加机器；worker 之间不共享内存，单个 worker 重启只影响它手上的任务；事件写入不再压在 RDS 上。
- **缺点**：多一个要运维、要付费的组件（阿里云 Redis 1G 主从每月几十元，或本机容器）；开发约 2 到 3 天（名额入库、续期、排空已经在方案一里做了，主要是换队列和事件存储）。
- **当时为什么没选**：同时生成的上限是 48，CPU 大部分时间在等模型；事件写入已经按任务合并，RDS 压力很小。方案一用现有组件就解决了多核、发版中断和卡顿；11-13 起 worker 也能多开、多机，原来「需要多机」「重启打断在途生成」两条触发条件已经不成立。
- **什么时候做**：出现以下任一情况时——
  - 生成事件写库成为 RDS 的主要负载（`[metrics]` 里 worker 的 `db_out` 长期偏高，或 RDS 写 IOPS 吃紧）；
  - 同时在跑的生成要远超现在的 48（几百路），按库计数的认领锁开始排队；
  - 需要优先级队列、延迟任务等数据库表不好做的调度功能。

### 方案三：函数计算跑重型任务

- **做法**：web 和普通生成仍按方案一或方案二运行。重型的资料解析，以及以后的多文件工程构建，投递到阿里云函数计算，按次计费，每个任务独占 CPU 和内存。
- **优点**：单个任务把内存撑爆也只死一个函数实例，不连累主站和其他用户；不用为高峰常备大机器。
- **缺点**：冷启动和打包依赖（PDF、Office 解析库）有额外工作；调试和日志分散；按调用计费，量大时要算账。
- **当时为什么没选**：现在生成是流式调用模型、输出单个 HTML，解析已有大小和页数上限，放在 worker 的线程里足够。
- **什么时候做**：生成页演进到多文件工程、需要在服务端构建；或大文件解析经常占满 worker 的内存，需要按任务隔离。

## 代码在哪

- 方案一的代码就在主线：`code/backend/app/services/generation_bus.py`、`generation_runner.py`、`app/worker.py`，以及 `APP_ROLE` 配置。
- `APP_ROLE=all`（默认）仍是拆分前的单进程形态，回滚时启用 `code/systemd/star-page-backend.service.example` 即可，步骤见 `code/systemd/README.md`「回滚到单进程」。
- 方案二、方案三没有写代码，不打标签。

## 怎么复现

方案一在本机三进程演练（连测试库，页面存本地，不碰线上）：

```bash
docker run -d --rm --name sp-test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=stars_page_test -p 127.0.0.1:55432:5432 postgres:16-alpine
cd code/backend
export DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/stars_page_test OBJECT_STORAGE_PROVIDER=local LOCAL_STORAGE_DIR=/tmp/sp-pages
.venv/bin/python -m app.db.migrate
APP_ROLE=worker .venv/bin/python -m app.worker &
APP_ROLE=worker .venv/bin/python -m app.worker &   # 第二个 worker：只有一个会做启动扫尾，名额两边共用
APP_ROLE=web .venv/bin/uvicorn app.main:app --port 8095 &
APP_ROLE=web .venv/bin/uvicorn app.main:app --port 8096 &
```

需要真实生成时再加载模型密钥。从 8095 提交、在 8096 订阅 `/api/generations/batches/{id}/events`，可以看到跨进程派发和唤醒。

## AB 测试建议

这是基础设施选型，不做用户侧 AB。决定是否升级到方案二时看这几项：worker 的 CPU 和内存占用；同时在跑的生成数与排队时长；worker 重启时被打断的任务数；RDS 连接数和写入量。

## 状态

| 日期 | 结论 |
| --- | --- |
| 2026-11-12 | 上线方案一；方案二、方案三记录在案，按上面的触发条件再评估 |
| 2026-11-13 | 复查方案一并修补，见 `doc/20261113/backend-review-fixes.md` |
| 2026-11-13 | 方案一升级为多 worker：名额入库、任务续期与接手、退出前排空、批次解析跨 worker 抢占；线上开 2 个 worker、3 个 web。方案二的触发条件改为看事件写库负载和并发规模，见 `doc/20261113/multi-worker-and-share-page-speedup.md` |
