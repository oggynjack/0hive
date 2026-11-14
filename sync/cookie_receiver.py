"""Receives Google sessions from the browser extension and registers each account in gemini-web2api.

POST /sync   header X-Sync-Key: <secret in sync_key.txt>
  {"accounts": [{"email", "auth_user", "cookie", "sapisid", "xsrf_token", "gemini_bl"}, ...]}
Each account becomes cookies/<email-slug>.json and an entry in config.json "accounts" (identified by email).
GET /health -> list of known accounts (no secrets).
"""
import hmac
import json
import re
import subprocess
import time
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent.parent
KEY_FILE = ROOT / "sync" / "sync_key.txt"
if not KEY_FILE.exists():
    import secrets
    KEY_FILE.write_text(secrets.token_urlsafe(24))
    print(f"[cookie_receiver] Generated a new sync key at {KEY_FILE} -- put this same value in sync/extension/config.js (SYNC_KEY).")
KEY = KEY_FILE.read_text().strip()
CFG = ROOT / "config.json"
PORT = 8090
API = "http://127.0.0.1:8081"
PM2 = "pm2"  # relies on PATH; set an absolute path (e.g. r"C:\ProgramData\npm\pm2.cmd") if pm2 is not on PATH


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def slug(email):
    return re.sub(r"[^a-z0-9]+", "_", email.lower()).strip("_")


def register(accts):
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    if not (ROOT / "config.json.bak_before_sync").exists():
        (ROOT / "config.json.bak_before_sync").write_text(CFG.read_text(encoding="utf-8"), encoding="utf-8")
    lst = cfg.setdefault("accounts", [])
    saved, new = [], []
    (ROOT / "cookies").mkdir(exist_ok=True)
    for a in accts:
        email = str(a.get("email", "")).strip().lower()
        if not email or not a.get("cookie") or not a.get("sapisid"):
            continue
        rel = f"cookies/{slug(email)}.json"
        data = {k: a.get(k) for k in ("cookie", "sapisid", "auth_user", "xsrf_token", "gemini_bl")}
        data.update(email=email, last_sync=datetime.now().isoformat(timespec="seconds"))
        (ROOT / rel).write_text(json.dumps(data, indent=2), encoding="utf-8")
        entry = next((e for e in lst if e.get("name") == email or e.get("cookie_file") == rel), None)
        if entry is None:
            lst.append({"name": email, "cookie_file": rel, "auth_user": a.get("auth_user"), "priority": len(lst) + 1, "enabled": True})
            new.append(email)
        else:
            entry["auth_user"] = a.get("auth_user")
            entry["enabled"] = True
        saved.append(email)
    CFG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    if new:
        log(f"new accounts {new}: restarting gemini-web2api")
        subprocess.run([PM2, "restart", "gemini-web2api"], capture_output=True, timeout=60)
    else:
        for email in saved:
            try:
                urllib.request.urlopen(urllib.request.Request(f"{API}/v1/accounts/{email}/reload", method="POST", data=b""), timeout=10).read()
            except Exception:
                pass
    return saved, new


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Sync-Key")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        if self.path == "/health":
            cfg = json.loads(CFG.read_text(encoding="utf-8"))
            self._send(200, {"ok": True, "accounts": [{"name": a.get("name"), "auth_user": a.get("auth_user"), "enabled": a.get("enabled")} for a in cfg.get("accounts", [])]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/sync":
            return self._send(404, {"error": "not found"})
        if not hmac.compare_digest(self.headers.get("X-Sync-Key", "").encode(), KEY.encode()):
            return self._send(401, {"error": "bad key"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            saved, new = register(body.get("accounts", []))
            log(f"synced {len(saved)} account(s), new: {len(new)}")
            self._send(200, {"saved": saved, "new": new})
        except Exception as e:
            log(f"error: {e}")
            self._send(500, {"error": str(e)[:200]})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    log(f"cookie receiver listening on :{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()

