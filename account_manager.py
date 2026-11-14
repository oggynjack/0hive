"""
Multi-Account Manager for Gemini Web2API
- Auto-fallback between accounts
- Rate limiting per account
- Health monitoring
- Account rotation
"""
import json
import time
import os
import threading
from collections import deque
from typing import Optional, Dict, List, Tuple

class Account:
    def __init__(self, config: dict):
        self.name = config.get("name", "unnamed")
        self.cookie_file = config.get("cookie_file", "")
        self.auth_user = config.get("auth_user")
        self.priority = config.get("priority", 100)
        self.enabled = config.get("enabled", True)
        
        # Runtime state
        self.cookie_str = ""
        self.sapisid = None
        self.last_refresh = 0
        self.consecutive_errors = 0
        self.total_requests = 0
        self.total_errors = 0
        self.request_times = deque(maxlen=100)
        self.last_error = None
        self.last_used = 0
        self.is_healthy = True
        self.error_disabled = False
        
        # Rate limiting
        self.request_timestamps = deque(maxlen=1000)
        
        # Load initial cookie
        self.load_cookie()
    
    def load_cookie(self):
        """Load cookie from file"""
        if not self.cookie_file:
            return False
        
        cookie_path = self.cookie_file
        if not os.path.isabs(cookie_path):
            cookie_path = os.path.join(os.path.dirname(__file__), cookie_path)
        
        if not os.path.exists(cookie_path):
            return False
        
        try:
            with open(cookie_path, "r") as f:
                content = f.read().strip()
            
            if content.startswith("{"):
                data = json.loads(content)
                self.cookie_str = data.get("cookie", "")
                self.sapisid = data.get("sapisid", "")
            else:
                self.cookie_str = content
                pairs = dict(p.split("=", 1) for p in self.cookie_str.split("; ") if "=" in p)
                self.sapisid = pairs.get("SAPISID")
            
            self.last_refresh = time.time()
            return True
        except Exception as e:
            self.last_error = str(e)
            return False
    
    def can_use(self) -> bool:
        """Check if account can be used"""
        if not self.enabled:
            return False
        if self.error_disabled:
            return False
        if not self.cookie_str:
            return False
        return True
    
    def check_rate_limit(self, max_requests: int, window_sec: int) -> bool:
        """Check if account is within rate limit"""
        now = time.time()
        cutoff = now - window_sec
        
        # Remove old timestamps
        while self.request_timestamps and self.request_timestamps[0] < cutoff:
            self.request_timestamps.popleft()
        
        return len(self.request_timestamps) < max_requests
    
    def record_request(self, duration: float, success: bool):
        """Record a request"""
        self.request_timestamps.append(time.time())
        self.total_requests += 1
        self.last_used = time.time()
        self.request_times.append(duration)
        
        if success:
            self.consecutive_errors = 0
        else:
            self.consecutive_errors += 1
            self.total_errors += 1
            if self.consecutive_errors >= 5:
                self.error_disabled = True
                self.is_healthy = False
    
    def get_stats(self) -> dict:
        """Get account statistics"""
        avg_latency = sum(self.request_times) / len(self.request_times) if self.request_times else 0
        return {
            "name": self.name,
            "enabled": self.enabled,
            "healthy": self.is_healthy,
            "error_disabled": self.error_disabled,
            "total_requests": self.total_requests,
            "total_errors": self.total_errors,
            "consecutive_errors": self.consecutive_errors,
            "avg_latency_ms": round(avg_latency * 1000, 1),
            "last_used": self.last_used,
            "last_error": self.last_error,
            "has_cookie": bool(self.cookie_str)
        }
    
    def __repr__(self):
        return f"Account({self.name}, healthy={self.is_healthy}, requests={self.total_requests})"


class AccountManager:
    def __init__(self, config: dict):
        self.accounts: List[Account] = []
        self.current_index = 0
        self.lock = threading.Lock()
        self.features = config.get("features", {})
        
        # Load accounts
        for acc_config in config.get("accounts", []):
            account = Account(acc_config)
            self.accounts.append(account)
        
        # Sort by priority
        self.accounts.sort(key=lambda a: a.priority)
    
    def get_next_account(self, model: str = None) -> Optional[Account]:
        """Get next available account with fallback"""
        with self.lock:
            # Try each account
            for _ in range(len(self.accounts)):
                account = self.accounts[self.current_index]
                self.current_index = (self.current_index + 1) % len(self.accounts)
                
                if account.can_use():
                    # Check rate limit
                    max_req = self.features.get("rate_limit_per_account", 50)
                    window = self.features.get("rate_limit_window_sec", 60)
                    
                    if account.check_rate_limit(max_req, window):
                        return account
            
            return None
    
    def record_request(self, account: Account, duration: float, success: bool):
        """Record request for an account"""
        with self.lock:
            account.record_request(duration, success)
    
    def enable_account(self, name: str):
        """Re-enable a disabled account"""
        with self.lock:
            for account in self.accounts:
                if account.name == name:
                    account.error_disabled = False
                    account.is_healthy = True
                    account.consecutive_errors = 0
                    break
    
    def disable_account(self, name: str):
        """Manually disable an account"""
        with self.lock:
            for account in self.accounts:
                if account.name == name:
                    account.enabled = False
                    break
    
    def reload_account(self, name: str):
        """Reload cookie for an account"""
        with self.lock:
            for account in self.accounts:
                if account.name == name:
                    account.load_cookie()
                    account.error_disabled = False
                    account.is_healthy = True
                    account.consecutive_errors = 0
                    break
    
    def get_all_stats(self) -> dict:
        """Get statistics for all accounts"""
        return {
            "total_accounts": len(self.accounts),
            "healthy_accounts": sum(1 for a in self.accounts if a.is_healthy),
            "enabled_accounts": sum(1 for a in self.accounts if a.enabled),
            "accounts": [a.get_stats() for a in self.accounts]
        }
    
    def get_cookie_and_prefix(self, account: Account) -> Tuple[str, str, Optional[str]]:
        """Get cookie string, sapisid, and account prefix"""
        prefix = ""
        if account.auth_user is not None and account.auth_user != "":
            prefix = f"/u/{account.auth_user}"
        
        return account.cookie_str, account.sapisid, prefix


# ─── Global Instance ──────────────────────────────────────────────────────────
account_manager = None

def init_account_manager(config: dict):
    global account_manager
    account_manager = AccountManager(config)
    return account_manager
