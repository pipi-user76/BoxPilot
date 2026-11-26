/* 文件职责：登录页的动态环形画廊。
 *
 * 几何与交互参考 ThreeUI "Gallery Heading"（https://threeui.com/component/gallery-heading，
 * MIT License，Meng To / Design+Code）。这里改成 BoxPilot 的纯色卡片与标题排版，
 * 用 Canvas 2D 绘制，不引入 Three.js 或其它依赖。
 *
 * 交互：自动缓慢公转；鼠标横向拖动可加速；滚轮可推动旋转；标签页隐藏时暂停。
 */
(function () {
  "use strict";

  const canvas = document.getElementById("hero-canvas");
  if (!canvas) return;

  const ctx = canvas.getContext("2d");
  const stage = canvas.parentElement;
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const TS = 256;          // 单块贴图分辨率
  const COUNT = 12;
  const RATIO = 0.36;      // 半短轴 / 半长轴，越小越“侧视”
  const AXIS_DEG = -14;
  const DIST = 5.6;
  const PERIOD = 42;       // 自动公转一圈秒数
  const RADIUS = 0.2;      // 圆角比例

  // 纯色卡片：低饱和的纸白、石墨、砖红、墨绿、赭黄。
  const COLORS = [
    "#e9e3d6", "#b7b1a5", "#2a2a2a", "#1d2430",
    "#8c3a2a", "#2f4a3a", "#c9a24b", "#6b2f3a",
    "#d8d1c2", "#3a3f4a", "#a35b3a", "#476b5a",
  ];

  function rng(seed) {
    let s = seed >>> 0;
    return function () {
      s ^= s << 13; s >>>= 0;
      s ^= s >>> 17;
      s ^= s << 5; s >>>= 0;
      return s / 4294967296;
    };
  }

  function makeGrain() {
    const size = 128;
    const c = document.createElement("canvas");
    c.width = c.height = size;
    const x = c.getContext("2d");
    const data = x.createImageData(size, size);
    const random = rng(0x51f3);
    for (let i = 0; i < data.data.length; i += 4) {
      const v = 128 + (random() - 0.5) * 120;
      data.data[i] = data.data[i + 1] = data.data[i + 2] = v;
      data.data[i + 3] = 255;
    }
    x.putImageData(data, 0, 0);
    return c;
  }

  const grain = makeGrain();

  function makeTile(color) {
    const c = document.createElement("canvas");
    c.width = c.height = TS;
    const x = c.getContext("2d");

    x.fillStyle = color;
    x.fillRect(0, 0, TS, TS);

    // 底部轻压暗，让平涂卡片有一点体积。
    const shade = x.createLinearGradient(0, 0, 0, TS);
    shade.addColorStop(0, "rgba(255,255,255,0.06)");
    shade.addColorStop(0.55, "rgba(0,0,0,0)");
    shade.addColorStop(1, "rgba(0,0,0,0.24)");
    x.fillStyle = shade;
    x.fillRect(0, 0, TS, TS);

    x.save();
    x.globalCompositeOperation = "overlay";
    x.globalAlpha = 0.22;
    x.fillStyle = x.createPattern(grain, "repeat");
    x.fillRect(0, 0, TS, TS);
    x.restore();

    return c;
  }

  function makeBack(front) {
    const c = document.createElement("canvas");
    c.width = c.height = TS;
    const x = c.getContext("2d");
    x.drawImage(front, 0, 0);
    x.globalCompositeOperation = "multiply";
    x.fillStyle = "rgba(10,10,12,0.55)";
    x.fillRect(0, 0, TS, TS);
    return c;
  }

  const front = COLORS.map(makeTile);
  const back = front.map(makeBack);

  const ax = (AXIS_DEG * Math.PI) / 180;
  const cf = RATIO;
  const sf = Math.sqrt(1 - cf * cf);
  const U = [Math.cos(ax), Math.sin(ax), 0];
  const V = [-Math.sin(ax) * cf, Math.cos(ax) * cf, sf];
  const AXIS = [
    U[1] * V[2] - U[2] * V[1],
    U[2] * V[0] - U[0] * V[2],
    U[0] * V[1] - U[1] * V[0],
  ];

  let W = 0;
  let H = 0;
  let a = 0;
  let ox = 0;
  let oy = 0;
  let headLayer = null;

  function resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const rect = stage.getBoundingClientRect();
    W = Math.max(1, Math.round(rect.width * dpr));
    H = Math.max(1, Math.round(rect.height * dpr));
    canvas.width = W;
    canvas.height = H;
    a = Math.min(W * 0.42, H * 0.40);
    ox = W * 0.5;
    oy = H * 0.50;
    buildHead();
  }

  function drawTracked(x, text, size, tracking, color, baseline) {
    x.save();
    x.font = '700 ' + size + 'px "Helvetica Neue", Helvetica, Arial, sans-serif';
    x.fillStyle = color;
    x.textBaseline = "alphabetic";
    x.textAlign = "left";

    let width = 0;
    for (const ch of text) width += x.measureText(ch).width + tracking;
    width -= tracking;

    let cx = W / 2 - width / 2;
    for (const ch of text) {
      x.fillText(ch, cx, baseline);
      cx += x.measureText(ch).width + tracking;
    }
    x.restore();
  }

  function buildHead() {
    headLayer = document.createElement("canvas");
    headLayer.width = W;
    headLayer.height = H;
    const x = headLayer.getContext("2d");

    const size = Math.min(W * 0.082, H * 0.14);
    const line1 = size * 0.92;
    const line2 = size;
    const cy = H * 0.445;

    drawTracked(x, "BOXPILOT", line1, size * 0.16, "#8a8a8a", cy - size * 0.14);
    drawTracked(x, "IN SLOW ORBIT", line2, size * 0.05, "#f4f2ec", cy + size * 0.86);
  }

  function project(p) {
    const k = (a * DIST) / (DIST - p[2]);
    return [ox + k * p[0], oy + k * p[1]];
  }

  function roundRect(x, w, h, r) {
    x.beginPath();
    x.moveTo(-w / 2 + r, -h / 2);
    x.lineTo(w / 2 - r, -h / 2);
    x.quadraticCurveTo(w / 2, -h / 2, w / 2, -h / 2 + r);
    x.lineTo(w / 2, h / 2 - r);
    x.quadraticCurveTo(w / 2, h / 2, w / 2 - r, h / 2);
    x.lineTo(-w / 2 + r, h / 2);
    x.quadraticCurveTo(-w / 2, h / 2, -w / 2, h / 2 - r);
    x.lineTo(-w / 2, -h / 2 + r);
    x.quadraticCurveTo(-w / 2, -h / 2, -w / 2 + r, -h / 2);
    x.closePath();
  }

  function drawTile(index, psi) {
    const c = Math.cos(psi);
    const s = Math.sin(psi);
    const C = [c * U[0] + s * V[0], c * U[1] + s * V[1], c * U[2] + s * V[2]];
    const T = [-s * U[0] + c * V[0], -s * U[1] + c * V[1], -s * U[2] + c * V[2]];
    const half = 0.175;

    const p0 = project(C);
    const pT = project([C[0] + T[0] * half, C[1] + T[1] * half, C[2] + T[2] * half]);
    const pA = project([C[0] + AXIS[0] * half, C[1] + AXIS[1] * half, C[2] + AXIS[2] * half]);

    const ex = pT[0] - p0[0];
    const ey = pT[1] - p0[1];
    const fx = pA[0] - p0[0];
    const fy = pA[1] - p0[1];

    if (Math.abs(ex * fy - ey * fx) < 3) return;

    const facing = C[2] > 0;
    const img = facing ? front[index] : back[index];

    ctx.save();
    ctx.setTransform(
      (ex * 2) / TS, (ey * 2) / TS,
      (fx * 2) / TS, (fy * 2) / TS,
      p0[0], p0[1]
    );
    roundRect(ctx, TS, TS, TS * RADIUS);
    ctx.clip();
    ctx.drawImage(img, -TS / 2, -TS / 2, TS, TS);
    ctx.strokeStyle = "rgba(255,255,255,0.10)";
    ctx.lineWidth = 4;
    roundRect(ctx, TS, TS, TS * RADIUS);
    ctx.stroke();
    ctx.restore();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
  }

  let spin = 0.9;
  let velocity = 0;
  let dragging = false;
  let dragX = 0;
  let last = 0;
  let raf = 0;

  function draw() {
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = "#000000";
    ctx.fillRect(0, 0, W, H);
    ctx.imageSmoothingQuality = "high";

    const list = [];
    for (let i = 0; i < COUNT; i += 1) {
      const psi = spin - (i * 2 * Math.PI) / COUNT;
      const c = Math.cos(psi);
      const s = Math.sin(psi);
      list.push({ i, psi, z: c * U[2] + s * V[2] });
    }

    const far = list.filter((item) => item.z <= 0).sort((p, q) => p.z - q.z);
    const near = list.filter((item) => item.z > 0).sort((p, q) => p.z - q.z);

    far.forEach((item) => drawTile(item.i, item.psi));
    // 标题夹在远侧与近侧卡片之间，转到前面的板会压住字母。
    if (headLayer) ctx.drawImage(headLayer, 0, 0);
    near.forEach((item) => drawTile(item.i, item.psi));
  }

  function frame(now) {
    if (!last) last = now;
    const dt = Math.min(64, now - last) / 1000;
    last = now;

    if (!dragging) {
      velocity *= 0.94;
      spin += dt * ((Math.PI * 2) / PERIOD) + velocity;
    }

    draw();
    raf = requestAnimationFrame(frame);
  }

  function play() {
    if (raf) return;
    last = 0;
    raf = requestAnimationFrame(frame);
  }

  function stop() {
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
  }

  function onPointerDown(event) {
    dragging = true;
    dragX = event.clientX;
    canvas.setPointerCapture?.(event.pointerId);
    canvas.style.cursor = "grabbing";
  }

  function onPointerMove(event) {
    if (!dragging) return;
    const dx = event.clientX - dragX;
    dragX = event.clientX;
    spin += dx * 0.007;
    velocity = dx * 0.004;
  }

  function onPointerUp() {
    dragging = false;
    canvas.style.cursor = "grab";
  }

  function onWheel(event) {
    event.preventDefault();
    velocity += event.deltaY * 0.0007;
    velocity = Math.max(-0.18, Math.min(0.18, velocity));
  }

  resize();
  draw();

  if (!reduceMotion) {
    play();
    canvas.style.cursor = "grab";
    canvas.addEventListener("pointerdown", onPointerDown);
    canvas.addEventListener("pointermove", onPointerMove);
    canvas.addEventListener("pointerup", onPointerUp);
    canvas.addEventListener("pointercancel", onPointerUp);
    canvas.addEventListener("wheel", onWheel, { passive: false });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) stop();
      else play();
    });
  }

  window.addEventListener("resize", () => {
    resize();
    draw();
  });

  if (window.ResizeObserver) {
    new ResizeObserver(() => {
      resize();
      draw();
    }).observe(stage);
  }
})();
