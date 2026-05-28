/*
 * 方案三（3003）· motion 库精细编排（命令式 FLIP / stagger）
 * 用 motion 的浏览器 ESM 做：
 *  - 输入卡 FLIP：从旧位置「飞」到新位置（hero 大卡 → 底部 composer，或反向）
 *  - 文字上浮成气泡：克隆 hero 输入文字，从输入框位置浮动淡出到「你的需求」气泡处
 *  - 三栏/步骤条/预览骨架 stagger 逐项入场
 *  - 旧 hero 元素快速淡出上移
 * motion 通过 ESM CDN 动态加载；若加载失败（离线等）自动降级为等效的 WAAPI 实现。
 * reduced-motion 时直接切换。
 *
 * 注：原型用命令式写法表达「视觉目标」；最终集成会改用 framer-motion 的
 * 声明式 AnimatePresence / layoutId，效果一致但写法不同。
 */
(function () {
  "use strict";
  document.body.classList.add("sp-motion");

  var motionLib = null;
  var EASE = [0.22, 1, 0.36, 1];

  // 动态加载 motion ESM（多 CDN 兜底），失败则返回 null 走 WAAPI 降级
  var motionReady = (async function () {
    var urls = [
      "https://cdn.jsdelivr.net/npm/motion@11/+esm",
      "https://esm.sh/motion@11",
    ];
    for (var i = 0; i < urls.length; i++) {
      try {
        var mod = await import(urls[i]);
        if (mod && typeof mod.animate === "function") {
          motionLib = mod;
          return mod;
        }
      } catch (e) {
        /* 尝试下一个 CDN */
      }
    }
    return null;
  })();

  function rect(el) {
    return el ? el.getBoundingClientRect() : null;
  }

  // 统一动画入口：优先 motion.animate（时间单位=秒），否则退回 WAAPI（毫秒）
  function A(target, keyframes, opts) {
    opts = opts || {};
    if (motionLib && motionLib.animate) {
      var mo = { duration: opts.duration || 0.4, ease: opts.easing || EASE };
      if (opts.delay != null) mo.delay = opts.delay;
      return motionLib.animate(target, keyframes, mo);
    }
    var nodes = target instanceof Element ? [target] : Array.prototype.slice.call(target);
    var dur = (opts.duration || 0.4) * 1000;
    var ease = Array.isArray(opts.easing)
      ? "cubic-bezier(" + (opts.easing || EASE).join(",") + ")"
      : opts.easing || "ease";
    var anims = nodes.map(function (n, idx) {
      var d =
        typeof opts.delay === "function"
          ? opts.delay(idx) * 1000
          : (opts.delay || 0) * 1000;
      return n.animate(keyframes, { duration: dur, easing: ease, delay: d, fill: "both" });
    });
    return {
      finished: Promise.all(
        anims.map(function (a) {
          return a.finished;
        })
      ).catch(function () {}),
    };
  }

  function staggerDelay(step, start) {
    start = start || 0;
    if (motionLib && motionLib.stagger) return motionLib.stagger(step, { startDelay: start });
    return function (i) {
      return start + i * step;
    };
  }

  function transition(ctx) {
    var stage = ctx.stage;
    var leaving = stage.querySelector(".sp-view");

    if (ctx.reduced) {
      leaving.innerHTML = ctx.html;
      return Promise.resolve();
    }

    // First：记录共享输入卡与文字源的位置
    var oldCard = leaving.querySelector('[data-shared="prompt"]');
    var firstCard = rect(oldCard);
    var oldText = leaving.querySelector("[data-float-src]");
    var floatText = oldText ? oldText.value || oldText.textContent : "";
    var firstText = rect(oldText);

    // 进入层（先不可见，避免闪烁）
    var entering = document.createElement("div");
    entering.className = "sp-view";
    entering.style.opacity = "0";
    entering.innerHTML = ctx.html;
    stage.appendChild(entering);

    var newCard = entering.querySelector('[data-shared="prompt"]');
    var lastCard = rect(newCard);
    var dst = entering.querySelector("[data-float-dst]");

    var done = [];

    // 旧视图快速淡出上移
    done.push(
      A(leaving, { opacity: [1, 0], transform: ["translateY(0px)", "translateY(-10px)"] }, {
        duration: 0.28,
        easing: [0.4, 0, 0.2, 1],
      }).finished
    );

    // 新视图整体淡入
    done.push(A(entering, { opacity: [0, 1] }, { duration: 0.36, delay: 0.05 }).finished);

    // 输入卡 FLIP 飞行
    if (firstCard && newCard && lastCard) {
      var dx = firstCard.left - lastCard.left;
      var dy = firstCard.top - lastCard.top;
      var sx = firstCard.width / lastCard.width;
      var sy = firstCard.height / lastCard.height;
      newCard.style.transformOrigin = "top left";
      newCard.style.position = "relative";
      newCard.style.zIndex = "5";
      done.push(
        A(
          newCard,
          {
            transform: [
              "translate(" + dx + "px," + dy + "px) scale(" + sx + "," + sy + ")",
              "translate(0px,0px) scale(1,1)",
            ],
          },
          { duration: 0.52, easing: EASE }
        ).finished
      );
    }

    // 文字上浮成气泡：仅 hero → 工作区
    if (floatText && firstText && dst && ctx.fromState === "hero") {
      var lastText = rect(dst);
      var clone = document.createElement("div");
      clone.className = "sp-float-clone";
      clone.textContent = floatText;
      clone.style.left = firstText.left + "px";
      clone.style.top = firstText.top + "px";
      clone.style.width = firstText.width + "px";
      document.body.appendChild(clone);
      dst.style.opacity = "0";
      var tdx = lastText.left - firstText.left;
      var tdy = lastText.top - firstText.top;
      A(
        clone,
        {
          transform: ["translate(0px,0px)", "translate(" + tdx + "px," + tdy + "px)"],
          opacity: [0.95, 0],
        },
        { duration: 0.5, easing: EASE }
      ).finished.then(function () {
        clone.remove();
        dst.style.opacity = "";
      });
    }

    // 新内容 stagger 逐项入场
    var staggered = Array.prototype.slice.call(entering.querySelectorAll("[data-stagger]"));
    if (staggered.length) {
      A(
        staggered,
        { opacity: [0, 1], transform: ["translateY(14px)", "translateY(0px)"] },
        { duration: 0.42, delay: staggerDelay(0.05, 0.12), easing: EASE }
      );
    }

    return Promise.all(done).then(function () {
      leaving.remove();
      entering.style.opacity = "";
      if (newCard) {
        newCard.style.transform = "";
        newCard.style.zIndex = "";
        newCard.style.position = "";
        newCard.style.transformOrigin = "";
      }
    });
  }

  // 等 motion 加载结果确定后再启动，避免首个过渡时库还没就绪
  motionReady.then(function () {
    var label = "方案三 · motion 库精细编排 (3003)" + (motionLib ? "" : " · 已降级 WAAPI");
    SPApp.init({ label: label, accent: "#a855f7", transition: transition });
  });
})();
