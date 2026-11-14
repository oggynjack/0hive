"""
Gemini Web2API Real-Time Dashboard
- WebSocket for 1ms latency updates
- Tracks all requests, responses, tokens, errors
- Glass-dark-ui theme
"""
import asyncio
import json
import time
import os
import sys
from datetime import datetime
from pathlib import Path
from collections import deque
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import websockets
import subprocess

# ─── Metrics Store ────────────────────────────────────────────────────────────
class MetricsStore:
    def __init__(self):
        self.total_requests = 0
        self.total_tokens = {"prompt": 0, "completion": 0, "total": 0}
        self.model_usage = {}
        self.recent_requests = deque(maxlen=100)
        self.errors = deque(maxlen=50)
        self.active_connections = 0
        self.start_time = time.time()
        self.request_times = deque(maxlen=200)
        self.status_codes = {"200": 0, "400": 0, "401": 0, "429": 0, "500": 0}
        self.ws_clients = set()
        
    def record_request(self, model, tokens, duration, status, error=None):
        self.total_requests += 1
        if tokens:
            self.total_tokens["prompt"] += tokens.get("prompt_tokens", 0)
            self.total_tokens["completion"] += tokens.get("completion_tokens", 0)
            self.total_tokens["total"] += tokens.get("total_tokens", 0)
        
        if model:
            self.model_usage[model] = self.model_usage.get(model, 0) + 1
        
        self.request_times.append(duration)
        self.status_codes[str(status)] = self.status_codes.get(str(status), 0) + 1
        
        entry = {
            "time": datetime.now().isoformat(),
            "model": model,
            "tokens": tokens,
            "duration": duration,
            "status": status,
            "error": error
        }
        self.recent_requests.appendleft(entry)
        
        if error:
            self.errors.appendleft({"time": datetime.now().isoformat(), "error": error})
        
        # Broadcast to WebSocket clients
        asyncio.get_event_loop().call_soon_threadsafe(
            lambda: asyncio.ensure_future(self.broadcast(entry))
        )
    
    async def broadcast(self, entry):
        if self.ws_clients:
            msg = json.dumps(entry)
            for client in self.ws_clients.copy():
                try:
                    await client.send(msg)
                except:
                    self.ws_clients.discard(client)
    
    def get_stats(self):
        avg_latency = sum(self.request_times) / len(self.request_times) if self.request_times else 0
        return {
            "total_requests": self.total_requests,
            "total_tokens": self.total_tokens,
            "model_usage": self.model_usage,
            "recent_requests": list(self.recent_requests)[:20],
            "errors": list(self.errors)[:10],
            "avg_latency_ms": round(avg_latency * 1000, 2),
            "uptime_seconds": int(time.time() - self.start_time),
            "status_codes": self.status_codes,
            "active_connections": len(self.ws_clients)
        }

metrics = MetricsStore()

# ─── Patch gemini_web2api to track metrics ───────────────────────────────────
def patch_gemini_handler():
    """Monkey-patch GeminiHandler to track metrics"""
    import gemini_web2api
    
    original_do_post = gemini_web2api.GeminiHandler.do_POST
    
    def tracked_do_post(self):
        start = time.time()
        try:
            original_do_post(self)
        finally:
            duration = time.time() - start
            # Extract model and tokens from response if available
            model = getattr(self, '_last_model', None)
            tokens = getattr(self, '_last_tokens', None)
            status = self._headers.get('status', 200) if hasattr(self, '_headers') else 200
            metrics.record_request(model, tokens, duration, status)
    
    gemini_web2api.GeminiHandler.do_POST = tracked_do_post

# ─── WebSocket Server ────────────────────────────────────────────────────────
async def ws_handler(websocket, path=None):
    metrics.ws_clients.add(websocket)
    try:
        # Send initial stats
        await websocket.send(json.dumps(metrics.get_stats()))
        # Keep connection alive and handle client messages
        async for message in websocket:
            if message == "get_stats":
                await websocket.send(json.dumps(metrics.get_stats()))
    finally:
        metrics.ws_clients.discard(websocket)

# ─── Dashboard HTTP Server ───────────────────────────────────────────────────
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gemini Web2API Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg-0:#020617;--bg-1:#0b1220;--bg-2:#111827;
  --glass-fill:rgba(15,23,42,.45);--glass-border:rgba(148,163,184,.08);
  --text-primary:#f8fafc;--text-secondary:#94a3b8;--text-muted:#475569;
  --emerald:#10b981;--red:#ef4444;--amber:#f59e0b;--blue:#3b82f6;--purple:#8b5cf6;
  --gradient-1:linear-gradient(135deg,#10b981,#3b82f6);
  --gradient-2:linear-gradient(135deg,#ef4444,#f59e0b);
  --radius:12px;--blur:16px;
}
body{font-family:'Inter',system-ui,sans-serif;background:var(--bg-0);color:var(--text-primary);min-height:100vh;overflow-x:hidden}
.container{max-width:1400px;margin:0 auto;padding:20px}

/* Header */
.header{display:flex;align-items:center;justify-content:space-between;padding:20px 0;border-bottom:1px solid var(--glass-border);margin-bottom:24px}
.header h1{font-size:1.5rem;font-weight:700;background:var(--gradient-1);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.header .status{display:flex;align-items:center;gap:8px}
.status-dot{width:8px;height:8px;border-radius:50%;background:var(--emerald);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
.status-text{font-size:.875rem;color:var(--text-secondary)}

/* Stats Grid */
.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-bottom:24px}
.stat-card{background:var(--glass-fill);backdrop-filter:blur(var(--blur));border:1px solid var(--glass-border);border-radius:var(--radius);padding:20px;position:relative;overflow:hidden}
.stat-card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:var(--gradient-1);opacity:.6}
.stat-card.error::before{background:var(--gradient-2)}
.stat-label{font-size:.75rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px}
.stat-value{font-size:1.75rem;font-weight:700;font-variant-numeric:tabular-nums}
.stat-sub{font-size:.75rem;color:var(--text-secondary);margin-top:4px}

/* Panels */
.panels{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:24px}
@media(max-width:900px){.panels{grid-template-columns:1fr}}
.panel{background:var(--glass-fill);backdrop-filter:blur(var(--blur));border:1px solid var(--glass-border);border-radius:var(--radius);overflow:hidden}
.panel-header{padding:16px 20px;border-bottom:1px solid var(--glass-border);display:flex;align-items:center;justify-content:space-between}
.panel-header h3{font-size:.875rem;font-weight:600;color:var(--text-secondary)}
.panel-body{padding:16px 20px;max-height:400px;overflow-y:auto}

/* Request Log */
.request-item{display:flex;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid var(--glass-border);font-size:.8125rem}
.request-item:last-child{border-bottom:none}
.request-time{color:var(--text-muted);font-variant-numeric:tabular-nums;min-width:80px}
.request-model{color:var(--blue);font-weight:500;min-width:120px}
.request-tokens{color:var(--amber);font-variant-numeric:tabular-nums;min-width:80px}
.request-duration{font-variant-numeric:tabular-nums;min-width:60px}
.request-status{min-width:40px;text-align:center}
.status-ok{color:var(--emerald)}
.status-err{color:var(--red)}

/* Model Chart */
.model-bar{display:flex;align-items:center;gap:12px;margin-bottom:12px}
.model-name{min-width:140px;font-size:.8125rem;color:var(--text-secondary)}
.model-fill{flex:1;height:24px;background:var(--bg-2);border-radius:4px;overflow:hidden;position:relative}
.model-fill-inner{height:100%;border-radius:4px;transition:width .3s ease}
.model-count{min-width:40px;text-align:right;font-size:.8125rem;font-variant-numeric:tabular-nums;color:var(--text-primary)}

/* Token Counter Animation */
.token-counter{display:flex;gap:20px;justify-content:center;padding:20px}
.token-box{text-align:center}
.token-number{font-size:2rem;font-weight:700;font-variant-numeric:tabular-nums}
.token-label{font-size:.6875rem;color:var(--text-muted);text-transform:uppercase;margin-top:4px}

/* Latency Graph */
.latency-graph{height:120px;position:relative;margin:10px 0}
.latency-canvas{width:100%;height:100%}

/* Connection indicator */
.ws-status{display:inline-flex;align-items:center;gap:6px;padding:4px 12px;border-radius:20px;font-size:.75rem;font-weight:500}
.ws-connected{background:rgba(16,185,129,.1);color:var(--emerald);border:1px solid rgba(16,185,129,.2)}
.ws-disconnected{background:rgba(239,68,68,.1);color:var(--red);border:1px solid rgba(239,68,68,.2)}

/* Scrollbar */
::-webkit-scrollbar{width:6px}
::-webkit-scrollbar-track{background:var(--bg-2)}
::-webkit-scrollbar-thumb{background:var(--text-muted);border-radius:3px}
</style>
</head>
<body>
<div class="container">
  <!-- Header -->
  <div class="header">
    <div style="display:flex;align-items:center;gap:12px">
      <h1>⚡ Gemini Web2API</h1>
      <span class="ws-status ws-disconnected" id="wsStatus">● Disconnected</span>
    </div>
    <div class="status">
      <span class="status-dot" id="serverDot"></span>
      <span class="status-text" id="serverStatus">Connecting...</span>
    </div>
  </div>

  <!-- Stats -->
  <div class="stats-grid">
    <div class="stat-card">
      <div class="stat-label">Total Requests</div>
      <div class="stat-value" id="totalRequests">0</div>
      <div class="stat-sub">Since server start</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Avg Latency</div>
      <div class="stat-value" id="avgLatency">0ms</div>
      <div class="stat-sub">Last 200 requests</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Uptime</div>
      <div class="stat-value" id="uptime">0m</div>
      <div class="stat-sub" id="uptimeSub">Since start</div>
    </div>
    <div class="stat-card error">
      <div class="stat-label">Errors</div>
      <div class="stat-value" id="errorCount">0</div>
      <div class="stat-sub">4xx + 5xx responses</div>
    </div>
  </div>

  <!-- Token Counter -->
  <div class="panel" style="margin-bottom:24px">
    <div class="panel-header"><h3>Token Usage</h3></div>
    <div class="token-counter">
      <div class="token-box">
        <div class="token-number" style="color:var(--blue)" id="promptTokens">0</div>
        <div class="token-label">Prompt Tokens</div>
      </div>
      <div class="token-box">
        <div class="token-number" style="color:var(--emerald)" id="completionTokens">0</div>
        <div class="token-label">Completion Tokens</div>
      </div>
      <div class="token-box">
        <div class="token-number" style="color:var(--amber)" id="totalTokens">0</div>
        <div class="token-label">Total Tokens</div>
      </div>
    </div>
  </div>

  <!-- Panels -->
  <div class="panels">
    <!-- Live Request Feed -->
    <div class="panel">
      <div class="panel-header">
        <h3>Live Requests</h3>
        <span style="font-size:.75rem;color:var(--text-muted)" id="requestCount">0 requests</span>
      </div>
      <div class="panel-body" id="requestFeed">
        <div style="text-align:center;color:var(--text-muted);padding:40px">Waiting for requests...</div>
      </div>
    </div>

    <!-- Model Usage -->
    <div class="panel">
      <div class="panel-header">
        <h3>Model Usage</h3>
      </div>
      <div class="panel-body" id="modelChart">
        <div style="text-align:center;color:var(--text-muted);padding:40px">No data yet</div>
      </div>
    </div>
  </div>

  <!-- Latency Graph -->
  <div class="panel">
    <div class="panel-header">
      <h3>Request Latency</h3>
      <span style="font-size:.75rem;color:var(--text-muted)" id="latencyInfo">Last 50 requests</span>
    </div>
    <div class="panel-body">
      <div class="latency-graph">
        <canvas id="latencyCanvas" class="latency-canvas"></canvas>
      </div>
    </div>
  </div>
</div>

<script>
// ─── WebSocket Connection ────────────────────────────────────────────────────
let ws = null;
let reconnectTimer = null;
let latencyHistory = [];
const MAX_LATENCY_POINTS = 50;

function connect() {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${protocol}//${location.host}/ws`);
  
  ws.onopen = () => {
    document.getElementById('wsStatus').className = 'ws-status ws-connected';
    document.getElementById('wsStatus').textContent = '● Connected';
    document.getElementById('serverDot').style.background = 'var(--emerald)';
    document.getElementById('serverStatus').textContent = 'Live';
    if (reconnectTimer) clearInterval(reconnectTimer);
  };
  
  ws.onclose = () => {
    document.getElementById('wsStatus').className = 'ws-status ws-disconnected';
    document.getElementById('wsStatus').textContent = '● Disconnected';
    document.getElementById('serverDot').style.background = 'var(--red)';
    document.getElementById('serverStatus').textContent = 'Offline';
    reconnectTimer = setInterval(connect, 2000);
  };
  
  ws.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      if (data.total_requests !== undefined) {
        updateStats(data);
      } else {
        handleNewRequest(data);
      }
    } catch (err) {}
  };
  
  ws.onerror = () => {};
}

// ─── Update Functions ────────────────────────────────────────────────────────
function updateStats(data) {
  document.getElementById('totalRequests').textContent = data.total_requests.toLocaleString();
  document.getElementById('avgLatency').textContent = data.avg_latency_ms + 'ms';
  document.getElementById('promptTokens').textContent = data.total_tokens.prompt.toLocaleString();
  document.getElementById('completionTokens').textContent = data.total_tokens.completion.toLocaleString();
  document.getElementById('totalTokens').textContent = data.total_tokens.total.toLocaleString();
  
  const errors = (data.status_codes['400']||0) + (data.status_codes['401']||0) + 
                 (data.status_codes['429']||0) + (data.status_codes['500']||0);
  document.getElementById('errorCount').textContent = errors;
  
  const uptime = data.uptime_seconds;
  const h = Math.floor(uptime/3600);
  const m = Math.floor((uptime%3600)/60);
  document.getElementById('uptime').textContent = h > 0 ? `${h}h ${m}m` : `${m}m`;
  
  // Model chart
  updateModelChart(data.model_usage);
  
  // Recent requests
  if (data.recent_requests && data.recent_requests.length > 0) {
    const feed = document.getElementById('requestFeed');
    feed.innerHTML = '';
    data.recent_requests.forEach(r => addRequestToFeed(r));
    document.getElementById('requestCount').textContent = data.total_requests + ' requests';
  }
}

function handleNewRequest(data) {
  document.getElementById('totalRequests').textContent = 
    (parseInt(document.getElementById('totalRequests').textContent.replace(/,/g,'')) + 1).toLocaleString();
  
  if (data.tokens) {
    const pt = parseInt(document.getElementById('promptTokens').textContent.replace(/,/g,'')) + (data.tokens.prompt_tokens||0);
    const ct = parseInt(document.getElementById('completionTokens').textContent.replace(/,/g,'')) + (data.tokens.completion_tokens||0);
    document.getElementById('promptTokens').textContent = pt.toLocaleString();
    document.getElementById('completionTokens').textContent = ct.toLocaleString();
    document.getElementById('totalTokens').textContent = (pt+ct).toLocaleString();
  }
  
  addRequestToFeed(data);
  
  if (data.duration) {
    latencyHistory.push(data.duration * 1000);
    if (latencyHistory.length > MAX_LATENCY_POINTS) latencyHistory.shift();
    drawLatencyGraph();
  }
}

function addRequestToFeed(req) {
  const feed = document.getElementById('requestFeed');
  if (feed.querySelector('div[style*="text-align:center"]')) feed.innerHTML = '';
  
  const div = document.createElement('div');
  div.className = 'request-item';
  
  const time = new Date(req.time).toLocaleTimeString();
  const model = req.model || 'unknown';
  const tokens = req.tokens ? `${req.tokens.total_tokens||0}` : '-';
  const dur = req.duration ? `${(req.duration*1000).toFixed(0)}ms` : '-';
  const statusClass = req.status < 400 ? 'status-ok' : 'status-err';
  
  div.innerHTML = `
    <span class="request-time">${time}</span>
    <span class="request-model">${model}</span>
    <span class="request-tokens">${tokens} tok</span>
    <span class="request-duration ${statusClass}">${dur}</span>
    <span class="request-status ${statusClass}">${req.status||'?'}</span>
  `;
  
  feed.insertBefore(div, feed.firstChild);
  if (feed.children.length > 30) feed.removeChild(feed.lastChild);
}

function updateModelChart(usage) {
  const chart = document.getElementById('modelChart');
  const total = Object.values(usage).reduce((a,b)=>a+b, 0);
  if (total === 0) return;
  
  const colors = ['var(--blue)','var(--emerald)','var(--amber)','var(--purple)','var(--red)'];
  let html = '';
  Object.entries(usage).sort((a,b)=>b[1]-a[1]).forEach(([model, count], i) => {
    const pct = (count/total*100).toFixed(1);
    html += `<div class="model-bar">
      <span class="model-name">${model}</span>
      <div class="model-fill"><div class="model-fill-inner" style="width:${pct}%;background:${colors[i%colors.length]}"></div></div>
      <span class="model-count">${count}</span>
    </div>`;
  });
  chart.innerHTML = html;
}

function drawLatencyGraph() {
  const canvas = document.getElementById('latencyCanvas');
  const ctx = canvas.getContext('2d');
  const rect = canvas.parentElement.getBoundingClientRect();
  canvas.width = rect.width;
  canvas.height = rect.height;
  
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (latencyHistory.length < 2) return;
  
  const maxLat = Math.max(...latencyHistory) * 1.2 || 1000;
  const step = canvas.width / (MAX_LATENCY_POINTS - 1);
  
  // Grid
  ctx.strokeStyle = 'rgba(148,163,184,0.1)';
  ctx.lineWidth = 1;
  for (let i = 0; i < 5; i++) {
    const y = (canvas.height / 5) * i;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(canvas.width, y);
    ctx.stroke();
  }
  
  // Line
  ctx.strokeStyle = '#10b981';
  ctx.lineWidth = 2;
  ctx.beginPath();
  latencyHistory.forEach((lat, i) => {
    const x = i * step;
    const y = canvas.height - (lat / maxLat * canvas.height);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
  
  // Fill
  ctx.lineTo(canvas.width, canvas.height);
  ctx.lineTo(0, canvas.height);
  ctx.closePath();
  ctx.fillStyle = 'rgba(16,185,129,0.1)';
  ctx.fill();
}

// ─── Init ────────────────────────────────────────────────────────────────────
connect();
setInterval(() => {
  if (ws && ws.readyState === 1) ws.send('get_stats');
}, 1000);
</script>
</body>
</html>"""

class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode())
        elif self.path == '/api/stats':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(metrics.get_stats()).encode())
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, fmt, *args):
        pass  # Silence dashboard logs

# ─── Main ────────────────────────────────────────────────────────────────────
async def main():
    # Start WebSocket server
    ws_server = await websockets.serve(ws_handler, "0.0.0.0", 8082)
    
    # Start dashboard HTTP server
    http_server = HTTPServer(("0.0.0.0", 8083), DashboardHandler)
    http_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
    http_thread.start()
    
    print(f"Dashboard: http://localhost:8083")
    print(f"WebSocket: ws://localhost:8082")
    
    await asyncio.Future()  # Run forever

if __name__ == "__main__":
    asyncio.run(main())
