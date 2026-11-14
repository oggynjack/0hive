# 🐝 0Hive

**A self-healing, multi-account OpenAI-compatible gateway for Gemini's web app** — with a sync
toolkit that keeps every account's login fresh automatically, and a live dashboard + chat
tester to run and watch it all.

Built on top of [gemini-web2api](https://github.com/Sophomoresty/gemini-web2api) by
**Sophomoresty** (MIT licensed) — credit where it's due. 0Hive adds:

- **Multi-account rotation with automatic failover** — add as many Google accounts as you want;
  if one hits a rate limit or goes stale, the next one picks up the request transparently.
- **A real account-sync toolkit**, in two flavors:
  - **CDP mode** (recommended): attaches to a real, already-running Brave/Chrome you use daily
    over its DevTools remote-debugging port. It opens brief background tabs to read whichever
    Google accounts are signed in, refreshing their session every 30 minutes — since it's your
    actual browser making every request, nothing looks automated to Google.
  - **Isolated-profile mode** (automatic fallback): a separate, dedicated browser profile you log
    into once; a background keeper re-exports fresh cookies from it forever.
  - **Browser extension**: one click sends every signed-in account from your browser to the
    sync server, identified by email, repeating automatically every 30 minutes.
- **A live dashboard** — requests, token usage, latency, per-account health, and account
  management, at `/dashboard.html`. Fully responsive (phone / tablet / desktop).
- **A built-in chat tester** at `/chat.html` — pick a model from the *live* list the server
  reports (never a hardcoded, possibly-outdated model name), send messages, drag in images.

> ⚠️ **This is an unofficial integration.** It works by reusing a real Gemini web session, which
> Google does not officially support for this purpose. Behavior, model names, and availability
> can change without notice, and an account could in principle be rate-limited or restricted for
> unusual traffic. Use accounts you're comfortable taking that risk with — never anything else's
> primary account — and keep automated traffic reasonable.

## Quick start

```bash
git clone https://github.com/oggynjack/0hive.git
cd 0hive
pip install -r requirements.txt
cp config.example.json config.json
# put a cookie file for at least one Google account in cookies/, referenced from config.json
python gemini_web2api.py
```

The API is now listening on `http://localhost:8081/v1` — OpenAI-compatible
(`/v1/chat/completions`, `/v1/models`).

### The dashboard + chat tester

```bash
python dashboard/serve_dashboard.py   # http://localhost:8083
```

Open `http://localhost:8083/dashboard.html` for live stats and account management, or
`http://localhost:8083/chat.html` to try it directly.

### Keeping accounts logged in automatically

See **[docs/ACCOUNT_SYNC.md](docs/ACCOUNT_SYNC.md)** for the full setup of either sync method
(CDP against your real browser, the isolated-profile keeper, or the browser extension).

### Docker

```bash
docker compose up -d --build
```

## How it works, briefly

Gemini's web app talks to Google over an internal RPC protocol, authenticated by your normal
browser session cookies. 0Hive replays that protocol as an OpenAI-shaped HTTP API, and the sync
toolkit's whole job is making sure those cookies never go stale — by either reading them straight
out of a real, continuously-used browser session (CDP mode) or by keeping one dedicated session
alive in the background (isolated-profile mode). `account_manager.py` then rotates across
however many accounts you've added, skipping unhealthy ones automatically.

## Project layout

```
gemini_web2api.py     the API server (core)
account_manager.py    multi-account rotation, health tracking
dashboard/            dashboard.html, chat.html, and their static server
sync/                 account-sync toolkit: keeper script, cookie receiver, browser extension
docs/                 setup guides
cloudflare/           optional Cloudflare Worker for exposing the API behind a domain
```

## Credits & license

- Core API protocol implementation: [gemini-web2api](https://github.com/Sophomoresty/gemini-web2api)
  by Sophomoresty.
- Multi-account sync toolkit, dashboard, chat tester, and related additions: 0Hive contributors.

MIT licensed — see [LICENSE](LICENSE).
