importScripts("config.js");

// Real tab navigation per account (not fetch()) -- Google appears to reject plain background
// fetch() requests to its account-switch endpoints even with valid cookies, but accepts a real
// page load. Each account's tab is opened in the BACKGROUND (active:false) so it never steals
// focus or interrupts whatever the user is doing in their current tab.

function waitForTabComplete(tabId, timeoutMs = 15000) {
  return new Promise((resolve) => {
    let done = false;
    const finish = () => { if (!done) { done = true; chrome.tabs.onUpdated.removeListener(listener); resolve(); } };
    function listener(id, info) { if (id === tabId && info.status === "complete") finish(); }
    chrome.tabs.onUpdated.addListener(listener);
    setTimeout(finish, timeoutMs);
  });
}

async function cookieJar() {
  // Every cookie the browser would actually attach to a gemini.google.com request, not a
  // hand-picked list -- newer Google sessions need extra cookies beyond the classic SID set.
  const all = await chrome.cookies.getAll({ url: "https://gemini.google.com/" });
  const extra = await chrome.cookies.getAll({ domain: "google.com" });
  const jar = {};
  for (const c of [...all, ...extra]) {
    if (!c.value) continue;
    if (!(c.name in jar) || c.domain === ".google.com") jar[c.name] = c.value;
  }
  return jar;
}

async function scanAccount(index) {
  const tab = await chrome.tabs.create({ url: `https://gemini.google.com/u/${index}/app`, active: false });
  try {
    await waitForTabComplete(tab.id);
    await new Promise((r) => setTimeout(r, 2500)); // let the SPA finish mounting (WIZ_global_data etc.)
    let result;
    try {
      [{ result }] = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: () => {
          const w = globalThis.WIZ_global_data || {};
          const h = document.documentElement.innerHTML;
          const x = w.SNlM0e || (h.match(/"SNlM0e"\s*:\s*"([^"\n]+)"/) || [])[1] || null;
          const b = w.cfb2h || (h.match(/"cfb2h"\s*:\s*"([^"\n]+)"/) || [])[1] || null;
          const email = (h.match(/"oPEP7c"\s*:\s*"([^"]+@[^"]+)"/) || [])[1] || null;
          return { x, b, email, url: location.href };
        },
      });
    } catch (e) {
      return null; // tab navigated away / closed before the script ran
    }
    if (!result || !result.x || result.url.includes("accounts.google.com")) return null;
    const jar = await cookieJar();
    if (!jar.SAPISID) return null;
    return {
      email: (result.email || "").toLowerCase() || null,
      auth_user: index,
      xsrf_token: result.x,
      gemini_bl: result.b,
      cookie: Object.entries(jar).map(([k, v]) => `${k}=${v}`).join("; "),
      sapisid: jar.SAPISID,
    };
  } finally {
    await chrome.tabs.remove(tab.id).catch(() => {});
  }
}

async function scanAllAccounts() {
  const accounts = [];
  const seen = new Set();
  for (let i = 0; i < 10; i++) {
    let a = null;
    try { a = await scanAccount(i); } catch (e) { a = null; }
    if (!a || !a.email || seen.has(a.email)) break;
    seen.add(a.email);
    accounts.push(a);
  }
  return accounts;
}

async function syncAll() {
  const accounts = await scanAllAccounts();
  if (!accounts.length) throw new Error("No Gemini-enabled Google account found. Make sure you are signed in to Google, then open gemini.google.com once.");
  const res = await fetch(`${RECEIVER}/sync`, { method: "POST", headers: { "Content-Type": "application/json", "X-Sync-Key": SYNC_KEY }, body: JSON.stringify({ accounts }) });
  if (!res.ok) throw new Error(`Server said ${res.status}: ${(await res.text()).slice(0, 100)}`);
  const out = await res.json();
  const result = { time: Date.now(), emails: accounts.map((a) => a.email), newOnes: out.new || [] };
  await chrome.storage.local.set({ lastSync: result, lastError: null });
  return result;
}

async function safeSync() {
  try { return { ok: true, result: await syncAll() }; }
  catch (e) { await chrome.storage.local.set({ lastError: { time: Date.now(), message: String(e.message || e) } }); return { ok: false, error: String(e.message || e) }; }
}

chrome.runtime.onMessage.addListener((msg, _s, send) => {
  if (msg === "sync") { safeSync().then(send); return true; }
});
chrome.runtime.onInstalled.addListener(() => chrome.alarms.create("auto", { periodInMinutes: AUTO_SYNC_MINUTES }));
chrome.runtime.onStartup.addListener(() => { chrome.alarms.create("auto", { periodInMinutes: AUTO_SYNC_MINUTES }); safeSync(); });
chrome.alarms.onAlarm.addListener((a) => { if (a.name === "auto") safeSync(); });
