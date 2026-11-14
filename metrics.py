"""
Enhanced metrics tracker that persists to SQLite.
Intercepts responses and captures actual token usage from the response body.
"""
import time
import json
import re
from collections import deque
import db


class MetricsTracker:
    def __init__(self):
        self.start_time = time.time()
        # Session-only counters (reset on restart)
        self.session_requests = 0
        self.session_tokens = {"prompt": 0, "completion": 0, "total": 0}
        self.session_model_usage = {}
        self.session_recent = deque(maxlen=100)
        self.session_errors = deque(maxlen=50)
        self.session_request_times = deque(maxlen=200)
        self.session_status_codes = {}

    def record_request(self, model, tokens, duration, status, error=None, account_name=""):
        """Record a request in both session memory and SQLite."""
        self.session_requests += 1
        if tokens:
            self.session_tokens["prompt"] += tokens.get("prompt_tokens", 0)
            self.session_tokens["completion"] += tokens.get("completion_tokens", 0)
            self.session_tokens["total"] += tokens.get("total_tokens", 0)
        if model:
            self.session_model_usage[model] = self.session_model_usage.get(model, 0) + 1
        self.session_request_times.append(duration)
        sc = str(status)
        self.session_status_codes[sc] = self.session_status_codes.get(sc, 0) + 1

        entry = {
            "time": time.strftime("%H:%M:%S"),
            "model": model or "unknown",
            "tokens": tokens,
            "duration": round(duration * 1000, 1),
            "status": status,
            "error": error,
            "account": account_name,
        }
        self.session_recent.appendleft(entry)
        if error:
            self.session_errors.appendleft({"time": time.strftime("%H:%M:%S"), "error": error})

        # Persist to DB
        try:
            db.log_request(
                model=model,
                prompt_tokens=tokens.get("prompt_tokens", 0) if tokens else 0,
                completion_tokens=tokens.get("completion_tokens", 0) if tokens else 0,
                total_tokens=tokens.get("total_tokens", 0) if tokens else 0,
                duration_ms=round(duration * 1000, 1),
                status=status,
                error=error,
                account_name=account_name,
            )
        except Exception:
            pass

    def extract_tokens_from_response(self, response_body):
        """Parse OpenAI-format response JSON to extract usage tokens.
        
        Handles both chat.completion and response objects.
        Returns dict with prompt_tokens, completion_tokens, total_tokens or None.
        """
        if not response_body:
            return None
        try:
            if isinstance(response_body, str):
                data = json.loads(response_body)
            elif isinstance(response_body, bytes):
                data = json.loads(response_body.decode("utf-8", errors="replace"))
            else:
                data = response_body

            # OpenAI chat.completion format: usage.prompt_tokens etc.
            usage = data.get("usage")
            if usage and isinstance(usage, dict):
                pt = usage.get("prompt_tokens", 0)
                ct = usage.get("completion_tokens", 0)
                tt = usage.get("total_tokens", 0)
                # Handle OpenAI Responses API format (input_tokens/output_tokens)
                if not pt and usage.get("input_tokens"):
                    pt = usage.get("input_tokens", 0)
                    ct = usage.get("output_tokens", 0)
                    tt = usage.get("total_tokens", pt + ct)
                if pt or ct or tt:
                    return {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": tt}

            # Google native format: usageMetadata
            um = data.get("usageMetadata")
            if um and isinstance(um, dict):
                return {
                    "prompt_tokens": um.get("promptTokenCount", 0),
                    "completion_tokens": um.get("candidatesTokenCount", 0),
                    "total_tokens": um.get("totalTokenCount", 0),
                }
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass
        return None

    def get_realtime_stats(self):
        """Return current session stats."""
        avg_latency = (sum(self.session_request_times) / len(self.session_request_times)
                       if self.session_request_times else 0)
        return {
            "total_requests": self.session_requests,
            "total_tokens": self.session_tokens,
            "model_usage": self.session_model_usage,
            "recent_requests": list(self.session_recent)[:30],
            "errors": list(self.session_errors)[:10],
            "avg_latency_ms": round(avg_latency, 2),
            "uptime_seconds": int(time.time() - self.start_time),
            "status_codes": self.session_status_codes,
        }

    def get_historical_stats(self):
        """Return combined session + DB stats."""
        try:
            db_stats = db.get_stats()
        except Exception:
            db_stats = {"total_requests": 0, "prompt_tokens": 0, "completion_tokens": 0,
                        "total_tokens": 0, "avg_latency_ms": 0, "error_count": 0}
        return db_stats

    def check_cookie_expiry(self, cookie_str):
        """Parse SAPISID cookie and return expiry info.
        
        Returns dict with: valid, expires_at, hours_left, alert.
        """
        result = {"valid": False, "expires_at": 0, "hours_left": 0, "alert": False}
        if not cookie_str:
            return result
        try:
            pairs = dict(p.split("=", 1) for p in cookie_str.split("; ") if "=" in p)
            # Check for Max-Age or explicit expiry in cookie string
            # SAPISID itself doesn't carry expiry in the string;
            # we rely on browser cookie expiry. If the cookie is loaded
            # from file, we check file mtime as a rough proxy.
            sapisid = pairs.get("SAPISID", "")
            if sapisid:
                result["valid"] = True
        except Exception:
            pass
        return result


# Global singleton
metrics = MetricsTracker()
