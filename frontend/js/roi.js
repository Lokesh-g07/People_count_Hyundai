/**
 * roi.js — ROI polygon drawing, canvas rendering, and mouse events
 */

/* ═══════════════════════════════════════════════════════════════════
   CANVAS SETUP
═══════════════════════════════════════════════════════════════════ */
const vCanvas = document.getElementById('video-canvas');
const vCtx    = vCanvas.getContext('2d');
const rCanvas = document.getElementById('roi-canvas');
const rCtx    = rCanvas.getContext('2d');
const wrap    = document.getElementById('canvas-wrap');

function resizeCanvases() {
  const w = wrap.clientWidth, h = wrap.clientHeight;
  vCanvas.width = rCanvas.width  = w;
  vCanvas.height = rCanvas.height = h;
}
window.addEventListener('resize', () => { resizeCanvases(); drawRoiOverlay(); });
resizeCanvases();

// Render frame from base64 JPEG
const img = new Image();
img.onload = () => {
  vCtx.clearRect(0, 0, vCanvas.width, vCanvas.height);
  const ar = img.width / img.height;
  const cw = vCanvas.width, ch = vCanvas.height;
  let dw = cw, dh = ch, dx = 0, dy = 0;
  if (cw / ch > ar) { dw = ch * ar; dx = (cw - dw) / 2; }
  else              { dh = cw / ar; dy = (ch - dh) / 2; }
  vCtx.drawImage(img, dx, dy, dw, dh);
  document.getElementById('canvas-overlay-msg').style.display = 'none';
};
function renderFrame(b64) { img.src = 'data:image/jpeg;base64,' + b64; }

/* ═══════════════════════════════════════════════════════════════════
   MOUSE EVENTS
═══════════════════════════════════════════════════════════════════ */
wrap.addEventListener('mousemove', (e) => {
  const r = wrap.getBoundingClientRect();
  mousePos = { x: e.clientX - r.left, y: e.clientY - r.top };
  if (drawMode) {
    const cd = document.getElementById('coord-display');
    cd.textContent = `X:${Math.round(mousePos.x)} Y:${Math.round(mousePos.y)}`;
    cd.style.opacity = '1';
    drawRoiOverlay();
  }
});
wrap.addEventListener('mouseleave', () => {
  document.getElementById('coord-display').style.opacity = '0';
  if (drawMode) drawRoiOverlay();
});
wrap.addEventListener('click', (e) => {
  if (!drawMode) return;
  const r = wrap.getBoundingClientRect();
  tempPoints.push({ x: e.clientX - r.left, y: e.clientY - r.top });
  updateRoiPointsInfo();
  drawRoiOverlay();
});
wrap.addEventListener('dblclick', (e) => {
  e.preventDefault();
  if (drawMode && tempPoints.length >= 3) finaliseRoi();
});
wrap.addEventListener('contextmenu', (e) => {
  e.preventDefault();
  if (!drawMode || tempPoints.length === 0) return;
  tempPoints.pop();
  updateRoiPointsInfo();
  drawRoiOverlay();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && drawMode) cancelDraw();
  if (e.key === 'Enter'  && drawMode && tempPoints.length >= 3) finaliseRoi();
});

/* ═══════════════════════════════════════════════════════════════════
   ROI DRAW MODE
═══════════════════════════════════════════════════════════════════ */
function toggleDrawMode() {
  drawMode ? cancelDraw() : startDraw();
}
function startDraw() {
  drawMode = true;
  tempPoints = [];
  wrap.classList.add('draw-mode');
  document.getElementById('draw-hint').classList.add('visible');
  const btn = document.getElementById('btn-draw');
  btn.textContent = '✕ CANCEL DRAW';
  btn.className = 'btn btn-danger btn-sm';
  log('ROI draw mode — click to place vertices', 'info');
}
function cancelDraw() {
  drawMode = false;
  tempPoints = [];
  wrap.classList.remove('draw-mode');
  document.getElementById('draw-hint').classList.remove('visible');
  document.getElementById('coord-display').style.opacity = '0';
  const btn = document.getElementById('btn-draw');
  btn.textContent = '✏ DRAW ROI';
  btn.className = 'btn btn-ghost btn-sm';
  document.getElementById('btn-apply-roi').disabled = roiPoints.length < 3;
  drawRoiOverlay();
}
function finaliseRoi() {
  if (tempPoints.length < 3) { log('Need at least 3 points', 'warn'); return; }
  roiPoints = tempPoints.map(p => toNormalised(p.x, p.y));
  cancelDraw();
  drawRoiOverlay();
  document.getElementById('btn-apply-roi').disabled = false;
  updateRoiPointsInfo();
  log(`ROI polygon ready — ${roiPoints.length} vertices. Click APPLY.`, 'ok');
}
function applyRoi() {
  if (roiPoints.length < 3) return;
  sendMsg({ type: 'set_roi', points: roiPoints, space: 'normalised' });
  document.getElementById('info-roi').textContent = roiPoints.length + ' vertices';
  log(`ROI applied — ${roiPoints.length} vertices`, 'ok');
}
function clearRoi() {
  roiPoints = [];
  tempPoints = [];
  if (drawMode) cancelDraw();
  wrap.classList.remove('draw-mode');
  document.getElementById('btn-apply-roi').disabled = true;
  document.getElementById('info-roi').textContent = 'None';
  sendMsg({ type: 'clear_roi' });
  drawRoiOverlay();
  updateRoiPointsInfo();
  log('ROI cleared', 'warn');
}
function toNormalised(sx, sy) {
  return [sx / wrap.clientWidth, sy / wrap.clientHeight];
}
function toScreen(nx, ny) {
  return { x: nx * wrap.clientWidth, y: ny * wrap.clientHeight };
}
function updateRoiPointsInfo() {
  const count = drawMode ? tempPoints.length : roiPoints.length;
  document.getElementById('roi-points-info').textContent =
    count + (count === 1 ? ' pt' : ' pts');
}

/* ═══════════════════════════════════════════════════════════════════
   ROI OVERLAY RENDERING
═══════════════════════════════════════════════════════════════════ */
function drawRoiOverlay() {
  const w = rCanvas.width, h = rCanvas.height;
  rCtx.clearRect(0, 0, w, h);

  // Committed ROI
  if (roiPoints.length >= 3 && !drawMode) {
    const pts = roiPoints.map(([nx, ny]) => toScreen(nx, ny));
    rCtx.beginPath();
    rCtx.moveTo(pts[0].x, pts[0].y);
    pts.slice(1).forEach(p => rCtx.lineTo(p.x, p.y));
    rCtx.closePath();
    rCtx.fillStyle   = 'rgba(0,200,240,.07)';
    rCtx.strokeStyle = 'rgba(0,200,240,.85)';
    rCtx.lineWidth   = 2;
    rCtx.setLineDash([]);
    rCtx.fill();
    rCtx.stroke();
    pts.forEach((p, i) => {
      rCtx.beginPath();
      rCtx.arc(p.x, p.y, 5, 0, Math.PI * 2);
      rCtx.fillStyle = '#00c8f0';
      rCtx.fill();
      rCtx.fillStyle = '#000';
      rCtx.font = 'bold 9px Share Tech Mono';
      rCtx.textAlign = 'center';
      rCtx.fillText(i + 1, p.x, p.y + 3.5);
    });
    return;
  }

  // In-progress polygon
  if (!drawMode || tempPoints.length === 0) return;
  const pts = [...tempPoints, mousePos];
  rCtx.beginPath();
  rCtx.moveTo(pts[0].x, pts[0].y);
  pts.slice(1).forEach(p => rCtx.lineTo(p.x, p.y));
  if (tempPoints.length >= 3) {
    rCtx.closePath();
    rCtx.fillStyle = 'rgba(255,124,42,.07)';
    rCtx.fill();
  }
  rCtx.strokeStyle = 'rgba(255,124,42,.9)';
  rCtx.lineWidth   = 2;
  rCtx.setLineDash([6, 3]);
  rCtx.stroke();
  rCtx.setLineDash([]);
  tempPoints.forEach((p, i) => {
    rCtx.beginPath();
    rCtx.arc(p.x, p.y, 5, 0, Math.PI * 2);
    rCtx.fillStyle = '#ff7c2a';
    rCtx.fill();
    rCtx.fillStyle = '#000';
    rCtx.font = 'bold 9px Share Tech Mono';
    rCtx.textAlign = 'center';
    rCtx.fillText(i + 1, p.x, p.y + 3.5);
  });
  // Crosshair
  rCtx.strokeStyle = 'rgba(255,124,42,.5)';
  rCtx.lineWidth = 1;
  rCtx.setLineDash([3, 3]);
  rCtx.beginPath();
  rCtx.moveTo(mousePos.x, 0); rCtx.lineTo(mousePos.x, h);
  rCtx.moveTo(0, mousePos.y); rCtx.lineTo(w, mousePos.y);
  rCtx.stroke();
  rCtx.setLineDash([]);
}
