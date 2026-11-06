const $ = (id) => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";

let state = null;
let limits = null;
let gaugeBuilt = false;
let limitsApplied = false;
let chartWin = 10;
let chartPts = 300;

const fmtInt = (n) => Math.round(n).toLocaleString("en-US");

function cyclesIndex(value) {
  return Math.round((value - limits.cycles[0]) / limits.cycles[2]);
}
function cyclesValue(index) {
  return limits.cycles[0] + index * limits.cycles[2];
}
function voltageIndex(v) {
  let best = 0;
  for (let i = 0; i < limits.voltages.length; i++) {
    if (Math.abs(limits.voltages[i] - v) < Math.abs(limits.voltages[best] - v)) best = i;
  }
  return best;
}
function snap(v, min, step) {
  return min + Math.round((v - min) / step) * step;
}
function clamp(v, lo, hi) {
  return Math.min(hi, Math.max(lo, v));
}

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  const r = await fetch(path, opts);
  return r.json();
}

async function poll() {
  try {
    const r = await api("/api/state?win=" + chartWin + "&pts=" + chartPts);
    if (r.ok) {
      state = r.state;
      limits = state.limits;
      if (!limitsApplied) applyLimits();
      render();
    }
  } catch (e) {
    showError("dashboard server unreachable: " + e.message);
  }
  setTimeout(poll, 200);
}

function showError(msg) {
  const box = $("error-box");
  if (msg) {
    box.textContent = msg;
    box.hidden = false;
  } else {
    box.hidden = true;
  }
}

function applyLimits() {
  limitsApplied = true;
  const setRange = (id, lo, hi, step) => {
    for (const suffix of ["", "-num"]) {
      const el = $(id + suffix);
      el.min = lo; el.max = hi; el.step = step;
    }
  };
  setRange("dwell", limits.dwell[0], limits.dwell[1], 0.5);
  setRange("min", 0, limits.stroke_mm - 1, 1);
  setRange("max", 1, limits.stroke_mm, 1);
  $("cycles").max = (limits.cycles[1] - limits.cycles[0]) / limits.cycles[2];
  setRange("cycles-num", limits.cycles[0], limits.cycles[1], limits.cycles[2]);
  $("voltage").max = limits.voltages.length - 1;
  setRange("goal", limits.goal_kg[0], limits.goal_kg[1], limits.goal_kg[2]);
  $("goal-num").min = limits.goal_kg[0];
  $("goal-num").max = limits.goal_kg[1];
  $("goal-num").step = limits.goal_kg[2];
  setRange("faildrop", limits.fail_drop_pct[0], limits.fail_drop_pct[1], 1);
}

function readControls() {
  return {
    dwell: parseFloat($("dwell").value),
    min_mm: parseFloat($("min").value),
    max_mm: parseFloat($("max").value),
    target: cyclesValue(parseInt($("cycles").value, 10)),
    voltage: limits.voltages[parseInt($("voltage").value, 10)],
    mode: $("mode").value,
    load_goal_kg: parseFloat($("goal").value),
    fail_drop_pct: parseFloat($("faildrop").value),
  };
}

async function pushConfig() {
  const r = await api("/api/config", { config: readControls() });
  if (!r.ok) showError(r.error);
}

function updateOutput(id, text) {
  $(id + "-out").textContent = text;
}

function wireControls() {
  const fmts = {
    dwell: (v) => v + " s",
    min: (v) => v + " mm",
    max: (v) => v + " mm",
    cycles: (v) => fmtInt(v),
  };
  const steps = { dwell: 0.5, min: 1, max: 1, cycles: null };
  for (const id of ["cycles", "min", "max", "dwell"]) {
    const el = $(id);
    el.addEventListener("input", () => {
      const step = id === "cycles" ? 1 : steps[id];
      const v = snap(parseFloat(el.value), parseFloat(el.min), step);
      el.value = v;
      if (id === "cycles") $(id + "-num").value = cyclesValue(v);
      else $(id + "-num").value = v;
      updateOutput(id, fmts[id](id === "cycles" ? cyclesValue(v) : v));
    });
    el.addEventListener("change", pushConfig);
    $(id + "-num").addEventListener("change", () => {
      const num = $(id + "-num");
      let v = parseFloat(num.value);
      if (isNaN(v)) v = parseFloat(num.min);
      v = clamp(v, parseFloat(num.min), parseFloat(num.max));
      if (id === "cycles") {
        v = snap(v, limits.cycles[0], limits.cycles[2]);
        num.value = v;
        el.value = cyclesIndex(v);
        updateOutput(id, fmts[id](v));
      } else {
        v = snap(v, parseFloat(num.min), steps[id]);
        num.value = v;
        el.value = v;
        updateOutput(id, fmts[id](v));
      }
      pushConfig();
    });
  }
  const volt = $("voltage");
  volt.addEventListener("input", () => {
    volt.value = snap(parseFloat(volt.value), parseFloat(volt.min), 1);
    updateOutput("voltage", limits.voltages[parseInt(volt.value, 10)] + " V");
  });
  volt.addEventListener("change", pushConfig);
  const goal = $("goal");
  goal.addEventListener("input", () => {
    const v = snap(parseFloat(goal.value), parseFloat(goal.min), 0.2);
    goal.value = v;
    $("goal-num").value = v;
    updateOutput("goal", v.toFixed(1) + " kg");
  });
  goal.addEventListener("change", pushConfig);
  $("goal-num").addEventListener("change", () => {
    const num = $("goal-num");
    let v = parseFloat(num.value);
    if (isNaN(v)) v = parseFloat(num.min);
    v = snap(clamp(v, parseFloat(num.min), parseFloat(num.max)), parseFloat(num.min), 0.2);
    num.value = v;
    goal.value = v;
    updateOutput("goal", v.toFixed(1) + " kg");
    pushConfig();
  });
  const faildrop = $("faildrop");
  faildrop.addEventListener("input", () => {
    const v = snap(parseFloat(faildrop.value), parseFloat(faildrop.min), 1);
    faildrop.value = v;
    $("faildrop-num").value = v;
    updateOutput("faildrop", v + " %");
  });
  faildrop.addEventListener("change", pushConfig);
  $("faildrop-num").addEventListener("change", () => {
    const num = $("faildrop-num");
    let v = parseFloat(num.value);
    if (isNaN(v)) v = parseFloat(num.min);
    v = snap(clamp(v, parseFloat(num.min), parseFloat(num.max)), parseFloat(num.min), 1);
    num.value = v;
    faildrop.value = v;
    updateOutput("faildrop", v + " %");
    pushConfig();
  });
  $("mode").addEventListener("change", () => {
    $("goal-row").hidden = $("mode").value !== "2b";
    $("faildrop-row").hidden = $("mode").value === "1";
    pushConfig();
  });
  $("btn-tare").addEventListener("click", async () => {
    const r = await api("/api/tare", {});
    if (!r.ok) showError(r.error);
  });
  $("chart-win").addEventListener("change", () => { chartWin = parseInt($("chart-win").value, 10); });
  $("chart-pts").addEventListener("change", () => { chartPts = parseInt($("chart-pts").value, 10); });
  $("btn-start").addEventListener("click", async () => {
    const r = await api("/api/start", {});
    if (!r.ok) showError(r.error);
  });
  $("btn-stop").addEventListener("click", async () => {
    const r = await api("/api/stop", {});
    if (!r.ok) showError(r.error);
  });
  $("btn-reset").addEventListener("click", async () => {
    const r = await api("/api/reset", {});
    if (!r.ok) showError(r.error);
  });
  $("btn-reconnect").addEventListener("click", async () => {
    const r = await api("/api/reconnect", {});
    if (!r.ok) showError(r.error);
  });
  $("btn-quit").addEventListener("click", async () => {
    showError(null);
    document.body.innerHTML = "<div style='padding:40px;font-size:24px'>L16 Cycle Tester closed. The controller has been released — you can now close this tab or start MAX.</div>";
    await api("/api/quit", {}).catch(() => {});
  });
}

function xForMm(mm) {
  const left = 40, right = 520;
  return left + (mm / limits.stroke_mm) * (right - left);
}

function buildGauge() {
  const svg = $("gauge");
  svg.textContent = "";
  const axis = document.createElementNS(SVG_NS, "line");
  axis.setAttribute("x1", 30); axis.setAttribute("y1", 40);
  axis.setAttribute("x2", 530); axis.setAttribute("y2", 40);
  axis.setAttribute("stroke", "#8fa1b8");
  axis.setAttribute("stroke-width", "3");
  svg.appendChild(axis);
  for (let mm = 0; mm <= limits.stroke_mm; mm += 5) {
    const x = xForMm(mm);
    const tick = document.createElementNS(SVG_NS, "line");
    tick.setAttribute("x1", x); tick.setAttribute("y1", 32);
    tick.setAttribute("x2", x); tick.setAttribute("y2", 48);
    tick.setAttribute("stroke", "#8fa1b8");
    tick.setAttribute("stroke-width", mm % 10 === 0 ? "3" : "1.5");
    svg.appendChild(tick);
    const label = document.createElementNS(SVG_NS, "text");
    label.setAttribute("x", x); label.setAttribute("y", 72);
    label.setAttribute("text-anchor", "middle");
    label.setAttribute("fill", "#8fa1b8");
    label.setAttribute("font-size", "14");
    label.textContent = mm;
    svg.appendChild(label);
  }
  const band = document.createElementNS(SVG_NS, "rect");
  band.setAttribute("id", "gauge-band");
  band.setAttribute("y", 30); band.setAttribute("height", 20);
  band.setAttribute("rx", 4);
  band.setAttribute("fill", "rgba(56,189,248,0.25)");
  svg.appendChild(band);
  const marker = document.createElementNS(SVG_NS, "line");
  marker.setAttribute("id", "gauge-marker");
  marker.setAttribute("y1", 8); marker.setAttribute("y2", 72);
  marker.setAttribute("stroke", "#38bdf8");
  marker.setAttribute("stroke-width", "5");
  marker.setAttribute("stroke-linecap", "round");
  svg.appendChild(marker);
  const dot = document.createElementNS(SVG_NS, "circle");
  dot.setAttribute("id", "gauge-dot");
  dot.setAttribute("cy", 40); dot.setAttribute("r", "9");
  dot.setAttribute("fill", "#38bdf8");
  dot.setAttribute("stroke", "#10151c");
  dot.setAttribute("stroke-width", "3");
  svg.appendChild(dot);
  gaugeBuilt = true;
}

function updateGauge(config, positionMm) {
  const band = $("gauge-band");
  const xMin = xForMm(config.min_mm);
  const xMax = xForMm(config.max_mm);
  band.setAttribute("x", xMin);
  band.setAttribute("width", Math.max(2, xMax - xMin));
  const x = xForMm(positionMm);
  const marker = $("gauge-marker");
  marker.setAttribute("x1", x);
  marker.setAttribute("x2", x);
  $("gauge-dot").setAttribute("cx", x);
}

function render() {
  const s = state;
  const c = s.config;
  const running = s.status === "running";

  const dev = $("device-pill");
  if (s.demo) {
    dev.textContent = "DEMO - simulated actuator";
    dev.className = "pill run";
  } else if (s.device.connected) {
    dev.textContent = "Connected: " + s.device.name;
    dev.className = "pill ok";
  } else {
    dev.textContent = "CONTROLLER NOT CONNECTED";
    dev.className = "pill bad";
  }
  $("btn-reconnect").hidden = s.demo || s.device.connected;

  const statusPill = $("status-pill");
  statusPill.textContent = s.status.toUpperCase();
  statusPill.className = "pill " + (running ? "run" : s.status === "done" ? "done" : s.status === "stopped" ? "bad" : "");
  const devError = s.device.connected ? null : s.device.error;
  showError(s.error || devError);
  if (!s.device.connected && s.device.error_owner) {
    showError((devError ? devError + " " : "") + "Controller currently held by: " + s.device.error_owner);
  }

  const activeEl = document.activeElement;
  const ids = ["cycles", "min", "max", "dwell"];
  if (!running) {
    for (const id of ids) {
      const el = $(id);
      const num = $(id + "-num");
      if (activeEl !== el && activeEl !== num) {
        if (id === "cycles") {
          el.value = String(cyclesIndex(c.target));
          num.value = c.target;
        } else {
          const key = id === "min" ? "min_mm" : id === "max" ? "max_mm" : id;
          el.value = String(c[key]);
          num.value = c[key];
        }
      }
      el.disabled = false;
      num.disabled = false;
    }
    const volt = $("voltage");
    if (activeEl !== volt) volt.value = String(voltageIndex(s.voltage));
    volt.disabled = false;
    const modeEl = $("mode");
    if (activeEl !== modeEl) modeEl.value = c.mode;
    modeEl.disabled = false;
    $("goal-row").hidden = c.mode !== "2b";
    $("faildrop-row").hidden = c.mode === "1";
    const goal = $("goal");
    if (activeEl !== goal && activeEl !== $("goal-num")) {
      goal.value = c.load_goal_kg;
      $("goal-num").value = c.load_goal_kg;
    }
    goal.disabled = false;
    $("goal-num").disabled = false;
    const fd = $("faildrop");
    if (activeEl !== fd && activeEl !== $("faildrop-num")) {
      fd.value = c.fail_drop_pct;
      $("faildrop-num").value = c.fail_drop_pct;
    }
    fd.disabled = false;
    $("faildrop-num").disabled = false;
  } else {
    for (const id of ids) {
      $(id).disabled = true;
      $(id + "-num").disabled = true;
    }
    $("voltage").disabled = true;
    $("mode").disabled = true;
    $("goal").disabled = true;
    $("goal-num").disabled = true;
    $("faildrop").disabled = true;
    $("faildrop-num").disabled = true;
  }

  const maxLocked = c.mode === "2b";
  $("max").disabled = running || maxLocked;
  $("max-num").disabled = running || maxLocked;

  const load = s.load;
  const loadPill = $("load-pill");
  if (s.demo) {
    loadPill.textContent = "DEMO - simulated load cell";
    loadPill.className = "pill run";
  } else if (load.connected) {
    loadPill.textContent = "Load cell: " + load.name;
    loadPill.className = "pill ok";
  } else {
    loadPill.textContent = "LOAD CELL NOT CONNECTED";
    loadPill.className = "pill bad";
  }
  $("load-kg").textContent = load.kg !== null ? load.kg.toFixed(3) : "--";
  $("load-prev-peak").textContent = load.prev_cycle_peak_kg != null ? load.prev_cycle_peak_kg.toFixed(3) : "–";
  $("btn-tare").disabled = !load.connected;
  drawChart(load.history || [], c.mode === "2b" ? c.load_goal_kg : null);
  $("run-file").textContent = s.run_file ? "Logging to " + s.run_file : "";

  $("goal-out").textContent = c.load_goal_kg.toFixed(1) + " kg";
  $("faildrop-out").textContent = c.fail_drop_pct + " %";
  $("dwell-out").textContent = c.dwell + " s";
  $("min-out").textContent = c.min_mm + " mm";
  $("max-out").textContent = c.max_mm + " mm";
  $("cycles-out").textContent = fmtInt(c.target);
  $("voltage-out").textContent = s.voltage + " V";

  $("count").textContent = fmtInt(s.count);
  $("target").textContent = fmtInt(c.target);
  const pct = c.target > 0 ? Math.min(100, (s.count / c.target) * 100) : 0;
  $("progress-bar").style.width = pct + "%";

  $("pos").textContent = Math.round(s.position_mm);
  $("range-out").textContent = c.min_mm + " – " + c.max_mm + " mm";

  if (!gaugeBuilt && limits) buildGauge();
  if (gaugeBuilt) updateGauge(c, s.position_mm);

  const canMove = s.device.connected;
  const loadOnlyOk = s.load.connected && c.mode === "1";
  $("btn-start").disabled = running || (!canMove && !loadOnlyOk);
  $("btn-stop").disabled = !running;
}

function drawChart(history, goalKg) {
  const canvas = $("load-chart");
  const ctx = canvas.getContext("2d");
  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = "#0c1118";
  ctx.fillRect(0, 0, W, H);
  const padL = 44, padR = 10, padT = 10, padB = 22;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  let maxV = 0.5;
  if (history.length) {
    for (const [, v] of history) maxV = Math.max(maxV, v);
  }
  if (goalKg) maxV = Math.max(maxV, goalKg * 1.2);
  maxV *= 1.1;
  ctx.strokeStyle = "#2a3648";
  ctx.fillStyle = "#8fa1b8";
  ctx.font = "11px system-ui, sans-serif";
  ctx.textAlign = "right";
  for (let i = 0; i <= 4; i++) {
    const v = (maxV / 4) * i;
    const y = padT + plotH - (v / maxV) * plotH;
    ctx.beginPath();
    ctx.moveTo(padL, y);
    ctx.lineTo(W - padR, y);
    ctx.stroke();
    ctx.fillText(v.toFixed(1), padL - 6, y + 4);
  }
  ctx.textAlign = "center";
  const wLabel = chartWin >= 60 ? "last " + chartWin / 60 + " min" : "last " + chartWin + " s";
  ctx.fillText("time (" + wLabel + ") →", padL + plotW / 2, H - 6);
  if (history.length < 2) {
    ctx.fillStyle = "#8fa1b8";
    ctx.textAlign = "center";
    ctx.fillText("waiting for data…", padL + plotW / 2, padT + plotH / 2);
    return;
  }
  const t0 = history[0][0];
  const t1 = history[history.length - 1][0];
  const span = Math.max(t1 - t0, 0.001);
  if (goalKg) {
    const gy = padT + plotH - (goalKg / maxV) * plotH;
    ctx.strokeStyle = "#fbbf24";
    ctx.setLineDash([5, 4]);
    ctx.beginPath();
    ctx.moveTo(padL, gy);
    ctx.lineTo(W - padR, gy);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "#fbbf24";
    ctx.textAlign = "left";
    ctx.fillText("goal " + goalKg.toFixed(1) + " kg", padL + 4, gy - 4);
  }
  const n = history.length;
  if (n > 300) {
    ctx.strokeStyle = "#38bdf8";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    for (let i = 0; i < n; i++) {
      const [t, v] = history[i];
      const x = padL + ((t - t0) / span) * plotW;
      const y = padT + plotH - (Math.max(v, 0) / maxV) * plotH;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
  } else {
    const bw = Math.max(1, plotW / n - 1);
    ctx.fillStyle = "#38bdf8";
    for (let i = 0; i < n; i++) {
      const [t, v] = history[i];
      const x = padL + ((t - t0) / span) * plotW;
      const h = (Math.max(v, 0) / maxV) * plotH;
      ctx.fillRect(x, padT + plotH - h, bw, h);
    }
  }
}

wireControls();
poll();
