# Python 异步服务中重度文档/媒体解析的子进程隔离范式

在基于 `asyncio`（FastAPI / Worker）的 Python 服务端处理用户上传的 PDF、Office 文档、大表格或图片时，**`asyncio.to_thread` 只适合轻 I/O 或毫秒级 C 调用，绝不能用来承载重度第三方解析**。本文沉淀将重度解析安全隔离到受限子进程的通用做法与避坑要点。

## 1. 为什么 `asyncio.to_thread` 兜不住重度解析

1. **抢占 GIL 拖死同进程实时流与心跳**：`pdfplumber`、`openpyxl`/`python-docx`、`BeautifulSoup` 等包含大量纯 Python 循环，在线程里跑仍长期持有 GIL。实测解析一份 5 万行×100 列表格会让同进程事件循环出现 **1.2 秒以上**的停顿（`loop_lag_max_ms > 1200ms`），足以拖慢 SSE 流式推送，甚至让分布式 Worker 错过续期心跳而被误判失联、触发重复执行。
2. **`wait_for(to_thread(...))` 超时杀不掉线程**：Python 线程没有强行终止原语。`wait_for` 抛 `TimeoutError` 后，底层线程仍在继续吃满 CPU 和内存；若服务优雅退出（drain）等待解析归零，一个卡住的解析会把排空一直拖到硬超时。
3. **glibc 内存碎片导致常驻进程 RSS 只涨不降**：解析器瞬间创建数百万个小 Python 对象（字符框、单元格、XML 节点），函数返回后 CPython arena / glibc heap 往往不归还给操作系统，常驻 Worker 跑几次大文档后 RSS 就永久膨胀数百 MB。
4. **单份坏文件 OOM 连累同进程所有在途任务**：线程与父进程共享地址空间，一旦触发内核 OOM Killer，整个 Worker 连同手上几十路正常的流式长任务一起被杀。

## 2. 全链路三道防线

### 第一道（Web 接入层）：零整份读入 + 流式轻预检

- **先看长度再碰流**：框架（如 Starlette `UploadFile`）在解析 `multipart/form-data` 时会将超过 1MB 的文件自动溢写到磁盘临时文件（`SpooledTemporaryFile`）并记录 `size`。必须先检查 `UploadFile.size` 拦截超限请求，**严禁一上来 `await upload.read()` 把 50MB 临时文件重新读回 Python 堆内存**。
- **预检与对象存储直连文件流**：`zipfile.ZipFile`、`pypdfium2.PdfDocument`、`python-calamine` 以及 `oss2.Bucket.put_object` 均原生支持文件对象（`BinaryIO`），且关闭文档对象后不会关闭底层临时文件，`seek(0)` 后即可直接流式上传到对象存储。
- **XML 预检用 `iterparse` 边读边清**：统计 Office 文档规模（如 DOCX 段落/表格上限）不要调 `docx.Document()` 构建整棵 DOM（实测 1000 图文档仅预检就会让 Web 进程永久涨 110MB+）。改用 `zipfile.open("word/document.xml")` + `ET.iterparse(..., events=("start", "end"))` 只统计 `w:body` 的直接子节点，每个子节点闭合时同时 `elem.clear()` 并清空父节点子列表，超限立即抛 422 中止，内存增量接近 0。

### 第二道（Worker 执行层）：单次一子进程 + `rlimit` + `pidfd` + `wait4`

每次解析由父进程 `asyncio.Semaphore` 限流后拉起一个一次性子进程（`python -m ...child`），输入原件和输出结果都走私有临时目录中的文件，父进程内存不持有原件字节：

1. **资源硬围栏（子进程入口首行设置）**：
   - `os.nice(10)`：降低调度优先级，让同机 Web / Worker 事件循环优先拿到 CPU。
   - `resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, hard))`：限制虚拟地址空间（如 1.5GB）。注意在 64 位 Linux 上 `RLIMIT_AS` 限制的是 `VmSize`（含动态库 mmap），上线前需实测正常解析的 `VmPeak`（Python + pdfium + calamine 约 120MB，设 1.5GB 有 10 倍以上余量）。超限时分配器立即返回 `ENOMEM`（Python 捕获 `MemoryError` 或以约定退出码退出），转成对用户可读的「文件过大或内容过于复杂」，父进程毫发无损。
2. **无阻塞等待 + 精确采集子进程峰值 RSS**：
   - 不要把大输出塞进 `stdout/stderr` 管道（C 库打印大量告警超过 64KB 管道缓冲且父进程未及时读时会死锁），统一把日志重定向到临时目录下的 `stderr.log`，结果用 `pickle` 原子写盘（`.tmp` -> `replace`）。
   - 在 Linux 5.3+ / Python 3.9+ 上，用 `subprocess.Popen(..., start_new_session=True)` 启动子进程后，打开 `pidfd = os.pidfd_open(proc.pid, 0)` 并挂到 `loop.add_reader(pidfd, ...)` 等待可读，既不占线程，也不会像 `asyncio.create_subprocess_exec` 默认 watcher 那样提前把子进程 `waitpid` 收走；随后调用 `os.wait4(proc.pid, 0)` 一步拿到退出码与内核统计的真实峰值内存 `rusage.ru_maxrss`（Linux 下单位 KB）。注意回收后同步写回 `proc.returncode`，避免 `Popen.__del__` 报 `ResourceWarning`。
   - **判定与计数细节**：先判定 `exitcode == 0` 且结果文件完整再做 `stderr` 关键字（如 `MemoryError`、`Cannot allocate memory`）的 OOM 归因，避免 C 库无关告警误伤成功解析；`asyncio.Semaphore.acquire()` 前后的排队计数必须用 `try ... finally` 成对维护，防止排队中被取消导致指标泄漏。
3. **超时与取消秒杀进程组**：
   - 捕获 `TimeoutError` 或 `asyncio.CancelledError`（用户停止、Worker 排空超时退出）时，立即 `os.killpg(proc.pid, signal.SIGKILL)` 再 `os.wait4` 回收，耗时 < 5ms，不留僵尸进程、不拖住发版排空。
   - 一次性子进程每次有 0.5～1 秒导入解析库的固定开销，相比秒级到数十秒的文档解析完全可接受；且退出即由内核 100% 回收全部物理页，彻底根治内存碎片，无需过早引入常驻子进程池。

### 第三道（分布式协调与宿主层）：防「毒文件」连环崩溃 + cgroup 封顶

在多 Worker 失联自动接手（lease 过期重领）的架构下，子进程隔离之外还必须补上**尝试次数熔断**：

- 若某份畸形「毒文件」触发内核级崩溃或整组被杀，导致 Worker 1 失联，45 秒后 Worker 2 会按续期超时规则接手同一份文件重跑，可能引发全集群轮流崩溃重启。
- **做法**：在批次/任务表记录 `parse_attempts`（默认 0，向后兼容滚动发版）。每次认领解析时原子 `+1`；当 `parse_attempts >= 2` 且租约再次过期时，后续 Worker 不再拉起第 3 次解析，直接在行锁下标记失败、清理暂存原件并退回预扣额度。注意三点细节：
  1. **区分「崩溃失联」与「发版主动交接」**：Worker 收到 `SIGTERM` 优雅退出交出未完解析时，在限定 `status == running` 的条件下清空续期并把 `parse_attempts` 减回 1（`GREATEST(parse_attempts - 1, 0)`），避免日常滚动发版误消耗重试额度。
  2. **检查第 2 次是否仍在跑**：同批并行任务去轮询解析状态时，看到 `parse_attempts == 2` 但 `heartbeat_at` 仍在有效期内，说明第 2 次尝试正在正常执行，必须继续等待（`busy`），只有租约也过期了才能判为连续崩溃失败。
  3. **提交成功后与取消隔离**：数据库提交成功（`committed = True`）之后的原件清理阶段若收到 `CancelledError`，绝不能再删除已入库产物或回退状态，只屏蔽完成原件清理后正常返回。
- **systemd cgroup 最后兜底**：在服务单元同时配置 `MemoryHigh`（内核开始积极回收页缓存）与 `MemoryMax`（硬上限，包含该 Worker 及其子进程所在 cgroup 总量），确保任何极端泄漏最多只重启当前实例，绝不把无 swap 的宿主机打死。

## 3. 解析器内部高频内存陷阱清单

即便进了子进程，也要避免无谓触碰 `RLIMIT_AS` 上限：

- **PDF 双库协作**：不要对同一份字节反复打开多次 `PdfDocument`；`pdfplumber` 每处理完一页必须在 `finally` 里同时调 `page.flush_cache()` 与 `page.get_textmap.cache_clear()`，否则整本 PDF 的字符布局树会一直攒到最后。
- **Excel 大表**：不要调 `sheet.to_python()` 一次性物化数百万单元格再切片过滤（会产生 3～4 份全量拷贝，500 万格峰值近 600MB）。改用 `sheet.iter_rows()` 按行流式迭代，小表缓冲整表输出，超过阈值后切入流式列累加器（数值列用 `array('d')` 存紧凑 C double，日期列只记首尾极值，样本行用定长 `deque`），峰值可压低且速度翻倍。
- **多图文档与图片重编码**：遍历文档时一旦有效去重配图达到上限（如 20 张）就跳过后续图片提取；内存里的 `PIL.Image` 转存储格式时编码一次后再对输出字节算哈希，不要为了先算哈希额外全尺寸压一遍 PNG；配图和快照逐张上传后立即置空字节引用。
