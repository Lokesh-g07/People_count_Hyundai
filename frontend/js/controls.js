/**
 * controls.js — Stream control, file upload, model/conf/capacity, actions
 */

/* ═══════════════════════════════════════════════════════════════════
   STREAM CONTROL
═══════════════════════════════════════════════════════════════════ */
function startStream() {
  const src = document.getElementById('source-input').value.trim();
  if (!src) { log('No source specified', 'warn'); return; }
  log(`Opening: ${src}`, 'info');
  sendMsg({ type: 'start', source: src });
  setStreamingState(true);
  frameTotal = 0;
}

function stopStream() {
  sendMsg({ type: 'stop' });
  setStreamingState(false);
  log('Stream stopped', 'warn');
}

function restartStream() {
  sendMsg({ type: 'restart' });
  setStreamingState(true);
  frameCount = frameTotal = 0;
  sparkData.fill(0);
  log('Stream restarted', 'info');
}

function setStreamingState(streaming) {
  document.getElementById('btn-start').disabled   =  streaming;
  document.getElementById('btn-stop').disabled    = !streaming;
  document.getElementById('btn-restart').disabled = !streaming;

  const dot   = document.getElementById('live-dot');
  const label = document.getElementById('status-label');
  if (streaming) {
    dot.className = 'live-dot live';
    label.textContent = 'LIVE';
  } else {
    dot.className = 'live-dot';
    label.textContent = 'OFFLINE';
  }
  if (!streaming) {
    ['stat-active','stat-exited','stat-unique','stat-peak'].forEach(id => setStatVal(id, 0));
    document.getElementById('stat-dwell').textContent = '0s';
    document.getElementById('stat-session').textContent = '00:00';
    clearAlertBanner();
    sparkData.fill(0);
    drawSparkline();
  }
}

/* ═══════════════════════════════════════════════════════════════════
   FILE BROWSE
═══════════════════════════════════════════════════════════════════ */
function triggerFileOpen() {
  document.getElementById('file-input').click();
}

async function handleFileSelect(e) {
  const file = e.target.files[0];
  if (!file) return;
  log(`Uploading ${file.name}…`, 'info');
  const form = new FormData();
  form.append('file', file);
  try {
    const res  = await fetch('/upload', { method: 'POST', body: form });
    const data = await res.json();
    document.getElementById('source-input').value = data.path;
    log(`Uploaded → ${data.path}`, 'ok');
  } catch (err) {
    log('Upload failed: ' + err, 'error');
  }
  e.target.value = '';
}

/* ═══════════════════════════════════════════════════════════════════
   MODEL / CONF / CAPACITY CONTROLS
═══════════════════════════════════════════════════════════════════ */
let confChangeTimer = null;
function onConfChange(val) {
  const conf = (parseInt(val) / 100).toFixed(2);
  document.getElementById('conf-val').textContent = conf;
  clearTimeout(confChangeTimer);
  confChangeTimer = setTimeout(() => {
    sendMsg({ type: 'set_conf', value: parseFloat(conf) });
    document.getElementById('info-conf').textContent = conf;
  }, 400);
}

let modelChangeTimer = null;
function onModelChange(val) {
  clearTimeout(modelChangeTimer);
  modelChangeTimer = setTimeout(() => {
    log(`Switching model to ${val}…`, 'info');
    sendMsg({ type: 'set_model', model: val });
    document.getElementById('info-model').textContent = val;
  }, 200);
}

function onCapacityChange(val) {
  const cap = Math.max(1, parseInt(val) || 1);
  document.getElementById('cap-input').value = cap;
  document.getElementById('cap-display').textContent = cap;
  sendMsg({ type: 'set_capacity', value: cap });
}

/* ═══════════════════════════════════════════════════════════════════
   ACTIONS
═══════════════════════════════════════════════════════════════════ */
async function exportCSV() {
  try {
    log('Exporting session CSV…', 'info');
    const res = await fetch('/export');
    if (!res.ok) { log('Export failed — no active session', 'error'); return; }
    const blob = await res.blob();
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href = url;
    a.download = `factory_eye_${new Date().toISOString().slice(0,19).replace(/:/g,'-')}.csv`;
    a.click();
    URL.revokeObjectURL(url);
    log('CSV exported ✓', 'ok');
  } catch (err) {
    log('Export error: ' + err, 'error');
  }
}

async function takeSnapshot() {
  try {
    log('Saving snapshot…', 'info');
    const res = await fetch('/snapshot');
    if (!res.ok) { log('Snapshot failed — no active frame', 'error'); return; }
    const blob = await res.blob();
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href = url;
    a.download = `snapshot_${new Date().toISOString().slice(0,19).replace(/:/g,'-')}.png`;
    a.click();
    URL.revokeObjectURL(url);
    log('Snapshot saved ✓', 'ok');
  } catch (err) {
    log('Snapshot error: ' + err, 'error');
  }
}

function requestSummary() {
  sendMsg({ type: 'get_summary' });
}

function showSummaryInLog(data) {
  log('── SESSION SUMMARY ─────────────────', 'info');
  log(`Unique visitors : ${data.unique_visitors}`, 'info');
  log(`Peak count      : ${data.peak_count}`, 'info');
  log(`Avg dwell (s)   : ${data.avg_dwell_secs}`, 'info');
  log(`Total visits    : ${data.total_visits}`, 'info');
  log(`Duration (s)    : ${data.session_duration}`, 'info');
  log(`Model           : ${data.model}`, 'info');
}
