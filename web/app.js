const $ = (selector) => document.querySelector(selector);
const form = $("#analysis-form");
const runButton = $("#run-button");
const agentGrid = $("#agent-grid");
const progressBar = $("#progress-bar");
const progressLabel = $("#progress-label");
const stageLabel = $("#stage-label");
const statusBanner = $("#status-banner");
const formError = $("#form-error");
const elapsed = $("#elapsed");
let timer = null;
let startedAt = null;
let source = null;

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[char]);
}

function setProgress(value, stage) {
  const safe = Math.max(0, Math.min(100, Number(value) || 0));
  progressBar.style.width = `${safe}%`;
  progressLabel.textContent = `${safe}%`;
  if (stage) stageLabel.textContent = stage;
}

function setBanner(kind, title, detail) {
  statusBanner.className = `status-banner ${kind}`;
  statusBanner.innerHTML = `<div><span class="status-orb"></span><strong>${escapeHtml(title)}</strong></div><p>${escapeHtml(detail || "")}</p>`;
}

function startTimer() {
  startedAt = Date.now();
  clearInterval(timer);
  timer = setInterval(() => {
    const seconds = Math.floor((Date.now() - startedAt) / 1000);
    elapsed.textContent = `${String(Math.floor(seconds / 60)).padStart(2,"0")}:${String(seconds % 60).padStart(2,"0")}`;
  }, 1000);
}

function stopTimer() { clearInterval(timer); timer = null; }

function renderAgents(agents) {
  agentGrid.className = "agent-grid";
  agentGrid.innerHTML = agents.map((agent) => `<article class="agent-card waiting" data-agent="${escapeHtml(agent.id)}"><div class="avatar">${escapeHtml(agent.id.slice(0,2).toUpperCase())}</div><div><h3>${escapeHtml(agent.name)}</h3><p>Waiting for handoff</p></div><span class="state"></span></article>`).join("");
}

function updateAgent(data) {
  const card = agentGrid.querySelector(`[data-agent="${CSS.escape(data.id)}"]`);
  if (!card) return;
  card.className = `agent-card ${data.status}`;
  card.querySelector("p").textContent = data.message || data.status;
}

function renderResult(data) {
  $("#results").classList.remove("hidden");
  $("#rating").textContent = data.rating;
  $("#decision-panel").textContent = data.decision;
  $("#report-count").textContent = data.reports.length;
  $("#reports-panel").innerHTML = data.reports.map((report) => `<article class="report-card"><h3>${escapeHtml(report.title)}</h3><p>${escapeHtml(report.content)}</p></article>`).join("") || "<p>No detailed reports were returned.</p>";
  $("#results").scrollIntoView({behavior:"smooth",block:"start"});
}

function finish() {
  runButton.disabled = false;
  runButton.querySelector("span").textContent = "Run another analysis";
  stopTimer();
  source?.close();
}

document.querySelectorAll(".result-tabs button").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll(".result-tabs button").forEach((item) => item.classList.toggle("active", item === button));
  $("#decision-panel").classList.toggle("hidden", button.dataset.tab !== "decision");
  $("#reports-panel").classList.toggle("hidden", button.dataset.tab !== "reports");
}));

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  formError.textContent = "";
  const analysts = [...document.querySelectorAll('input[name="analysts"]:checked')].map((input) => input.value);
  if (!analysts.length) { formError.textContent = "Select at least one analyst."; return; }
  runButton.disabled = true;
  runButton.querySelector("span").textContent = "Deploying agents";
  $("#results").classList.add("hidden");
  elapsed.textContent = "00:00";
  setProgress(1,"Initializing");
  setBanner("running","Creating the mission","Validating inputs and preparing the agent graph.");
  startTimer();
  try {
    const response = await fetch("/api/analyses", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({ticker:$("#ticker").value,trade_date:$("#trade-date").value,analysts})});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Unable to start analysis.");
    source = new EventSource(`/api/analyses/${payload.job_id}/events`);
    source.addEventListener("agents", (e) => renderAgents(JSON.parse(e.data).agents));
    source.addEventListener("agent", (e) => updateAgent(JSON.parse(e.data)));
    source.addEventListener("status", (e) => { const data=JSON.parse(e.data); setProgress(data.progress,data.stage); setBanner("running",data.message,"Live updates from the multi-agent workflow."); });
    source.addEventListener("result", (e) => { const data=JSON.parse(e.data); setProgress(100,"Complete"); setBanner("complete","Analysis complete","The investment committee output is ready."); renderResult(data); });
    source.addEventListener("failure", (e) => { const data=JSON.parse(e.data); setBanner("error",data.title,data.message); formError.textContent=data.title; finish(); });
    source.addEventListener("done", finish);
    source.onerror = () => { if (source?.readyState === EventSource.CLOSED) return; setBanner("error","Connection interrupted","The live progress connection was lost. Check Railway logs and try again."); finish(); };
  } catch (error) {
    formError.textContent = error.message;
    setBanner("error","Unable to start analysis",error.message);
    finish();
  }
});

fetch("/api/meta").then((response) => response.json()).then((meta) => {
  $("#trade-date").value = meta.today;
  $("#trade-date").max = meta.today;
  $("#version").textContent = `TradingAgents ${meta.app_version} · UI ${meta.ui_version} · Build ${meta.build}`;
}).catch(() => { $("#trade-date").value = new Date().toISOString().slice(0,10); });
