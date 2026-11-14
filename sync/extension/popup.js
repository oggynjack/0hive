const out = document.getElementById("out"), btn = document.getElementById("go");
function show(r) {
  if (r.error) { out.innerHTML = `<div class="err">Failed: ${r.error}</div>`; return; }
  const res = r.result || r;
  out.innerHTML = `<div class="ok">Sent ${res.emails.length} account(s)${res.newOnes.length ? ` (${res.newOnes.length} new)` : ""}:</div><ul>${res.emails.map(e => `<li>${e}${res.newOnes.includes(e) ? " <b>(new)</b>" : ""}</li>`).join("")}</ul><div style="color:#9aa">${new Date(res.time || Date.now()).toLocaleString()}</div>`;
}
chrome.storage.local.get(["lastSync", "lastError"], d => {
  if (d.lastError && (!d.lastSync || d.lastError.time > d.lastSync.time)) show({ error: d.lastError.message });
  else if (d.lastSync) show(d.lastSync);
});
btn.onclick = () => {
  btn.disabled = true; btn.textContent = "Sending...";
  chrome.runtime.sendMessage("sync", r => { btn.disabled = false; btn.textContent = "Send my accounts now"; show(r || { error: "No answer" }); });
};
