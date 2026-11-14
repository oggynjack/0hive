"""0Hive account keeper: keeps every Google account you add logged in and synced, forever,
with zero manual cookie copying.

Two modes, in order of preference:

  1. CDP mode (recommended) -- attach to a REAL, already-running Brave/Chrome you use daily,
     via its DevTools remote-debugging port (see docs/ACCOUNT_SYNC.md for how to enable this
     safely). The keeper opens brief background tabs in YOUR real browser, reads whichever
     Google accounts are signed in there, and refreshes their cookies every 30 minutes. Since
     it's your real session making every request, Google never flags it as suspicious.

  2. Isolated-profile mode (fallback, used automatically if CDP isn't reachable) -- Playwright
     launches its OWN separate Brave profile under this project (browser-profile/), independent
     of your everyday browser.
       python sync/gemini_keeper.py --login    one-time: opens that profile so you can sign in to
                                                every account via Brave's own account switcher,
                                                then press Enter in this console when done.
       python sync/gemini_keeper.py --rescan   one-off refresh, no waiting.
       python sync/gemini_keeper.py            run forever (every 30 min), e.g. as a scheduled
                                                task / systemd service / pm2 process.

Either way, cookies are written to cookies/<email>.json and registered in config.json, so
gemini_web2api.py picks up every account automatically (a brand-new account triggers one
restart of the `gemini-web2api` pm2 process; existing accounts just reload their cookie file).
"""
import asyncio
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).parent.parent
HERE = Path(__file__).parent
PROFILE = ROOT / "browser-profile"
COOKIES_DIR = ROOT / "cookies"
CONFIG = ROOT / "config.json"
STATUS = HERE / "keeper_status.json"
EVERY = 30 * 60
PM2 = "pm2"  # relies on PATH; set an absolute path (e.g. r"C:\ProgramData\npm\pm2.cmd") if pm2 is not on PATH


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def status(ok, msg, accounts=None):
    STATUS.write_text(json.dumps({"ok": ok, "message": msg, "accounts": accounts or [],
                                  "time": datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")


def slug(email):
    return re.sub(r"[^a-z0-9]+", "_", email.lower()).strip("_")


async def all_cookies(ctx):
    """Every cookie Google set for this session (not a filtered list): newer sessions need more
    than the classic SID/SAPISID set."""
    jar = {}
    for c in await ctx.cookies(["https://gemini.google.com", "https://accounts.google.com", "https://www.google.com"]):
        if c.get("value") and (c["name"] not in jar or c["domain"] == ".google.com"):
            jar[c["name"]] = c["value"]
    return jar


async def snapshot_account(ctx, index):
    """Open Gemini as /u/<index>/app and export that account's data, or None if it doesn't exist /
    isn't signed in."""
    page = await ctx.new_page()
    try:
        await page.goto(f"https://gemini.google.com/u/{index}/app", wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(3500)
        if "accounts.google.com" in page.url or "/signin" in page.url:
            return None
        meta = await page.evaluate("""() => {
            const w = globalThis.WIZ_global_data || {}; const h = document.documentElement.innerHTML;
            let x = w.SNlM0e || (h.match(/"SNlM0e"\\s*:\\s*"([^"\\n]+)"/) || [])[1] || null;
            let b = w.cfb2h || (h.match(/"cfb2h"\\s*:\\s*"([^"\\n]+)"/) || [])[1] || null;
            let email = (h.match(/"oPEP7c"\\s*:\\s*"([^"]+@[^"]+)"/) || [])[1] || null;
            return {x, b, email};
        }""")
        if not meta.get("x"):
            return None
        jar = await all_cookies(ctx)
        if not jar.get("SAPISID"):
            return None
        email = (meta.get("email") or "").lower() or None
        return {"cookie": "; ".join(f"{k}={v}" for k, v in jar.items()), "sapisid": jar["SAPISID"],
                "auth_user": index, "xsrf_token": meta["x"], "gemini_bl": meta["b"], "email": email,
                "last_sync": datetime.now().isoformat(timespec="seconds")}
    finally:
        await page.close()


async def snapshot_all(ctx, max_accounts=10):
    """Loop /u/0, /u/1, ... until an index has no signed-in account, OR repeats an email we
    already saw (Google silently re-serves account 0 for an index past the last real account,
    rather than erroring -- without this check we'd keep "finding" account 0 forever)."""
    out = []
    seen = set()
    for i in range(max_accounts):
        try:
            data = await snapshot_account(ctx, i)
        except Exception as e:
            log(f"  index {i} failed: {str(e)[:100]}")
            data = None
        if not data or (data.get("email") and data["email"] in seen):
            break
        if data.get("email"):
            seen.add(data["email"])
        out.append(data)
    return out


def register(accounts):
    """Write each account's cookie file and make sure config.json lists it. Returns (names, new)."""
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    lst = cfg.setdefault("accounts", [])
    COOKIES_DIR.mkdir(exist_ok=True)
    names, new = [], []
    for a in accounts:
        email = a.get("email") or f"account{a['auth_user']}@unknown"
        rel = f"cookies/{slug(email)}.json"
        (ROOT / rel).write_text(json.dumps({k: a[k] for k in ("cookie", "sapisid", "auth_user", "xsrf_token", "gemini_bl", "email", "last_sync")}, indent=2), encoding="utf-8")
        entry = next((e for e in lst if e.get("name") == email or e.get("cookie_file") == rel), None)
        if entry is None:
            lst.append({"name": email, "cookie_file": rel, "auth_user": a["auth_user"], "priority": len(lst) + 1, "enabled": True})
            new.append(email)
        else:
            entry["auth_user"] = a["auth_user"]
            entry["enabled"] = True
        names.append(email)
    CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return names, new


def reload_accounts(names, new):
    if new:
        log(f"new account(s) {new}: restarting gemini-web2api")
        subprocess.run([PM2, "restart", "gemini-web2api"], capture_output=True, timeout=60)
        return
    import urllib.request
    for email in names:
        try:
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:8081/v1/accounts/{email}/reload", method="POST", data=b""), timeout=10).read()
        except Exception:
            pass


import os

CDP_URL = os.environ.get("HIVE_CDP_URL", "http://127.0.0.1:9334")  # see docs/ACCOUNT_SYNC.md
BRAVE_PATH = os.environ.get("HIVE_BROWSER_PATH", r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe")
# macOS: "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
# Linux:  "/usr/bin/brave-browser" (or swap channel for a plain Chromium/Chrome install)


class _CdpCtx:
    """Thin wrapper so a CDP-attached context can be used with `await ctx.close()` without
    closing the user's real browser -- only the remote-debugging connection is dropped."""
    def __init__(self, browser, ctx):
        self._browser = browser
        self._ctx = ctx

    def new_page(self):
        return self._ctx.new_page()

    def cookies(self, urls):
        return self._ctx.cookies(urls)

    async def close(self):
        await self._browser.close()  # disconnects only; the real Brave window stays open


async def try_cdp(p):
    """Attach to the user's real daily Brave over the tunneled CDP port. Returns a context-like
    object on success, or None if the tunnel/Brave isn't reachable right now."""
    try:
        browser = await p.chromium.connect_over_cdp(CDP_URL, timeout=8000)
        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
        return _CdpCtx(browser, ctx)
    except Exception as e:
        log(f"CDP (real Brave) not reachable ({str(e)[:100]}); falling back to the isolated profile")
        return None


async def launch(p, headless):
    # Uses Brave (the user's actual browser), in its OWN isolated profile under this project
    # (not the user's real daily Brave profile) -- log in here once; the keeper then IS the
    # browser making every request, so there is no cookie-export/replay step to go wrong.
    return await p.chromium.launch_persistent_context(
        str(PROFILE), executable_path=BRAVE_PATH, headless=headless, viewport={"width": 1200, "height": 800},
        args=["--disable-blink-features=AutomationControlled"] + (["--headless=new"] if headless else []),
        ignore_default_args=["--enable-automation"])


async def login():
    async with async_playwright() as p:
        ctx = await launch(p, False)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto("https://gemini.google.com/app")
        log("Sign in to as many Google accounts as you want, using Brave's own account switcher")
        log("(top-right avatar -> Add account). I will NOT touch this window while you do that.")
        log("")
        log(">>> When you are done adding accounts, come back here and press ENTER. <<<")
        # Wait for the user to press Enter in this console -- nothing runs in the browser while
        # they are signing in, so it never interrupts them or steals the tab.
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, input, "")
        log("Checking which accounts are signed in (opens a few tabs briefly)...")
        try:
            accounts = await snapshot_all(ctx)
        except Exception as e:
            log(f"Could not read accounts: {e}")
            accounts = []
        if accounts:
            names, new = register(accounts)
            status(True, f"{len(names)} account(s) logged in", names)
            log(f"Found {len(names)} account(s): {names}")
            log("DONE. You can close this window now; the background keeper takes over from here.")
        else:
            log("No signed-in account was found. Make sure you actually signed in before pressing Enter, then run this again.")
        await ctx.close()


async def keep():
    log("Gemini multi-account keeper started (prefers the real Brave over CDP, falls back to the isolated profile)")
    async with async_playwright() as p:
        while True:
            via = "cdp"
            ctx = await try_cdp(p)
            if ctx is None:
                via = "isolated-profile"
                try:
                    ctx = await launch(p, True)
                except Exception as e:
                    status(False, f"error: {str(e)[:150]}")
                    log(f"error: {e}")
                    await asyncio.sleep(EVERY)
                    continue
            try:
                accounts = await snapshot_all(ctx)
                if accounts:
                    names, new = register(accounts)
                    reload_accounts(names, new)
                    status(True, f"ok (via {via})", names)
                    log(f"refreshed {len(names)} account(s) via {via}: {names}")
                else:
                    status(False, "NOT LOGGED IN - run sync\\Gemini-Login.bat on this PC")
                    log(f"not logged in via {via}")
            except Exception as e:
                status(False, f"error: {str(e)[:150]}")
                log(f"error: {e}")
            finally:
                await ctx.close()
            await asyncio.sleep(EVERY)


async def rescan():
    """One clean scan, no waiting -- prefers the real Brave over CDP, falls back to the isolated
    profile. Used to fix up accounts without requiring the user to log in again."""
    async with async_playwright() as p:
        via = "cdp"
        ctx = await try_cdp(p)
        if ctx is None:
            via = "isolated-profile"
            ctx = await launch(p, True)
        accounts = await snapshot_all(ctx)
        if accounts:
            names, new = register(accounts)
            status(True, f"ok (via {via})", names)
            log(f"rescanned {len(names)} account(s) via {via}: {names}")
        else:
            status(False, "NOT LOGGED IN")
            log(f"not logged in via {via}")
        await ctx.close()


if __name__ == "__main__":
    if "--login" in sys.argv:
        asyncio.run(login())
    elif "--rescan" in sys.argv:
        asyncio.run(rescan())
    else:
        asyncio.run(keep())
