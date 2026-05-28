/*
 * 方案一（3001）· 纯 CSS 交叉过渡
 * 旧视图淡出 + 上移收缩，新视图淡入 + 自下浮起；两层叠放交叉，零依赖、全浏览器一致。
 * DOM 切换靠「新建进入层 + 定时移除离开层」协调，时长 ~340ms，easeOutExpo 曲线。
 */
(function () {
  "use strict";
  document.body.classList.add("sp-cross");

  function wait(ms) {
    return new Promise(function (resolve) {
      setTimeout(resolve, ms);
    });
  }

  function transition(ctx) {
    var stage = ctx.stage;
    var leaving = stage.querySelector(".sp-view");

    // reduced-motion / 无障碍降级：直接切换，不做任何动画
    if (ctx.reduced) {
      leaving.innerHTML = ctx.html;
      return Promise.resolve();
    }

    var entering = document.createElement("div");
    entering.className = "sp-view sp-entering";
    entering.innerHTML = ctx.html;
    stage.appendChild(entering);
    leaving.classList.add("sp-leaving");

    // 强制 reflow，确保进入层的初始态（透明 + 下移）先生效，再触发过渡
    void entering.offsetWidth;

    requestAnimationFrame(function () {
      leaving.classList.add("sp-active");
      entering.classList.add("sp-active");
    });

    return wait(360).then(function () {
      leaving.remove();
      entering.classList.remove("sp-entering", "sp-active");
    });
  }

  SPApp.init({
    label: "方案一 · 纯 CSS 交叉过渡 (3001)",
    accent: "#38bdf8",
    transition: transition,
  });
})();
