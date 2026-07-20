# -*- coding: utf-8 -*-
"""
@file    page.py
@brief   大屏 `/` 数据壳页面(L03 §2 语义等价的无 Three.js 形态):安灯条/
         KPI 四枚/WS 芯片/档位指示/事件流/告警 HUD/「布局已更新」芯片;
         内嵌 WS 客户端按秒回报 fps(R-F3D-1 端到端闭环)。三维场景本体
         (Three.js)随里程碑 9 填入 #scene(GAP-16 部分解除)。
         站点名等动态文本:服务端 html.escape、客户端一律 textContent,
         杜绝编辑权放大为公开页 XSS。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import html

_SCRIPT = """
(function () {
  var proto = location.protocol === "https:" ? "wss://" : "ws://";
  var ws = null, retry = 500;
  var frames = 0, lastReport = performance.now();
  function byId(id) { return document.getElementById(id); }
  function setText(id, value) { byId(id).textContent = String(value); }
  function applyFrame(frame) {
    setText("site-name", frame.site);
    setText("kpi-total", frame.kpi.total);
    setText("kpi-online", frame.kpi.online);
    setText("kpi-alarm", frame.kpi.alarm);
    setText("kpi-offline", frame.kpi.offline);
    setText("tier-chip", "档位:" + frame.tier);
    var alarm = frame.kpi.alarm > 0;
    byId("andon").className = alarm ? "andon alarm" : "andon ok";
    setText("alarm-count", frame.alarms.counts.active);
    var list = byId("device-list");
    list.textContent = "";
    frame.devices.forEach(function (device) {
      var item = document.createElement("li");
      item.className = "dev " + device.s;
      item.dataset.id = device.id;
      item.textContent = device.n + " · " + device.b + " · " + device.s;
      list.appendChild(item);
    });
    var events = byId("event-list");
    events.textContent = "";
    frame.events.forEach(function (entry) {
      var item = document.createElement("li");
      item.textContent = entry.ts.slice(11, 19) + " " + entry.kind + " " +
        entry.device + " " + entry.from + "→" + entry.to;
      events.appendChild(item);
    });
    if (window.__dataRev !== undefined && frame.data_rev !== window.__dataRev) {
      byId("layout-chip").hidden = false;
    }
    window.__dataRev = frame.data_rev;
    window.__lastFrame = frame;
  }
  function connect() {
    ws = new WebSocket(proto + location.host + "/ws");
    ws.onopen = function () { retry = 500; setText("ws-chip", "已连接"); };
    ws.onmessage = function (event) { applyFrame(JSON.parse(event.data)); };
    ws.onclose = function () {
      setText("ws-chip", "断线重连");
      setTimeout(connect, retry);
      retry = Math.min(retry * 2, 8000);      // 指数退避(L03 §7)
    };
  }
  window.__reportFps = function (value) {   // 测试/低端机可直接驱动
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify({ type: "fps", value: value }));
    }
  };
  function raf() {
    frames += 1;
    var now = performance.now();
    if (now - lastReport >= 1000) {
      window.__reportFps(frames * 1000 / (now - lastReport));
      frames = 0;
      lastReport = now;
    }
    requestAnimationFrame(raf);
  }
  setInterval(function () {
    setText("clock-chip", new Date().toTimeString().slice(0, 8));
  }, 1000);
  connect();
  requestAnimationFrame(raf);
})();
"""

_STYLE = """
body { margin: 0; font: 14px/1.5 system-ui, sans-serif; background: #0b1220;
       color: #dbe4f0; }
.andon { height: 6px; }
.andon.ok { background: #18c2b8; }
.andon.alarm { background: #e0453a; }
header { display: flex; gap: 12px; align-items: center; padding: 8px 16px; }
.chip { padding: 2px 10px; border-radius: 10px; background: #1c2942;
        font-size: 12px; }
[hidden] { display: none !important; }
main { display: grid; grid-template-columns: 280px 1fr 300px; gap: 12px;
       padding: 0 16px; }
ul { list-style: none; margin: 0; padding: 0; max-height: 60vh;
     overflow: auto; }
.dev.offline { color: #e0453a; }
#alarm-hud { border: 1px solid #33415e; border-radius: 8px; padding: 8px; }
#scene { min-height: 30vh; border: 1px dashed #33415e; border-radius: 8px;
         display: flex; align-items: center; justify-content: center; }
"""


def render_big_screen(site_name: str, version: str, min_icon_px: int) -> str:
    """@brief 渲染大屏数据壳(站点名 html.escape;window.F3D_VER 注入,L03 §8)"""
    safe_site = html.escape(site_name)
    return f"""<!doctype html><html lang="zh-CN"><head>
<meta charset="utf-8"><title>{safe_site} · 三维物联监控大屏</title>
<style>{_STYLE}</style></head><body>
<div id="andon" class="andon ok"></div>
<header>
  <h2 id="site-name">{safe_site}</h2>
  <span class="chip">总数 <b id="kpi-total">--</b></span>
  <span class="chip">在线 <b id="kpi-online">--</b></span>
  <span class="chip">告警 <b id="kpi-alarm">--</b></span>
  <span class="chip">离线 <b id="kpi-offline">--</b></span>
  <span class="chip" id="fps-chip">-- fps</span>
  <span class="chip" id="tier-chip">档位:full</span>
  <span class="chip" id="ws-chip">连接中</span>
  <button class="chip" id="layout-chip" hidden
          onclick="location.reload()">布局已更新 · 刷新生效</button>
  <span class="chip" id="clock-chip">--:--:--</span>
</header>
<main>
  <section><h4>事件流</h4><ul id="event-list"></ul></section>
  <section>
    <div id="scene" data-min-icon-px="{min_icon_px}">
      正在加载三维场景…(Three.js 场景随里程碑 9 交付,GAP-16)</div>
    <h4>设备</h4><ul id="device-list"></ul>
  </section>
  <section id="alarm-hud"><h4>离线告警
    <span class="chip" id="alarm-count">0</span></h4></section>
</main>
<script>window.F3D_VER = "{version}";</script>
<script>{_SCRIPT}</script>
</body></html>"""
