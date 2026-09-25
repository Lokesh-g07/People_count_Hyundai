/**
 * websocket.js — WebSocket connection and message handling
 */

function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws`);

  ws.onopen = () => {
    setWsStatus(true);
    log('WebSocket connected', 'ok');
  };
  ws.onclose = () => {
    setWsStatus(false);
    log('WebSocket disconnected — retrying in 3 s…', 'warn');
    setStreamingState(false);
    setTimeout(connectWS, 3000);
  };
  ws.onerror = () => log('WebSocket error', 'error');
  ws.onmessage = (e) => handleMessage(JSON.parse(e.data));
}

function handleMessage(msg) {
  switch (msg.type) {
    case 'frame':
      renderFrame(msg.frame);
      updateStats(msg.stats);
      frameCount++;
      frameTotal++;
      updateFPS();
      document.getElementById('frame-counter').textContent = `FRAME ${frameTotal}`;
      break;
    case 'end':
      log('Stream ended — video finished', 'warn');
      setStreamingState(false);
      document.getElementById('btn-restart').disabled = false;
      break;
    case 'info':
      updateSourceInfo(msg.data);
      break;
    case 'summary':
      showSummaryInLog(msg.data);
      break;
    case 'status':
      log(msg.message, 'info');
      break;
    case 'error':
      log('⚠ ' + msg.message, 'error');
      setStreamingState(false);
      break;
  }
}

function sendMsg(obj) {
  if (ws && ws.readyState === WebSocket.OPEN)
    ws.send(JSON.stringify(obj));
}
