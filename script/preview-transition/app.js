/*
 * 首页 ↔ 生成页 衔接过渡动画 · 公共原型骨架
 * ---------------------------------------------------------------------------
 * 本文件只负责「用与生产一致的 className 重建两态 DOM」+「导演控制条 + 状态机」。
 * 三套方案各自的过渡实现（纯 CSS / View Transitions / motion）由对应的
 * variant-*.js 通过 SPApp.init({ transition }) 注入，互不影响。
 *
 * 注意：这是选型用的临时原型，不会进入生产代码。
 */
(function () {
  "use strict";

  // 与生产首页一致的一段示例需求：hero 预填它，提交后会变成对话流里的「你的需求」气泡
  const PROMPT_TEXT =
    "做一个面向客户的产品介绍页，风格简洁、高级，突出三个核心卖点与典型适用场景，并配一个醒目的行动号召。";

  // 模型思考抽屉里的示例 reasoning
  const REASONING_TEXT =
    "用户需要一个面向客户的产品介绍页。先规划信息架构：顶部 Hero 用一句话点明核心价值，" +
    "紧接三栏卖点卡片，再补充典型适用场景，最后放一个醒目的行动号召按钮，整体走简洁高级的留白风格……";

  const PREVIEW_URL = "https://star.example.com/p/demo-product-intro";

  // 与生产 page.tsx 完全一致的内联 SVG 图标
  const icons = {
    history:
      '<svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>',
    plus:
      '<svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>',
    attachment:
      '<svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"/></svg>',
    arrowUp:
      '<svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>',
    send:
      '<svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>',
    bolt:
      '<svg width="1em" height="1em" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M13 2 4.5 13.5H11l-1 8.5 8.5-11.5H12l1-8.5z"/></svg>',
  };

  // 推荐场景 chips（与生产一致）
  const presets = [
    { emoji: "🚀", label: "产品介绍页" },
    { emoji: "📊", label: "工作汇报" },
    { emoji: "👤", label: "个人简历" },
    { emoji: "🎉", label: "活动邀请" },
  ];

  function progressIcon(status) {
    if (status === "completed") return "✓";
    if (status === "running") return '<span class="progress-spinner" aria-hidden="true"></span>';
    if (status === "failed") return "!";
    return "";
  }

  // 侧边栏（收起态）——两态共享，是过渡的「保持」锚点
  function sidebarHTML() {
    return (
      '<nav class="history-sidebar collapsed" aria-label="历史创建" data-vt="sidebar" data-shared="sidebar">' +
      '<button class="sidebar-brand" type="button" title="展开侧边栏" aria-label="展开侧边栏">' +
      '<span class="brand-glyph" aria-hidden="true"><img src="stars-page-logo-simple.png" alt="" width="28" height="28"></span>' +
      "</button>" +
      '<button class="new-chat-button" type="button" title="新对话" aria-label="新对话">' +
      '<span class="sidebar-icon">' + icons.plus + "</span>" +
      "</button>" +
      '<button class="sidebar-icon-button" type="button" title="历史创建" aria-label="历史创建">' +
      '<span class="sidebar-icon">' + icons.history + "</span>" +
      "</button>" +
      '<div class="history-content" aria-hidden="true">' +
      '<div class="history-title">历史创建</div>' +
      '<div class="history-list"></div>' +
      "</div>" +
      "</nav>"
    );
  }

  // 输入卡片：hero 大卡 ↔ 底部 compact composer，是共享元素飞行的主角
  function promptFormHTML(compact) {
    const wrapCls = compact ? "compact-wrap" : "hero-wrap";
    const cardCls = compact ? "compact-prompt" : "hero-prompt";
    const textareaContent = compact ? "" : PROMPT_TEXT;
    const placeholder = compact
      ? "继续描述你想调整的方向…"
      : "说说你想做的页面，例如「面向客户的产品介绍页」";
    const rows = compact ? 1 : 3;

    const fileHint = compact
      ? ""
      : '<span class="file-hint">docx · pptx · xlsx · txt · md · html，单文件 ≤ 50MB</span>';

    const submit = compact
      ? '<button class="submit-button is-secondary" type="button" aria-label="发送修改">发送<span class="button-icon" aria-hidden="true">' +
        icons.send +
        "</span></button>"
      : '<button class="submit-button" type="button" aria-label="创建页面">创建<span class="button-icon" aria-hidden="true">' +
        icons.arrowUp +
        "</span></button>";

    const chips = compact
      ? ""
      : '<div class="prompt-chip-row" role="list" aria-label="推荐场景" data-stagger>' +
        presets
          .map(
            (p) =>
              '<button type="button" role="listitem" class="prompt-chip"><span class="chip-emoji" aria-hidden="true">' +
              p.emoji +
              "</span>" +
              p.label +
              "</button>"
          )
          .join("") +
        "</div>";

    return (
      '<div class="prompt-form-wrap ' + wrapCls + '">' +
      '<form class="prompt-card ' + cardCls + '" data-vt="prompt" data-shared="prompt" onsubmit="return false">' +
      '<textarea rows="' + rows + '" placeholder="' + placeholder + '" data-float-src>' + textareaContent + "</textarea>" +
      '<div class="prompt-toolbar">' +
      '<div class="prompt-tool-group">' +
      '<label class="file-upload-button" title="上传文档作为生成参考"><span class="button-icon" aria-hidden="true">' +
      icons.attachment +
      "</span>上传资料</label>" +
      fileHint +
      "</div>" +
      submit +
      "</div>" +
      "</form>" +
      chips +
      "</div>"
    );
  }

  // 创建节点步骤条：thinking（思考中）与 completed（全部完成）两种快照
  function stepsHTML(mode) {
    const thinking = mode !== "completed";
    const steps = [
      {
        id: "model_thinking",
        title: "模型思考",
        desc: thinking ? "模型正在展开思考" : "模型思考完成",
        status: thinking ? "running" : "completed",
        thinking: true,
      },
      {
        id: "model_output",
        title: "模型输出答案",
        desc: thinking ? "等待模型开始输出 HTML" : "HTML 输出完成",
        status: thinking ? "pending" : "completed",
        token: true,
        tokens: thinking ? 0 : 2048,
        estimated: thinking,
      },
      {
        id: "deploy",
        title: "部署",
        desc: thinking ? "等待 HTML 上传和数据库更新" : "页面已部署上线",
        status: thinking ? "pending" : "completed",
      },
    ];

    return steps
      .map((step, index) => {
        const isLast = index === steps.length - 1 ? " is-last" : "";
        const toggle =
          step.id === "model_thinking"
            ? '<button class="node-toggle" type="button">收起</button>'
            : "";
        const token = step.token
          ? '<span class="token-meta"><span class="token-meta-icon" aria-hidden="true">' +
            icons.bolt +
            "</span>输出 " +
            step.tokens +
            " tokens" +
            (step.estimated ? "（估算）" : "") +
            "</span>"
          : "";
        const thinkingBody = step.thinking
          ? '<div class="thinking-node-body"><pre>' + REASONING_TEXT + "</pre></div>"
          : "";

        return (
          '<div class="progress-item ' + step.status + isLast + '" data-stagger>' +
          '<span class="progress-icon" aria-hidden="true">' + progressIcon(step.status) + "</span>" +
          '<div class="progress-body">' +
          '<div class="progress-title-row"><strong>' + step.title + "</strong>" + toggle + "</div>" +
          "<p>" + step.desc + "</p>" +
          token +
          thinkingBody +
          "</div>" +
          "</div>"
        );
      })
      .join("");
  }

  function skeletonHTML() {
    return (
      '<div class="preview-empty" data-stagger>' +
      '<div class="preview-skeleton" aria-hidden="true">' +
      '<div class="skeleton-bar skeleton-topbar"><span class="skeleton-chip"></span><span class="skeleton-chip"></span><span class="skeleton-chip"></span></div>' +
      '<div class="skeleton-hero"><span class="skeleton-line skeleton-line-lg"></span><span class="skeleton-line skeleton-line-md"></span><span class="skeleton-line skeleton-line-sm"></span></div>' +
      '<div class="skeleton-cards"><span class="skeleton-card"></span><span class="skeleton-card"></span><span class="skeleton-card"></span></div>' +
      "</div>" +
      "<h3>正在为你生成页面…</h3>" +
      "<p>左侧实时展示模型思考与创建节点，完成后这里会渲染最终网页。</p>" +
      "</div>"
    );
  }

  function previewWindowHTML() {
    return (
      '<div class="preview-window" data-stagger>' +
      '<div class="preview-window-bar" aria-hidden="true">' +
      '<span class="win-dots"><span class="win-dot win-dot-red"></span><span class="win-dot win-dot-amber"></span><span class="win-dot win-dot-green"></span></span>' +
      '<span class="preview-window-url">' + PREVIEW_URL + "</span>" +
      "</div>" +
      '<div class="preview-viewport">' +
      '<iframe title="生成页面预览" src="demo-page.html" style="width:100%;height:100%;border:0;background:#fff"></iframe>' +
      "</div>" +
      "</div>" +
      '<div class="link-actions" data-stagger>' +
      '<a href="demo-page.html" target="_blank" rel="noreferrer">打开页面</a>' +
      '<button type="button">复制链接</button>' +
      "</div>"
    );
  }

  function heroHTML() {
    return (
      '<main class="home-shell sidebar-collapsed">' +
      '<div class="hero-aurora" aria-hidden="true">' +
      '<span class="aurora-blob aurora-blob-1"></span>' +
      '<span class="aurora-blob aurora-blob-2"></span>' +
      '<span class="aurora-blob aurora-blob-3"></span>' +
      '<span class="aurora-grid"></span>' +
      "</div>" +
      sidebarHTML() +
      '<section class="page-shell">' +
      '<div class="hero">' +
      '<div class="brand-mark" data-stagger><img src="stars-page-logo-simple.png" alt="星页 StarPage" class="brand-logo" width="56" height="56" data-vt="logo"></div>' +
      "<h1 data-stagger>想做什么页面？</h1>" +
      '<p class="subtitle" data-stagger>说说你的想法，<strong class="brand-inline">星页 StarPage</strong> 帮你生成一个可分享的精致网页。</p>' +
      promptFormHTML(false) +
      "</div>" +
      "</section>" +
      "</main>"
    );
  }

  function workspaceHTML(mode) {
    const completed = mode === "completed";
    const dotCls = completed ? "ready" : "loading";
    const statusText = completed ? "已生成" : "生成中";
    const previewBody = completed ? previewWindowHTML() : skeletonHTML();
    const charCount = PROMPT_TEXT.length;

    return (
      '<main class="workspace-shell">' +
      '<section class="workspace-layout sidebar-collapsed">' +
      sidebarHTML() +
      '<aside class="conversation-pane">' +
      '<div class="conversation-scroll">' +
      '<div class="chat-message user-message" data-stagger>' +
      '<div class="user-message-meta"><span>你的需求</span><span>' + charCount + " 字</span></div>" +
      "<p data-float-dst>" + PROMPT_TEXT + "</p>" +
      "</div>" +
      '<div class="chat-message assistant-message progress-message" data-stagger>' +
      '<div class="assistant-label">创建节点</div>' +
      '<div class="progress-list">' + stepsHTML(mode) + "</div>" +
      "</div>" +
      "</div>" +
      '<div class="composer-wrap">' + promptFormHTML(true) + "</div>" +
      "</aside>" +
      '<section class="preview-pane">' +
      '<article class="panel preview-panel workspace-preview">' +
      '<div class="panel-heading">' +
      '<span class="dot ' + dotCls + '" aria-hidden="true"></span>' +
      "<h2>页面预览</h2>" +
      '<span class="preview-status-text">' + statusText + "</span>" +
      "</div>" +
      previewBody +
      "</article>" +
      "</section>" +
      "</section>" +
      "</main>"
    );
  }

  function viewHTML(state) {
    if (state === "hero") return heroHTML();
    if (state === "work-completed") return workspaceHTML("completed");
    return workspaceHTML("thinking");
  }

  // ---------------------------------------------------------------------------
  // 导演控制条 + 状态机：三套方案共用，只把「如何过渡」委托给注入的 transition()
  // ---------------------------------------------------------------------------
  const TRANSITIONS = [
    { act: "generate", to: "work-thinking", label: "生成 → 进入工作区" },
    { act: "history", to: "work-completed", label: "历史进入（直达完成态）" },
    { act: "newchat", to: "hero", label: "返回首页（新对话）" },
  ];

  function directorHTML(label, accent) {
    const buttons = TRANSITIONS.map(
      (t) =>
        '<button type="button" class="sp-btn" data-act="' + t.act + '">' + t.label + "</button>"
    ).join("");

    return (
      '<header class="sp-director">' +
      '<div class="sp-director-brand">' +
      '<span class="sp-director-dot" style="background:' + accent + '"></span>' +
      "<strong>衔接过渡原型</strong>" +
      '<span class="sp-director-variant">' + label + "</span>" +
      "</div>" +
      '<div class="sp-director-actions">' +
      buttons +
      '<label class="sp-reduce"><input type="checkbox" id="sp-reduce-toggle"> 模拟 reduced-motion</label>' +
      '<span class="sp-state-tag" id="sp-state-tag">当前：首页</span>' +
      "</div>" +
      "</header>"
    );
  }

  const STATE_LABEL = {
    hero: "首页",
    "work-thinking": "生成中",
    "work-completed": "完成态",
  };

  function init(options) {
    const opts = options || {};
    const label = opts.label || "未命名方案";
    const accent = opts.accent || "#3563e9";
    const transition = opts.transition;
    const root = document.getElementById("sp-root");

    root.insertAdjacentHTML("beforeend", directorHTML(label, accent));
    const stage = document.createElement("div");
    stage.className = "sp-stage";
    stage.innerHTML = '<div class="sp-view"></div>';
    root.appendChild(stage);

    const reduceToggle = document.getElementById("sp-reduce-toggle");
    const stateTag = document.getElementById("sp-state-tag");

    let state = "hero";
    let busy = false;

    function reducedMotion() {
      const sys = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      return Boolean(sys || (reduceToggle && reduceToggle.checked));
    }

    function setView(html) {
      stage.querySelector(".sp-view").innerHTML = html;
    }

    // 首屏：直接渲染 hero
    setView(viewHTML("hero"));
    stateTag.textContent = "当前：" + STATE_LABEL[state];

    function go(toState) {
      if (busy || toState === state) return;
      busy = true;
      const fromState = state;
      const html = viewHTML(toState);
      const ctx = { stage, view: stage.querySelector(".sp-view"), fromState, toState, html, reduced: reducedMotion(), SP: SP };

      Promise.resolve(transition(ctx)).then(function () {
        state = toState;
        stateTag.textContent = "当前：" + STATE_LABEL[state];
        busy = false;
      }).catch(function (err) {
        // 过渡异常时兜底直接切换，保证原型不会卡死
        console.warn("[过渡异常，回退为直接切换]", err);
        setView(html);
        state = toState;
        stateTag.textContent = "当前：" + STATE_LABEL[state];
        busy = false;
      });
    }

    root.querySelector(".sp-director-actions").addEventListener("click", function (event) {
      const btn = event.target.closest("button[data-act]");
      if (!btn) return;
      const target = TRANSITIONS.find((t) => t.act === btn.getAttribute("data-act"));
      if (target) go(target.to);
    });

    return { go, getState: () => state };
  }

  const SP = {
    PROMPT_TEXT,
    PREVIEW_URL,
    icons,
    presets,
    sidebarHTML,
    promptFormHTML,
    stepsHTML,
    skeletonHTML,
    previewWindowHTML,
    heroHTML,
    workspaceHTML,
    viewHTML,
    setView: function (stage, html) {
      stage.querySelector(".sp-view").innerHTML = html;
    },
  };

  window.SP = SP;
  window.SPApp = { init };
})();
