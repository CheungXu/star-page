/*
 * 方案二（3002）· 原生 View Transitions API 共享元素飞行
 * document.startViewTransition 包裹 DOM 切换，浏览器自动对前后快照做 morph：
 *  - 输入卡 vt-prompt：中央大卡 ↔ 底部 composer，自动补间位置与尺寸
 *  - 侧边栏 vt-sidebar：两态保持，平滑过渡
 *  - hero logo vt-logo：进入工作区时优雅退场，返回时入场
 * 其余部分走 ::view-transition-old/new(root) 的淡入淡出 + 位移。
 * 不支持（如 Firefox）或 reduced-motion 时自动降级为直接切换。
 */
(function () {
  "use strict";
  document.body.classList.add("sp-vt");

  function transition(ctx) {
    var view = ctx.stage.querySelector(".sp-view");

    if (ctx.reduced || typeof document.startViewTransition !== "function") {
      view.innerHTML = ctx.html;
      return Promise.resolve();
    }

    var t = document.startViewTransition(function () {
      view.innerHTML = ctx.html;
    });

    return t.finished.catch(function () {});
  }

  var supported = typeof document.startViewTransition === "function";
  SPApp.init({
    label: "方案二 · View Transitions (3002)" + (supported ? "" : " · 当前浏览器不支持，已降级"),
    accent: "#22c55e",
    transition: transition,
  });
})();
