# Keeping accounts logged in automatically

0Hive needs a valid Google session (cookies) per account. Sessions expire if nothing refreshes
them — usually within about a week of being unused. The sync toolkit in `sync/` solves this
three ways; pick whichever fits how you work.

## Option A — CDP mode (recommended): use your real, everyday browser

Instead of copying cookies anywhere, the keeper attaches directly to a browser you actually use
day to day, over its DevTools remote-debugging protocol, and reads whichever Google accounts are
signed in there. Since it's genuinely your own session making every request, this is the most
reliable option and needs no separate login.

**Security note:** the debug port gives *full control* of that browser window to whoever can
reach it — not just Google cookies. Only enable it on a port you've firewalled to trusted hosts
(e.g. your own machines over a private network/VPN), never exposed publicly.

1. Launch your browser with remote debugging enabled, bound to loopback only (most Chromium
   browsers restrict this to `127.0.0.1` regardless of what address you pass):
   ```
   brave.exe --remote-debugging-port=9334
   ```
2. If the keeper runs on a *different* machine than your browser, bridge the loopback port to it
   with an SSH reverse tunnel (works because both ends then see a `localhost` connection, which
   satisfies the browser's own security check):
   ```
   ssh -N -R 9334:127.0.0.1:9334 user@your-keeper-host
   ```
   Keep this tunnel alive with a small watchdog (restart it if the process dies) if you want it
   permanent.
3. Set `HIVE_CDP_URL` (default `http://127.0.0.1:9334`) and run the keeper:
   ```
   python sync/gemini_keeper.py
   ```
   It checks every 30 minutes, opens a brief background tab per signed-in account (doesn't
   disturb whatever you're doing in your other tabs), and writes `cookies/<email>.json` for each.

Add more accounts any time just by signing in to them in that same browser (its own account
switcher) — the next cycle picks them up automatically.

## Option B — Isolated profile (fallback, no real-browser access needed)

A separate, dedicated browser profile that only this project uses.

```
python sync/gemini_keeper.py --login
```

This opens a browser window. Sign in to every account you want via its account switcher, then
come back to the console and press **Enter**. From then on, run the keeper normally:

```
python sync/gemini_keeper.py
```

It automatically falls back to this mode whenever CDP mode (Option A) isn't reachable.

## Option C — Browser extension

Load `sync/extension/` as an unpacked extension in any Chromium-based browser (Developer mode →
Load unpacked). Set `RECEIVER` and `SYNC_KEY` in `sync/extension/config.js` to match a running
`sync/cookie_receiver.py` (it generates a random key on first run — copy it from
`sync/sync_key.txt`). Click the extension's button to send every signed-in account once; it also
repeats automatically every 30 minutes while the browser is open.

## Picking a model

Call `/v1/models` for the live list the server currently exposes — don't hardcode a model name
in your own code, since Google renames and retires tiers over time. `gemini-auto` is a safe
default that lets Gemini route to whatever its current all-around tier is.
