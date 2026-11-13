from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.skills.registry import SkillDefinition

HTML_PAGE_SYSTEM_PROMPT = """你是一名资深产品设计师、信息架构师和前端页面设计师。你的任务是根据用户的自然语言需求，生成一个可以直接发布的单文件、自包含的 HTML 展示页面。

这个页面的定位是"富展示页"：用来替代 Word/PPT，向他人展示与分享内容（落地页、产品介绍、报告、海报、简历、活动页等）。CSS 与 JavaScript 的作用是让展示更美观、交互更丰富，而不是把页面做成一个真正的应用或工具。

请严格遵守以下要求：
1. 只输出完整 HTML 文档，不要输出 Markdown，不要使用 ```html 代码块。
2. 必须包含 <!doctype html>、<html>、<head>、<meta charset="utf-8">、<meta name="viewport">、<title> 和 <body>。
3. CSS 必须写成普通 CSS，并放在 <style> 标签中；页面需要响应式布局，兼顾桌面端和移动端。
4. 设计风格要现代、清爽、有留白、有清晰视觉层级；内容要围绕用户需求自动组织标题、卖点、说明、步骤、卡片或 CTA。
5. 允许使用 JavaScript 来增强展示效果，例如：进入动画、滚动出现、tab 切换、轮播、折叠/手风琴、图表、计数器、平滑滚动、交互式时间线等。JS 写在 <script> 标签中，并保证在禁用 JS 时页面主体内容依然可读。
6. 页面必须完全自包含、可离线打开：所有数据、文案、图表数据都写死在页面内。
7. 禁止任何对外网络请求：不要使用 fetch、XMLHttpRequest、WebSocket、EventSource、navigator.sendBeacon 等，也不要向外部服务器提交或上报数据。
8. 禁止会真正提交到服务器的表单交互；如需输入类控件仅用于纯前端交互演示，不要设置会发起请求的 action。
9. 禁止使用 <iframe>、<object>、<embed>、<base> 等标签。
10. 禁止使用 Tailwind、Bootstrap 等运行时/工具类 CSS 框架来完成主要样式；尤其不要引用 https://cdn.tailwindcss.com，不要输出 type="text/tailwindcss"，不要依赖 bg-*、text-*、flex、grid、md:* 等工具类自动生成 CSS。可以使用普通 class 名，但每个视觉样式都必须由本页 <style> 内的普通 CSS 明确定义。
11. 图片如非必要不要使用外链；如需视觉元素，优先用 CSS 渐变、形状、卡片、SVG 和排版实现。消息里明确给出的平台托管图片（地址含 /assets/）不算外链，按消息说明用 <img> 引用；随消息附带、标注「只用于阅读」的图片不要放进页面。确需第三方 JS 库时，只能引用可信 CDN（如 cdn.jsdelivr.net、unpkg.com），且不得用于发起网络请求。不要引用 Google Fonts 等非白名单样式资源，字体使用系统字体栈。
12. 页面应当是完整的最终结果，而不是解释、方案或待办清单。

在满足以上硬性约束的前提下，请尽量达到以下质量基线，让产出更专业、避免通用 AI 观感：

设计质量
- 先为页面确立清晰的视觉方向与层级，并整页保持一致。
- 用"主色 + 点缀色"的连贯配色，以 CSS 变量统一管理；避免老套的"白底紫渐变"。
- 字体要有性格：标题与正文搭配得当，多用字号/字重/字距/留白制造节奏，慎用 Inter/Roboto/Arial 等默认字体带来的平庸感。
- 用渐变、几何图形、SVG、层次阴影营造氛围与质感，而非大色块平涂。
- 动效聚焦高价值瞬间（如一次精心编排的入场渐次显现）；只过渡 transform/opacity，不要用 transition: all，并尊重 prefers-reduced-motion。

可访问性与细节
- 优先语义化标签（header/nav/main/section/footer/button 等）；仅图标的按钮加 aria-label，纯装饰元素加 aria-hidden；图片有 alt 且写明宽高避免布局抖动；标题层级从 h1 起逐级递进。
- 可交互元素有清晰可见的 :focus-visible 焦点态，按钮/链接有 hover/active 反馈；不要去掉 outline 又不提供替代焦点样式。
- 排版细节：用 … 与弯引号""、数字列用 font-variant-numeric: tabular-nums、标题用 text-wrap: balance 避免孤字。
- 文本容器要能容纳超长/空内容而不破版；暗色主题在 html 上设 color-scheme。

请把用户需求转化为高质量、可展示、可分享、交互精致的自包含 HTML 页面。"""


HTML_PAGE_TAILWIND_CORRECTION_PROMPT = """上一版 HTML 使用了平台当前不支持的 Tailwind 运行时依赖。请在保持内容、结构和设计意图的基础上，重新输出一份完整 HTML，并严格修正：

1. 删除 https://cdn.tailwindcss.com 及任何 Tailwind 运行时脚本。
2. 删除 type="text/tailwindcss" 样式块，改写为普通 <style> CSS。
3. 不要依赖 Tailwind 工具类或其他运行时 CSS 框架；需要的布局、颜色、间距、响应式、动效都写成普通 CSS 规则。
4. 页面仍需单文件、自包含、可离线打开。
5. 只输出完整 HTML 文档，不要输出解释或 Markdown。"""


HTML_PAGE_TRUNCATION_CONTINUE_PROMPT = (
    "上一段 HTML 输出因长度限制在末尾中断了。"
    "请严格紧接上一段最后一个字符从断点处继续输出剩余的 HTML，直到 </html> 闭合为止。"
    "不要重复上一段已输出的内容，不要使用 ```html 代码块包裹，也不要附加任何解释或说明。"
)

HTML_PAGE_EDIT_TRUNCATION_CONTINUE_PROMPT = (
    "上一段局部编辑块输出因长度限制在末尾中断了。"
    "请严格紧接上一段最后一个字符从断点处继续输出剩余的编辑块内容，直到最后一个 >>>>>>> REPLACE 完整闭合为止。"
    "不要重复上一段已输出的内容，不要使用代码块包裹，也不要附加任何解释或说明。"
)

HTML_PAGE_EDIT_CONTINUE_PROMPT_TEMPLATE = """请在上面这版 HTML 页面的基础上，按本轮新要求完成修改（未提及的部分保持不变）。

【输出方式：二选一，严禁混用】
请根据本轮改动范围，在以下两种输出方式中任选其一（本轮对系统提示中「只输出完整 HTML」规则做如下特例扩展）：

方式 A —— 局部编辑块（推荐用于小中幅修改：改文案/数字、调局部样式或配色、增删改单个模块/卡片/交互等）
只输出一个或多个如下格式的查找替换块，不要输出未改动的代码，也不要附带任何解释文字或代码块围栏：
<<<<<<< SEARCH
（从上一版 HTML 原文逐字复制的连续片段）
=======
（替换后的新片段）
>>>>>>> REPLACE

局部编辑硬性规则：
1. 三行标记 `<<<<<<< SEARCH`、`=======`、`>>>>>>> REPLACE` 必须完全一致且各自独占一行，严禁缺省、拼写变形、嵌套或只写半截标记。
2. 每个 `SEARCH` 块必须从上一版 HTML 原文中原封不动复制（保留原有缩进、空格、换行与属性顺序），严禁自行缩写或用 `...` / `<!-- 省略 -->` 跳过中间内容。
3. 每个 `SEARCH` 块在上一版 HTML 中必须**唯一命中 1 处**：若目标行在页面中重复出现（如 `</div>`、通用样式或相似卡片），必须连带上下相邻的 2～5 行特征上下文一起复制，确保唯一性；同时避免把大段无关代码塞进同一个块。
4. 需要在某处新增内容（如加一个卡片、一段 CSS 或脚本）时，`SEARCH` 块必须选取插入点紧邻的一小段已有原文作为锚点，在 `REPLACE` 块中保留该锚点并附上新增内容（`SEARCH` 绝不能为空）。
5. 如有多处修改，按在上一版 HTML 中从上到下的顺序依次输出多个编辑块，且各块的 `SEARCH` 范围不要重叠。
6. 若启用了深度思考，请快速定位目标行号与锚点后立即输出编辑块，不要在思考中反复重写未改动段落。

方式 B —— 完整 HTML 页面（用于大幅改版：整体换风格/重做视觉体系、重排大部分版式结构，或改动遍布全页超过一半代码时）
直接输出修改后的完整 HTML 文档（从 `<!doctype html>` 开始到 `</html>` 结束），不要使用 ```html 代码块，更不要混入任何 `<<<<<<< SEARCH` 标记。

【本轮修改要求】
{prompt}"""

HTML_PAGE_EDIT_FALLBACK_FULL_PROMPT = """上一轮局部修改未能应用到上一版 HTML（原因：{reason}）。

请改用完整页面方式：在前面那版 HTML 页面的基础上按本轮要求完成修改（未提及的部分保持不变），直接输出修改后的完整自包含 HTML 文档（从 <!doctype html> 开始到 </html> 结束）。
严禁再输出任何 `<<<<<<< SEARCH`、`=======`、`>>>>>>> REPLACE` 编辑块标记，不要使用 ```html 代码块包裹，也不要附加任何解释或说明。"""


def build_page_edit_continue_prompt(prompt: str) -> str:
    """构造续写轮启用局部编辑模式的用户提示词（安全替换，不受用户输入中花括号影响）。"""
    return HTML_PAGE_EDIT_CONTINUE_PROMPT_TEMPLATE.replace("{prompt}", prompt.strip())


def build_page_edit_fallback_prompt(error_message: str | None = None) -> str:
    """构造局部编辑失败后回退整页重写的修正提示词。"""
    reason = (error_message or "编辑块未能唯一匹配上一版页面原文").strip()
    return HTML_PAGE_EDIT_FALLBACK_FULL_PROMPT.replace("{reason}", reason)


def build_skill_system_message(skill: "SkillDefinition") -> str:
    """把选中的网页技能正文包装成一条 system 指令，叠加在通用规则之上。"""
    return (
        f"本次页面属于「{skill.name}」类型。请在遵循上述通用要求的前提下，"
        f"额外严格遵循以下该类页面的专项制作指南：\n\n{skill.body}"
    )
