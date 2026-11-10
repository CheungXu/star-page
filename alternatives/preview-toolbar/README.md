# 结果预览工具栏与分享：备选方案

## 背景与线上方案

生成 3 个以上模型时，结果区是「大预览 + 底部缩略图（胶片条）」。原来「打开 / 复制 / 发布固定链接」浮在大预览上，鼠标一移上去就挡住页面。2026-11-11 做了四套方案（A、B、C1、C2）在多端口并行对比，经过九轮打磨后：

- **线上：C1 胶片联动**。选中的缩略图向右展开焦点卡：模型名（左对齐）在上，tokens / 用时 / 积分三项与「打开 / 放大 / 分享」按列对齐成一张小表；「继续改」是缩略图底部的宽条。大预览里只有页面本身，没有任何浮层。
- 分享（所有方案共用，已接后端）：按钮旁弹出浮层，左侧整列分享图、右侧可改标题 / 副标题 / 左中右对齐，保存后链接卡片与公开页海报同步更新。

过程记录见 `doc/20261111/preview-toolbar-and-share-variants.md`，截图在 `images/20261111/preview-toolbar-variants/`。

## 备选方案

| 方案 | 做法 | 优点 | 代价 / 没选的原因 | 代码 |
| --- | --- | --- | --- | --- |
| **A 浏览器顶栏** | 大预览上方一条 50px 细栏：左边当前模型「标签」（方形头像 + 名字 + 序号，底边模型专属色），中间地址栏（点开即新标签页），右边放大、分享、以此结果继续 | 最直观，像在真浏览器里看网页；操作离页面最近 | 页面少约 50px 高；和 C1 比，胶片条与操作分在上下两处 | 主线保留，`NEXT_PUBLIC_UX_VARIANT=A` |
| **C2 聚光胶片** | 选中的缩略图放大到 224×126 并浮起（白色间隙 + 主色描边），其它略暗；模型名、三个圆形玻璃按钮和「继续改」胶囊压在放大缩略图底部的渐暗层上，切换带轻微回弹 | 不额外占宽度，视觉冲击强、最有「作品集」感 | 按钮压在缩略图上，图标按钮需要悬停才看清含义；胶片条比 C1 高约 30px | 主线保留，`NEXT_PUBLIC_UX_VARIANT=C2` |
| B 侧边控制轨 | 大预览右侧一条竖排工具轨（头像、打开 / 复制 / 分享 / 放大 / 继续改、翻页），切换时名字在左上角闪现 | 页面高度一点不损失 | 竖排图标需要适应，信息密度低；第一轮即淘汰 | 仅标签存档 |
| C1「继续改」侧边竖按钮 | 焦点卡最右侧一整块与缩略图等高的竖按钮 | 最好点、最醒目 | 焦点卡变宽约 60px | 仅标签存档（`?cont=tab`） |
| C1「继续改」按钮行内 | 按钮行最右的主按钮，打开 / 放大 / 分享改为图标按钮 | 不压缩略图 | 三个操作只剩图标，含义要靠悬停提示 | 仅标签存档（`?cont=row`） |
| C1 用量左对齐 | 用量与按钮内容都靠左，图标对成竖线；用时按分钟显示 | 竖线规整 | 列宽不够，用时只能显示「16分」，丢精度 | 仅标签存档（`?meta=left`） |
| C1 模型名居中 | 模型名在整块焦点卡里居中 | 与居中的用量对称 | 和左对齐的按钮图标没有共同的竖线 | 仅标签存档（`?name=center`） |

关键截图：

- A：`images/20261111/preview-toolbar-variants/a-stage.png`、`a-chrome-hover.png`、`a-share.png`
- C2：`c2-stage.png`、`c2-spotlight.png`、`c2-spotlight-hover.png`
- C1 及其细节变体：`c1-continue-positions.png`；切换动画中间帧：`switch-transition.png`

## 代码在哪

- **主线**（`main`）：`code/frontend/app/components/workspace/variant.ts` 的 `UX_VARIANT` 决定结果区用哪套，构建时读 `NEXT_PUBLIC_UX_VARIANT`（`A` / `C1` / `C2`，默认 `C1`）。各方案的差异集中在：
  - `panels.tsx`：`GenerationResults` 里 A 的顶栏（`tb-chrome`）、C1 的 `FocusPanel` 与缩略图上的「继续改」、C2 的 `SpotOverlay`；
  - `actions.tsx`：`ResultActions` 的 `chrome`（A）、`card` / `continue`（C1）、`spot`（C2）三种摆放；
  - `actions.css` / `workspace.css`：`.tb-variant-a|c1|c2` 下的样式。
  - 分享浮层、胶片条滚轮横滑、大预览淡入淡出、切换动画是三套共用的，已接后端。
- **完整演示版本**：git 标签 `alt/preview-toolbar-v1`（分支 `ux/preview-toolbar-variants` 上，合并前的最后一版）。包含 A / B / C1 / C2、C1 的 `?cont=` `?meta=` `?name=` 细节开关、顶部方案角标、`?demo=9` 演示数据（11-11 那次 9 个模型的真实页面）和多端口脚本 `script/preview-toolbar-variants/`。主线已删除这些对比工具。

## 怎么复现

本地并排对比（不连后端、不扣积分）：

```bash
git worktree add /root/star-page-worktrees/preview-toolbar-alt alt/preview-toolbar-v1
cd /root/star-page-worktrees/preview-toolbar-alt/code/frontend
cp -al /root/star-page/code/frontend/node_modules ./node_modules
cd ../.. && bash script/preview-toolbar-variants/serve.sh start   # 3011 A、3013 C1、3014 C2
# 浏览器打开 http://localhost:3013/?demo=9&cont=thumb&meta=center&name=left 等
```

注意：标签里的分享浮层还是「仅前端」版本（标题存在本机），后端接入以主线为准。

在主线用真实数据看某个备选（会连真实后端）：

```bash
cd code/frontend
NEXT_PUBLIC_UX_VARIANT=A npm run dev   # 或 C2
```

## AB 测试建议

- **比较指标**：分享浮层打开率（`page_share_click` / 出现 3 个以上结果的会话）、复制链接率（`share_copy_link`）、保存分享图率（`share_save_poster`）、「继续改」点击后的下一轮提交率、固定链接被外部打开次数（`page_view_events` 非本人）。
- **埋点**：上面三个前端事件都带 `ux_variant`（A / C1 / C2）和 `model_key`，可以直接按方案分组。
- **分流**：目前方案是构建时决定的，同一时间只能上一套。真正做 AB 时需要把 `UX_VARIANT` 改成运行时读取（例如按用户 id 哈希分桶，或后端在 `/api/auth/me` 里下发实验分组），并在会话里保持分组不变。

## 状态

- 2026-11-11：C1（缩略图「继续改」+ 用量居中 + 名称左对齐）上线并接入后端；A、C2 保留在主线等待 AB；B 与 C1 其它细节变体仅标签存档。
