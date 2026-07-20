// =============================================================================
// @file  scene.js
// @brief F3 大屏三维场景(H11 §一 F3,GAP-16 解除):
//        · Three.js 本地副本(fetch_libs.sh 预取,禁 CDN,ARC-5);
//        · 零 addons 自研轨道相机(拖拽=环绕、滚轮=距离、双击=回 home);
//        · 低多边形:楼宇盒体 + 设备小盒,平光材质,无贴图(集成显卡基线 02-D1);
//        · 数据面:/api/layout 一次成景;WS 全量帧(壳脚本单连接,经
//          f3d-frame 事件转发)驱动设备状态着色与告警脉冲;
//        · 降级阶梯承接(R-F3D-1):tier=full 开阴影 → no_shadow 关阴影 →
//          low_tex 降 pixelRatio → low_push 再降(推送频率服务端已放大);
//        · fps 回报走壳脚本 __reportFps(单 WS,端到端闭环);
//        · 生命周期:pagehide 停 rAF(H11 §四.8)。
// @author 港电实验室平台组
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import * as THREE from '/static/vendor/three.module.min.js';

const COLOR = {
  ground: 0x121d31, zone: 0x18263f, buildingTop: 0x27395a, buildingSide: 0x1f3049,
  online: 0x18c2b8, offline: 0xe0453a, silent: 0x8a97ab, unknown: 0x5a6b84,
  alarmRing: 0xe0453a, edge: 0x33415e,
};
const TIER_PIXEL_RATIO = { full: null, no_shadow: null, low_tex: 1, low_push: 0.75 };

/** @brief 状态 → 颜色(未知状态归 unknown 桶,不误触发红色) */
function statusColor(s) {
  if (s === 'online') return COLOR.online;
  if (s === 'offline') return COLOR.offline;
  if (s === 'silent') return COLOR.silent;
  return COLOR.unknown;
}

/** 自研轨道相机(零 addons):球坐标环绕 + 滚轮缩放 + 惯性无、实现最小面 */
class OrbitCamera {
  constructor(camera, dom, home) {
    this.camera = camera;
    this.dom = dom;
    this.target = new THREE.Vector3(...(home.target || [0, 0, 0]));
    this.radius = home.radius || 190;
    this.theta = ((home.theta ?? 35) * Math.PI) / 180;   // 水平角
    this.elev = ((home.elev ?? 55) * Math.PI) / 180;     // 仰角
    this.home = { radius: this.radius, theta: this.theta, elev: this.elev };
    this._drag = null;
    dom.addEventListener('pointerdown', (e) => {
      this._drag = { x: e.clientX, y: e.clientY };
      dom.setPointerCapture(e.pointerId);
    });
    dom.addEventListener('pointermove', (e) => {
      if (!this._drag) return;
      this.theta -= (e.clientX - this._drag.x) * 0.005;
      this.elev = Math.min(Math.max(this.elev + (e.clientY - this._drag.y) * 0.005, 0.15), 1.45);
      this._drag = { x: e.clientX, y: e.clientY };
    });
    dom.addEventListener('pointerup', () => (this._drag = null));
    dom.addEventListener('pointercancel', () => (this._drag = null));
    dom.addEventListener(
      'wheel',
      (e) => {
        e.preventDefault();
        this.radius = Math.min(Math.max(this.radius * (e.deltaY > 0 ? 1.08 : 0.92), 40), 500);
      },
      { passive: false },
    );
    dom.addEventListener('dblclick', () => {
      this.radius = this.home.radius;
      this.theta = this.home.theta;
      this.elev = this.home.elev;
    });
    this.apply();
  }
  apply() {
    const r = this.radius, cosE = Math.cos(this.elev);
    this.camera.position.set(
      this.target.x + r * cosE * Math.sin(this.theta),
      this.target.y + r * Math.sin(this.elev),
      this.target.z + r * cosE * Math.cos(this.theta),
    );
    this.camera.lookAt(this.target);
  }
}

/** @brief 楼内设备网格布点:按栋内序号排两列,落在楼宇脚下前沿 */
function indoorSlot(building, index) {
  const w = building.size.w, d = building.size.d;
  const cols = Math.max(2, Math.ceil(Math.sqrt(building.devices.length)));
  const col = index % cols, row = Math.floor(index / cols);
  return [
    building.offset.dx - w / 2 + ((col + 0.5) * w) / cols,
    1.2,
    building.offset.dz + d / 2 + 4 + row * 4,
  ];
}

async function boot() {
  const host = document.getElementById('scene');
  if (!host || typeof WebGLRenderingContext === 'undefined') return;
  let layout;
  try {
    const resp = await fetch('/api/layout', { headers: { Accept: 'application/json' } });
    if (!resp.ok) throw new Error(String(resp.status));
    layout = await resp.json();
  } catch (err) {
    host.textContent = '三维场景初始化失败(布局接口不可达),数据面板不受影响';
    return;
  }
  const doc = layout.layout || layout;

  // ---- 渲染器与场景 ----
  host.textContent = '';
  host.style.display = 'block';
  const renderer = new THREE.WebGLRenderer({ antialias: false, powerPreference: 'low-power' });
  const width = host.clientWidth || 800, height = Math.max(host.clientHeight, 340);
  renderer.setSize(width, height);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  renderer.shadowMap.enabled = true;
  host.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0b1220);
  scene.fog = new THREE.Fog(0x0b1220, 260, 520);
  const camera = new THREE.PerspectiveCamera(50, width / height, 1, 1000);
  const orbit = new OrbitCamera(camera, renderer.domElement, doc.home || {});

  scene.add(new THREE.AmbientLight(0xffffff, 0.55));
  const sun = new THREE.DirectionalLight(0xffffff, 0.9);
  sun.position.set(120, 180, 80);
  sun.castShadow = true;
  sun.shadow.mapSize.set(1024, 1024);
  scene.add(sun);

  const ground = new THREE.Mesh(
    new THREE.PlaneGeometry(420, 320),
    new THREE.MeshLambertMaterial({ color: COLOR.ground }),
  );
  ground.rotation.x = -Math.PI / 2;
  ground.receiveShadow = true;
  scene.add(ground);

  // ---- 场区 / 楼宇 / 设备(低多边形盒体) ----
  const deviceMeshes = new Map(); // id → {mesh, mat, ring}
  const zone = (doc.zones || [])[0];
  if (zone) {
    const pad = new THREE.Mesh(
      new THREE.PlaneGeometry(300, 220),
      new THREE.MeshLambertMaterial({ color: COLOR.zone }),
    );
    pad.rotation.x = -Math.PI / 2;
    pad.position.y = 0.1;
    scene.add(pad);
    for (const b of zone.buildings || []) {
      const h = 14 + (b.size.w % 7);
      const box = new THREE.Mesh(
        new THREE.BoxGeometry(b.size.w, h, b.size.d),
        new THREE.MeshLambertMaterial({ color: COLOR.buildingSide }),
      );
      box.position.set(b.offset.dx, h / 2 + 0.1, b.offset.dz);
      box.castShadow = true;
      box.receiveShadow = true;
      scene.add(box);
      const edge = new THREE.LineSegments(
        new THREE.EdgesGeometry(box.geometry),
        new THREE.LineBasicMaterial({ color: COLOR.edge }),
      );
      edge.position.copy(box.position);
      scene.add(edge);
      (b.devices || []).forEach((dev, i) => {
        addDevice(dev, indoorSlot(b, i));
      });
    }
  }
  for (const dev of doc.outdoor || []) {
    const pos = dev.pos && (dev.pos[0] || dev.pos[2]) ? [dev.pos[0], 1.2, dev.pos[2]] : [0, 1.2, 95];
    addDevice(dev, pos);
  }

  function addDevice(dev, pos) {
    const mat = new THREE.MeshLambertMaterial({ color: COLOR.unknown });
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(2.4, 2.4, 2.4), mat);
    mesh.position.set(pos[0], pos[1], pos[2]);
    mesh.castShadow = true;
    scene.add(mesh);
    const ring = new THREE.Mesh(
      new THREE.RingGeometry(2.2, 3.0, 16),
      new THREE.MeshBasicMaterial({ color: COLOR.alarmRing, transparent: true, opacity: 0 }),
    );
    ring.rotation.x = -Math.PI / 2;
    ring.position.set(pos[0], 0.25, pos[2]);
    scene.add(ring);
    deviceMeshes.set(dev.id, { mesh, mat, ring, status: 'unknown' });
  }

  // ---- 帧驱动:状态着色 + 告警脉冲 + 降级档位 ----
  let tier = 'full';
  function applyTier(next) {
    if (next === tier) return;
    tier = next;
    renderer.shadowMap.enabled = tier === 'full';
    const ratio = TIER_PIXEL_RATIO[tier];
    renderer.setPixelRatio(ratio || Math.min(window.devicePixelRatio || 1, 1.5));
    scene.traverse((o) => {
      if (o.material) o.material.needsUpdate = true;
    });
  }
  function applyFrame(frame) {
    applyTier(frame.tier);
    for (const dev of frame.devices || []) {
      const entry = deviceMeshes.get(dev.id);
      if (!entry) continue;
      entry.status = dev.s;
      entry.mat.color.setHex(statusColor(dev.s));
    }
  }
  window.addEventListener('f3d-frame', (e) => applyFrame(e.detail));
  if (window.__lastFrame) applyFrame(window.__lastFrame);

  // ---- 渲染循环(rAF;pagehide 停,H11 §四.8) ----
  let running = true;
  window.addEventListener('pagehide', () => (running = false));
  const clock = new THREE.Clock();
  function loop() {
    if (!running) return;
    const t = clock.getElapsedTime();
    const pulse = (Math.sin(t * 5) + 1) / 2;
    for (const entry of deviceMeshes.values()) {
      entry.ring.material.opacity = entry.status === 'offline' ? 0.25 + 0.55 * pulse : 0;
    }
    orbit.apply();
    renderer.render(scene, camera);
    requestAnimationFrame(loop);
  }
  requestAnimationFrame(loop);

  window.addEventListener('resize', () => {
    const w2 = host.clientWidth || width;
    renderer.setSize(w2, height);
    camera.aspect = w2 / height;
    camera.updateProjectionMatrix();
  });
}

boot();
