/**
 * ui.js — Clock, stats display, sparkline, FPS counter, log, alerts
 */

/* ═══════════════════════════════════════════════════════════════════
   CLOCK & SHIFT
═══════════════════════════════════════════════════════════════════ */
function updateClock() {
  document.getElementById('clock').textContent = new Date().toTimeString().slice(0,8);
  updateShiftBadge();
}
setInterval(updateClock, 1000);
updateClock();

function updateShiftBadge() {
  const selected = document.getElementById('shift-select').value;
  const shiftMap = {
    morning:   '☀ MORNING SHIFT',
    afternoon: '🌤 AFTERNOON SHIFT',
    night:     '🌙 NIGHT SHIFT',
  };
  document.getElementById('shift-badge').textContent = shiftMap[selected] || '';
  document.getElementById('stat-shift-label').textContent =
    shiftMap[selected]?.replace(/[^\w\s]/g, '').trim() || '';
}

function onShiftChange(val) { updateShiftBadge(); log(`Shift changed to ${val}`, 'info'); }

/* ═══════════════════════════════════════════════════════════════════
   LOG
═══════════════════════════════════════════════════════════════════ */
function log(msg, type = 'default') {
  const box = document.getElementById('log-box');
  const el  = document.createElement('div');
  el.className = `log-entry ${type}`;
  const t = new Date().toTimeString().slice(0, 8);
  el.innerHTML = `<span class="log-time">${t}</span><span class="log-msg">${msg}</span>`;
  box.appendChild(el);
  box.scrollTop = box.scrollHeight;
  while (box.children.length > 200) box.removeChild(box.firstChild);
}

function clearLog() {
  document.getElementById('log-box').innerHTML = '';
}

/* ═══════════════════════════════════════════════════════════════════
   STATS UPDATE
═══════════════════════════════════════════════════════════════════ */
function updateStats(s) {
  lastStats = s;

  setStatVal('stat-active', s.active);
  setStatVal('stat-exited', s.exited);
  setStatVal('stat-unique',  s.unique);
  setStatVal('stat-peak',    s.peak);

  // Dwell time
  document.getElementById('stat-dwell').textContent = s.avg_dwell + 's';

  // Session duration (mm:ss)
  const secs = s.session_secs || 0;
  const mm = String(Math.floor(secs / 60)).padStart(2, '0');
  const ss = String(Math.floor(secs % 60)).padStart(2, '0');
  document.getElementById('stat-session').textContent = `${mm}:${ss}`;

  // Capacity bar
  const cap = s.max_capacity || 10;
  document.getElementById('cap-display').textContent = cap;
  const pct = Math.min(100, (s.active / cap) * 100);
  const fill = document.getElementById('cap-bar-fill');
  fill.style.width = pct + '%';
  fill.style.background = s.alert ? 'var(--danger)' : (pct > 70 ? 'var(--warn)' : 'var(--accent3)');

  // Alert
  if (s.alert && !alertActive) {
    alertActive = true;
    document.getElementById('alert-banner').classList.add('active');
    document.getElementById('live-dot').className = 'live-dot warn';
    const now = Date.now();
    if (now - lastAlertLog > 5000) {
      log(`🚨 OVERCAPACITY — ${s.active}/${cap} in zone`, 'alert');
      lastAlertLog = now;
    }
  } else if (!s.alert && alertActive) {
    clearAlertBanner();
    log(`Zone back within capacity (${s.active}/${cap})`, 'ok');
  }

  // Sparkline update (~1 per second)
  const now2 = Date.now();
  if (now2 - sparkTimer >= 1000) {
    sparkData.shift();
    sparkData.push(s.active);
    sparkTimer = now2;
    drawSparkline();
  }
}

function clearAlertBanner() {
  alertActive = false;
  document.getElementById('alert-banner').classList.remove('active');
  const isStreaming = !document.getElementById('btn-stop').disabled;
  document.getElementById('live-dot').className = isStreaming ? 'live-dot live' : 'live-dot';
}

function setStatVal(id, val) {
  const el = document.getElementById(id);
  if (el && el.textContent !== String(val)) {
    el.textContent = val;
    el.classList.remove('bump');
    void el.offsetWidth;
    el.classList.add('bump');
  }
}

function updateSourceInfo(data) {
  if (data.width)        document.getElementById('info-res').textContent    = `${data.width}×${data.height}`;
  if (data.fps)          document.getElementById('info-fps').textContent    = `${data.fps} fps`;
  if (data.total_frames !== undefined)
                         document.getElementById('info-frames').textContent = data.total_frames || '∞ (live)';
  if (data.model)        document.getElementById('info-model').textContent  = data.model;
  if (data.conf)         document.getElementById('info-conf').textContent   = data.conf.toFixed(2);
  if (data.max_capacity) {
    document.getElementById('cap-input').value = data.max_capacity;
    document.getElementById('cap-display').textContent = data.max_capacity;
  }
}

/* ═══════════════════════════════════════════════════════════════════
   SPARKLINE
═══════════════════════════════════════════════════════════════════ */
function drawSparkline() {
  const canvas = document.getElementById('sparkline-canvas');
  const ctx = canvas.getContext('2d');
  const W = canvas.width  = canvas.offsetWidth;
  const H = canvas.height = 60;
  ctx.clearRect(0, 0, W, H);

  const maxVal = Math.max(1, ...sparkData);
  const stepX  = W / (SPARK_LEN - 1);

  // Fill gradient
  const grad = ctx.createLinearGradient(0, 0, 0, H);
  grad.addColorStop(0,   'rgba(0,200,240,.35)');
  grad.addColorStop(1,   'rgba(0,200,240,.02)');

  ctx.beginPath();
  sparkData.forEach((v, i) => {
    const x = i * stepX;
    const y = H - (v / maxVal) * (H - 4) - 2;
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  // Close fill path
  ctx.lineTo((SPARK_LEN - 1) * stepX, H);
  ctx.lineTo(0, H);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  // Line
  ctx.beginPath();
  sparkData.forEach((v, i) => {
    const x = i * stepX;
    const y = H - (v / maxVal) * (H - 4) - 2;
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.strokeStyle = 'rgba(0,200,240,.9)';
  ctx.lineWidth = 1.5;
  ctx.stroke();

  // Y-axis max label
  ctx.fillStyle = 'rgba(61,90,114,.8)';
  ctx.font = '8px Share Tech Mono';
  ctx.textAlign = 'right';
  ctx.fillText(maxVal, W - 2, 10);
  ctx.fillText(0, W - 2, H - 2);
}
drawSparkline();

/* ═══════════════════════════════════════════════════════════════════
   FPS
═══════════════════════════════════════════════════════════════════ */
function updateFPS() {
  const now = Date.now();
  if (now - fpsTimer >= 1000) {
    document.getElementById('fps-counter').textContent = frameCount + ' FPS';
    frameCount = 0;
    fpsTimer = now;
  }
}

/* ═══════════════════════════════════════════════════════════════════
   WS STATUS
═══════════════════════════════════════════════════════════════════ */
function setWsStatus(connected) {
  document.getElementById('ws-status').textContent =
    connected ? 'WS: CONNECTED' : 'WS: DISCONNECTED';
}
