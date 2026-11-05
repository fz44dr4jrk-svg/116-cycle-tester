const $ = (id) => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";

let state = null;
let limits = null;
let gaugeBuilt = false;
let limitsApplied = false;

const fmtInt = (n) => Math.round(n).toLocaleString("en-US");

function cyclesIndex(value) {
  return Math.round((value - limits.cycles[0]) / limits.cycles[2]);
}
function cyclesValue(index) {
  return limits.cycles[0] + index * limits.cycles[2];
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
    const r = await api("/api/state");
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
  setRange("speed", limits.speed[0], limits.speed[1], 1);
  setRange("dwell", limits.dwell[0], limits.dwell[1], 0.5);
  setRange("min", 0, limits.stroke_mm - 1, 1);
  setRange("max", 1, limits.stroke_mm, 1);
  $("cycles").max = (limits.cycles[1] - limits.cycles[0]) / limits.cycles[2];
  setRange("cycles-num", limits.cycles[0], limits.cycles[1], limits.cycles[2]);
}

function readControls() {
  const active = document.querySelector(".volt-btn.active");
  return {
    speed: parseFloat($("speed").value),
    dwell: parseFloat($("dwell").value),
    min_mm: parseFloat($("min").value),
    max_mm: parseFloat($("max").value),
    target: cyclesValue(parseInt($("cycles").value, 10)),
    voltage: active ? parseFloat(active.dataset.v) : 6.0,
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
    speed: (v) => v + " %",
    dwell: (v) => v + " s",
    min: (v) => v + " mm",
    max: (v) => v + " mm",
    cycles: (v) => fmtInt(v),
  };
  const steps = { speed: 1, dwell: 0.5, min: 1, max: 1, cycles: null };
  for (const id of ["speed", "dwell", "min", "max", "cycles"]) {
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
  document.querySelectorAll(".volt-btn").forEach((b) => {
    b.addEventListener("click", () => {
      document.querySelectorAll(".volt-btn").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      pushConfig();
    });
  });
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

function yForMm(mm) {
  const top = 40, bottom = 440;
  return bottom - (mm / limits.stroke_mm) * (bottom - top);
}

function buildGauge() {
  const svg = $("gauge");
  svg.textContent = "";
  const axis = document.createElementNS(SVG_NS, "line");
  axis.setAttribute("x1", 100); axis.setAttribute("y1", 30);
  axis.setAttribute("x2", 100); axis.setAttribute("y2", 450);
  axis.setAttribute("stroke", "#8fa1b8");
  axis.setAttribute("stroke-width", "3");
  svg.appendChild(axis);
  for (let mm = 0; mm <= limits.stroke_mm; mm += 5) {
    const y = yForMm(mm);
    const tick = document.createElementNS(SVG_NS, "line");
    tick.setAttribute("x1", 92); tick.setAttribute("y1", y);
    tick.setAttribute("x2", 108); tick.setAttribute("y2", y);
    tick.setAttribute("stroke", "#8fa1b8");
    tick.setAttribute("stroke-width", mm % 10 === 0 ? "3" : "1.5");
    svg.appendChild(tick);
    const label = document.createElementNS(SVG_NS, "text");
    label.setAttribute("x", 84); label.setAttribute("y", y + 5);
    label.setAttribute("text-anchor", "end");
    label.setAttribute("fill", "#8fa1b8");
    label.setAttribute("font-size", "14");
    label.textContent = mm;
    svg.appendChild(label);
  }
  const band = document.createElementNS(SVG_NS, "rect");
  band.setAttribute("id", "gauge-band");
  band.setAttribute("x", 92); band.setAttribute("width", 16);
  band.setAttribute("rx", 4);
  band.setAttribute("fill", "rgba(56,189,248,0.25)");
  svg.appendChild(band);
  const marker = document.createElementNS(SVG_NS, "line");
  marker.setAttribute("id", "gauge-marker");
  marker.setAttribute("x1", 40); marker.setAttribute("x2", 145);
  marker.setAttribute("stroke", "#38bdf8");
  marker.setAttribute("stroke-width", "5");
  marker.setAttribute("stroke-linecap", "round");
  svg.appendChild(marker);
  const dot = document.createElementNS(SVG_NS, "circle");
  dot.setAttribute("id", "gauge-dot");
  dot.setAttribute("cx", 100); dot.setAttribute("r", "9");
  dot.setAttribute("fill", "#38bdf8");
  dot.setAttribute("stroke", "#10151c");
  dot.setAttribute("stroke-width", "3");
  svg.appendChild(dot);
  gaugeBuilt = true;
}

function updateGauge(config, positionMm) {
  const band = $("gauge-band");
  const yMax = yForMm(config.max_mm);
  const yMin = yForMm(config.min_mm);
  band.setAttribute("y", yMax);
  band.setAttribute("height", Math.max(2, yMin - yMax));
  const y = yForMm(positionMm);
  const marker = $("gauge-marker");
  marker.setAttribute("y1", y);
  marker.setAttribute("y2", y);
  const dot = $("gauge-dot");
  dot.setAttribute("cy", y);
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
  const ids = ["speed", "dwell", "min", "max", "cycles"];
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
    document.querySelectorAll(".volt-btn").forEach((b) => {
      b.disabled = false;
      b.classList.toggle("active", parseFloat(b.dataset.v) === s.voltage);
    });
  } else {
    for (const id of ids) {
      $(id).disabled = true;
      $(id + "-num").disabled = true;
    }
    document.querySelectorAll(".volt-btn").forEach((b) => (b.disabled = true));
  }

  $("speed-out").textContent = c.speed + " %";
  $("dwell-out").textContent = c.dwell + " s";
  $("min-out").textContent = c.min_mm + " mm";
  $("max-out").textContent = c.max_mm + " mm";
  $("cycles-out").textContent = fmtInt(c.target);

  $("count").textContent = fmtInt(s.count);
  $("target").textContent = fmtInt(c.target);
  const pct = c.target > 0 ? Math.min(100, (s.count / c.target) * 100) : 0;
  $("progress-bar").style.width = pct + "%";

  $("pos").textContent = Math.round(s.position_mm);
  $("range-out").textContent = c.min_mm + " – " + c.max_mm + " mm";

  if (!gaugeBuilt && limits) buildGauge();
  if (gaugeBuilt) updateGauge(c, s.position_mm);

  $("btn-start").disabled = running || !s.device.connected;
  $("btn-stop").disabled = !running;
}

wireControls();
poll();
