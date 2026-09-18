import { api } from "./api.js";
import { drawResourceChart } from "./charts.js";
import { bytes, code, compact, coverageBadge, duration, escapeAttribute, escapeHtml, number, percent, statusBadge } from "./format.js";

const state = {
  runs: [], trackOptions: [], selectedRun: null, selectedRecord: null,
  records: [], currentRun: null, currentRecord: null, selectedGroup: null,
  detailTab: "overview", includeHidden: false, editingAnnotationId: null,
};
const $ = (id) => document.getElementById(id);

function renderInlineMarkdown(value, assets = []) {
  let text = String(value ?? "");
  const assetByPath = new Map();
  for (const asset of assets || []) {
    const relative = String(asset.relative_path || ""); const fileName = relative.split("/").pop();
    if (relative) assetByPath.set(relative, asset.url); if (fileName) assetByPath.set(fileName, asset.url);
    const imagePath = relative.split("/images/").pop(); if (imagePath) assetByPath.set(`images/${imagePath}`, asset.url);
  }
  text = text.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g, (match, alt, src) => assetByPath.has(src) ? `\n<img class="asset-image" src="${escapeAttribute(assetByPath.get(src))}" alt="${escapeAttribute(alt || "benchmark image")}" loading="lazy">\n` : match);
  return text.split(/(<img class="asset-image"[^>]*>)/g).map((part) => part.startsWith("<img ") ? part : escapeHtml(part)).join("");
}

function pct(progress) { const total = Number(progress?.total || 0); return total ? Math.max(0, Math.min(100, Math.round((Number(progress?.completed || 0) / total) * 100))) : 0; }
function groupLabel(groupId) { return ({ single_llm_skills_on: "Skills on", single_llm_skills_off: "Skills off", chemqa_skills_on: "ChemQA" })[groupId] || groupId; }
function scoreValue(group) { const value = group?.evaluation?.normalized_score ?? group?.evaluation?.score; return Number.isFinite(Number(value)) ? Number(value) : null; }
function coverageText(coverage = {}) { const keys = ["timing", "tokens", "tools", "packages", "resources"]; return keys.map((key) => coverage[key]?.exact !== undefined ? `${key.slice(0, 3)} ${coverage[key].exact}/${coverage[key].total}` : `${key.slice(0, 3)} ${coverage[key] || "—"}`).join(" · "); }

function renderGroupDuration(diagnostics = {}) {
  const agentDuration = diagnostics.agent_duration_seconds;
  if (typeof agentDuration === "number" && Number.isFinite(agentDuration)) return `answer ${Math.round(agentDuration)}s`;
  return `elapsed ${Math.round(Number(diagnostics.elapsed_seconds) || 0)}s`;
}

function renderRecordScoreBadges(groupResults = []) {
  return groupResults.map((item) => `<span class="score-chip ${escapeAttribute(item.outcome || "")}"><b>${escapeHtml(groupLabel(item.group_id))}</b>${escapeHtml(String(item.score_label || "pending").replace(/^Verifier\s+/i, ""))}</span>`).join("");
}

function renderRunScoreComparison(run) {
  const groups = run.summary?.groups || {}; const on = groups.single_llm_skills_on?.avg_normalized_score; const off = groups.single_llm_skills_off?.avg_normalized_score;
  if (!Number.isFinite(Number(on)) && !Number.isFinite(Number(off))) return "";
  const parts = []; if (Number.isFinite(Number(on))) parts.push(`on ${Number(on).toFixed(2)}`); if (Number.isFinite(Number(off))) parts.push(`off ${Number(off).toFixed(2)}`);
  if (Number.isFinite(Number(on)) && Number.isFinite(Number(off))) parts.push(`Δ ${(Number(on) - Number(off) >= 0 ? "+" : "")}${(Number(on) - Number(off)).toFixed(2)}`);
  return parts.join(" · ");
}

function renderFilterOptions() {
  const selected = $("track-filter").value;
  $("track-filter").innerHTML = [`<option value="">All tracks</option>`, ...state.trackOptions.map((value) => `<option value="${escapeAttribute(value)}" ${value === selected ? "selected" : ""}>${escapeHtml(value)}</option>`)].join("");
}

function filteredRuns() {
  const query = $("search-input").value.trim().toLowerCase(); const status = $("status-filter").value; const track = $("track-filter").value;
  return state.runs.filter((run) => (!status || run.status === status) && (!track || (run.tracks || []).includes(track)) && (!query || `${run.run_id} ${run.alias || ""} ${(run.tracks || []).join(" ")}`.toLowerCase().includes(query)));
}

function renderRuns() {
  const runs = filteredRuns(); $("run-count").textContent = String(runs.length);
  $("run-list").innerHTML = runs.map((run) => `<button class="run-row ${state.selectedRun === run.run_id ? "active" : ""}" data-run="${escapeAttribute(run.run_id)}"><div class="run-row-top"><span class="run-name">${run.favorite ? "★ " : ""}${escapeHtml(run.alias || run.run_id)}</span>${statusBadge(run.status)}</div><div class="run-meta"><span>${run.progress?.completed || 0}/${run.progress?.total || 0}</span><span>${escapeHtml((run.tracks || []).join(", ") || "unknown")}</span></div><div class="progress-track"><i style="width:${pct(run.progress)}%"></i></div><div class="run-score">${escapeHtml(renderRunScoreComparison(run))}</div></button>`).join("") || `<p class="empty-copy">No matching runs</p>`;
  document.querySelectorAll(".run-row").forEach((row) => row.addEventListener("click", () => selectRun(row.dataset.run)));
}

function renderProgress(progress = {}) {
  const current = Object.entries(progress.groups || {}).filter(([, value]) => value.current_record_id).map(([group, value]) => `${groupLabel(group)} · ${value.current_record_id}`);
  $("progress-strip").innerHTML = `<div class="progress-summary"><span>${statusBadge(progress.status)}</span><strong>${progress.completed || 0}<small> / ${progress.total || 0}</small></strong><span>${pct(progress)}%</span></div><div class="progress-track large"><i style="width:${pct(progress)}%"></i></div><div class="current-work">${current.length ? current.map((item) => `<span>${escapeHtml(item)}</span>`).join("") : `<span>Idle</span>`}</div>`;
}

function renderComparison() {
  const observed = state.currentRun?.observability?.groups || {}; const scores = state.currentRun?.payload?.summary?.groups || {};
  const ids = [...new Set([...Object.keys(scores), ...Object.keys(observed)])];
  $("comparison-body").innerHTML = ids.map((id) => { const metrics = observed[id] || {}; const score = scores[id]?.avg_normalized_score; const timing = metrics.timing || {}; const coverage = metrics.coverage || {}; const exact = (key) => Number(coverage[key]?.exact || 0) > 0; return `<tr><td><strong>${escapeHtml(groupLabel(id))}</strong><small>${escapeHtml(id)}</small></td><td>${number(score, 3)}</td><td>${exact("timing") ? `${duration(timing.mean_seconds)} <small>/ ${duration(timing.p95_seconds)}</small>` : "—"}</td><td>${exact("tokens") ? compact(metrics.tokens) : "—"}</td><td>${exact("tools") ? `${number(metrics.tool_failures, 0)} <small>${percent(metrics.tool_failure_rate)}</small>` : "—"}</td><td>${exact("packages") ? `${number(metrics.unique_added_package_count, 0)} <small>${number(metrics.failed_installs, 0)} failed</small>` : "—"}</td><td>${exact("resources") ? bytes(metrics.memory_peak) : "—"}</td><td><span class="coverage-line">${escapeHtml(coverageText(coverage))}</span></td></tr>`; }).join("") || `<tr><td colspan="8" class="empty-cell">Metrics pending</td></tr>`;
}

function renderActiveAttempts(monitor = {}) {
  const attempts = monitor.active_attempts || []; $("active-count").textContent = `${attempts.length} active`;
  $("active-attempts").innerHTML = attempts.map((attempt) => { const identity = attempt.identity || {}; const resource = attempt.latest_resource_window || {}; return `<article class="active-attempt ${attempt.stale ? "stale" : ""}"><header><div><strong>${escapeHtml(identity.record_id)}</strong><span>${escapeHtml(groupLabel(identity.group_id))} · attempt ${Number(identity.attempt_index || 0) + 1}</span></div>${statusBadge(attempt.stale ? "stale" : "running")}</header><dl><div><dt>CPU peak</dt><dd>${number(resource.cpu_peak_percent, 1)}%</dd></div><div><dt>Memory</dt><dd>${bytes(resource.memory_peak_bytes)}</dd></div><div><dt>PIDs</dt><dd>${number(resource.pids_peak, 0)}</dd></div><div><dt>Heartbeat</dt><dd>${duration(attempt.heartbeat_age_seconds)} ago</dd></div></dl></article>`; }).join("") || `<p class="empty-copy inline">No active attempts</p>`;
}

function primaryObservation(record) { return (record.group_results || []).find((item) => item.group_id === "single_llm_skills_on")?.observability || record.group_results?.[0]?.observability || {}; }
function primaryScore(record) { const item = (record.group_results || []).find((row) => row.group_id === "single_llm_skills_on") || record.group_results?.[0]; const match = String(item?.score_label || "").match(/[0-9.]+/); return match ? Number(match[0]) : Infinity; }

function sortedRecords() {
  const query = $("search-input").value.trim().toLowerCase(); const mode = $("record-sort").value;
  const rows = state.records.filter((record) => !query || `${record.record_id} ${record.track}`.toLowerCase().includes(query));
  return rows.sort((a, b) => { const ao = primaryObservation(a); const bo = primaryObservation(b); if (mode === "time-desc") return Number(bo.record_wall_seconds || 0) - Number(ao.record_wall_seconds || 0); if (mode === "tokens-desc") return Number(bo.total_tokens || 0) - Number(ao.total_tokens || 0); if (mode === "failures-desc") return Number(bo.tool_failure_count || 0) - Number(ao.tool_failure_count || 0); if (mode === "score-asc") return primaryScore(a) - primaryScore(b); return a.record_id.localeCompare(b.record_id); });
}

function renderRecords() {
  const records = sortedRecords(); $("record-count").textContent = `${records.length} records`;
  $("record-list").innerHTML = records.map((record) => { const obs = primaryObservation(record); const coverage = obs.coverage || {}; return `<tr class="record-row" tabindex="0" data-record="${escapeAttribute(record.record_id)}"><td><strong>${escapeHtml(record.record_id)}</strong><small>${escapeHtml(record.eval_kind || "")}</small></td><td>${escapeHtml(record.track || "—")}</td><td><span class="score-strip">${renderRecordScoreBadges(record.group_results || [])}</span></td><td>${duration(obs.record_wall_seconds)}</td><td>${compact(obs.total_tokens)}</td><td>${number(obs.tool_failure_count, 0)} <small>/ ${number(obs.tool_call_count, 0)}</small></td><td>${number(obs.added_package_count, 0)}</td><td>${coverageBadge(coverage.tools)} ${coverageBadge(coverage.resources)}</td></tr>`; }).join("") || `<tr><td colspan="8" class="empty-cell">No matching records</td></tr>`;
  document.querySelectorAll(".record-row").forEach((row) => { row.addEventListener("click", () => selectRecord(row.dataset.record)); row.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") selectRecord(row.dataset.record); }); });
}

async function loadRuns({ preserveSelection = true } = {}) {
  const selected = preserveSelection ? state.selectedRun : null;
  [state.trackOptions, state.runs] = await Promise.all([api("/api/tracks"), api(`/api/runs${state.includeHidden ? "?include_hidden=true" : ""}`)]);
  renderFilterOptions(); renderRuns();
  if (selected && state.runs.some((run) => run.run_id === selected)) return;
  if (!state.selectedRun && state.runs.length) await selectRun(state.runs[0].run_id);
}

async function selectRun(runId) {
  state.selectedRun = runId; state.selectedRecord = null; state.currentRecord = null;
  [state.currentRun, state.records] = await Promise.all([api(`/api/runs/${encodeURIComponent(runId)}`), api(`/api/runs/${encodeURIComponent(runId)}/records`)]);
  $("empty-view").hidden = true; $("run-view").hidden = false; $("run-overview").hidden = false; $("record-detail").hidden = true; $("record-back-button").hidden = true;
  $("run-title").textContent = state.currentRun.alias || runId;
  const runStatus = String(state.currentRun.progress?.status || "unknown");
  $("run-status").className = `status-badge ${runStatus}`;
  $("run-status").innerHTML = `<i></i>${escapeHtml(runStatus)}`;
  $("run-subtitle").textContent = `${state.currentRun.payload?.records || state.records.length} records · ${(state.records.map((item) => item.track).filter(Boolean).filter((value, index, all) => all.indexOf(value) === index)).join(", ")}`;
  $("favorite-run").textContent = state.currentRun.favorite ? "★" : "☆"; $("hide-run").textContent = state.currentRun.hidden ? "↩" : "⌫";
  renderRuns(); renderProgress(state.currentRun.progress); renderComparison(); renderRecords(); await refreshMonitor();
}

function selectedGroup() { return state.currentRecord?.groups?.find((group) => group.group_id === state.selectedGroup) || state.currentRecord?.groups?.[0] || null; }

async function selectRecord(recordId) {
  state.selectedRecord = recordId; state.currentRecord = await api(`/api/runs/${encodeURIComponent(state.selectedRun)}/records/${encodeURIComponent(recordId)}`); state.selectedGroup = state.currentRecord.groups?.[0]?.group_id || null; state.detailTab = "overview";
  $("run-overview").hidden = true; $("record-detail").hidden = false; $("record-back-button").hidden = false; $("record-title").textContent = state.currentRecord.record_id; $("record-subtitle").textContent = `${state.currentRecord.track} · ${state.currentRecord.eval_kind}`;
  window.scrollTo(0, 0);
  renderGroupSelector(); renderRecordDetail();
}

function renderGroupSelector() {
  $("group-selector").innerHTML = (state.currentRecord.groups || []).map((group) => `<button role="tab" aria-selected="${group.group_id === state.selectedGroup}" class="${group.group_id === state.selectedGroup ? "active" : ""}" data-group="${escapeAttribute(group.group_id)}"><span>${escapeHtml(groupLabel(group.group_id))}</span><b>${escapeHtml(group.score_label || "pending")}</b></button>`).join("");
  document.querySelectorAll("#group-selector button").forEach((button) => button.addEventListener("click", () => { state.selectedGroup = button.dataset.group; renderGroupSelector(); renderRecordDetail(); }));
}

function metricTile(label, value, meta = "") { return `<div class="kpi"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong>${meta ? `<small>${escapeHtml(meta)}</small>` : ""}</div>`; }

function renderRecordDetail() {
  const group = selectedGroup(); if (!group) return; const obs = group.observability || {}; const totals = obs.totals || {}; const timing = totals.timing || {}; const tokens = totals.tokens || {}; const tools = totals.tools || {}; const packages = totals.packages || {}; const resources = totals.resources || {};
  $("record-kpis").innerHTML = [metricTile("Score", group.evaluation?.normalized_score === undefined ? group.score_label : number(group.evaluation.normalized_score, 3)), metricTile("Wall time", duration(timing.record_wall_seconds), `${obs.attempt_count || 0} attempts`), metricTile("Tokens", compact(tokens.total_tokens), `${compact(tokens.cache_read_tokens)} cache read`), metricTile("Tool failures", `${number(tools.failure_count, 0)} / ${number(tools.call_count, 0)}`, percent(tools.failure_rate)), metricTile("Packages", number(packages.added_package_count, 0), `${number(packages.failed_install_event_count, 0)} failed installs`), metricTile("Peak memory", bytes(resources.memory_peak_bytes), `${number(resources.cpu_peak_percent, 1)}% CPU`)].join("");
  document.querySelectorAll("#detail-tabs button").forEach((button) => button.classList.toggle("active", button.dataset.tab === state.detailTab)); renderDetailPanel();
}

function renderOverview(group) {
  const reference = state.currentRecord.reference || {}; const annotations = state.currentRecord.annotations || [];
  return `<div class="overview-grid"><section><h4>Question</h4><div class="document-text">${renderInlineMarkdown(state.currentRecord.question_markdown || state.currentRecord.prompt, state.currentRecord.assets || [])}</div><h4>Reference</h4><div class="document-text">${escapeHtml(reference.answer || "Unavailable")}</div></section><section><h4>Agent answer</h4><div class="document-text answer-text">${escapeHtml(group.answer_text || "No answer")}</div><h4>Review notes</h4><div class="note-list">${annotations.map((item) => `<article><header><b>${escapeHtml(item.status || "note")}</b><button class="note-edit text-button" data-note="${item.id}">Edit</button></header><p>${escapeHtml(item.note || "")}</p></article>`).join("") || `<p class="empty-copy inline">No review notes</p>`}</div></section></div>`;
}

function renderTimeline(group) {
  const obs = group.observability || {}; const timing = obs.totals?.timing || {};
  const attempts = (obs.attempts || []).map((attempt) => `<article class="timeline-item"><i></i><header><strong>Attempt ${Number(attempt.attempt_index || 0) + 1}</strong>${statusBadge(attempt.status)}</header><dl><div><dt>Wall</dt><dd>${duration(attempt.timing?.attempt_wall_seconds)}</dd></div><div><dt>Agent</dt><dd>${duration(attempt.timing?.agent_seconds)}</dd></div><div><dt>Tokens</dt><dd>${compact(attempt.tokens?.total_tokens)}</dd></div><div><dt>Exec</dt><dd>${number(attempt.tools?.exec_call_count, 0)} / ${number(attempt.tools?.exec_failure_count, 0)} failed</dd></div></dl></article>`).join("");
  return `<div class="timing-breakdown">${metricTile("Attempts", number(obs.attempt_count, 0))}${metricTile("Retry backoff", duration(timing.retry_backoff_seconds))}${metricTile("Scoring wait", duration(timing.scoring_wait_seconds))}${metricTile("Scoring", duration(timing.scoring_seconds))}${metricTile("Overhead", duration(timing.overhead_seconds))}</div><div class="timeline">${attempts || `<p class="empty-copy">No attempt timeline in this artifact</p>`}</div>`;
}

function allExecCalls(group) { return (group.observability?.attempts || []).flatMap((attempt) => (attempt.tools?.exec_calls || []).map((call) => ({ ...call, attempt_index: attempt.attempt_index }))); }
function renderExec(group) {
  const calls = allExecCalls(group);
  return `<div class="sub-toolbar"><input id="exec-search" type="search" placeholder="Filter commands"><select id="exec-status"><option value="">All states</option>${["success", "failure", "blocked", "timeout", "cancelled", "incomplete"].map((value) => `<option>${value}</option>`).join("")}</select></div><div class="table-scroll"><table class="data-table exec-table"><thead><tr><th>#</th><th>Command</th><th>Status</th><th>Duration</th><th>Exit</th><th>CWD</th></tr></thead><tbody id="exec-body">${renderExecRows(calls)}</tbody></table></div>`;
}
function renderExecRows(calls) { return calls.map((call) => `<tr data-command="${escapeAttribute(call.command)}" data-status="${escapeAttribute(call.status)}"><td>${Number(call.attempt_index || 0) + 1}.${call.sequence}</td><td><details><summary>${code(call.command)}</summary><pre>${escapeHtml(call.result_excerpt || "No result output")}</pre></details></td><td>${statusBadge(call.status)}${call.failure_kind ? `<small>${escapeHtml(call.failure_kind)}</small>` : ""}</td><td>${duration(Number(call.duration_ms) / 1000)}</td><td>${call.exit_code ?? "—"}</td><td>${code(call.effective_cwd || call.requested_cwd || "—")}</td></tr>`).join("") || `<tr><td colspan="6" class="empty-cell">No exec calls recorded</td></tr>`; }

function renderPackages(group) {
  const attempts = group.observability?.attempts || [];
  return attempts.map((attempt) => { const packages = attempt.packages || {}; const delta = packages.delta || {}; const installs = packages.install_events || []; const direct = new Set(packages.direct_added_packages || []); return `<section class="attempt-section"><div class="section-heading"><h4>Attempt ${Number(attempt.attempt_index || 0) + 1}</h4>${coverageBadge(attempt.coverage?.packages)}</div><div class="package-summary"><div><span>Added</span><strong>${number(delta.added?.length, 0)}</strong></div><div><span>Changed</span><strong>${number(delta.version_changed?.length, 0)}</strong></div><div><span>Policy removed</span><strong>${number(delta.policy_removed?.length, 0)}</strong></div></div><h4>Direct requests</h4><div class="package-tags direct">${(delta.added || []).filter((item) => direct.has(item.name)).map((item) => `<span>${escapeHtml(item.name)} <b>${escapeHtml(item.version)}</b></span>`).join("") || `<i>No direct additions</i>`}</div><h4>Transitive additions</h4><div class="package-tags">${(delta.added || []).filter((item) => !direct.has(item.name)).map((item) => `<span>${escapeHtml(item.name)} <b>${escapeHtml(item.version)}</b></span>`).join("") || `<i>No transitive additions</i>`}</div><div class="table-scroll"><table class="data-table"><thead><tr><th>Requested command</th><th>Outcome</th></tr></thead><tbody>${installs.map((event) => `<tr><td>${code(event.command)}</td><td>${statusBadge(event.outcome === "succeeded" ? "success" : event.outcome)}</td></tr>`).join("") || `<tr><td colspan="2" class="empty-cell">No install events</td></tr>`}</tbody></table></div></section>`; }).join("") || `<p class="empty-copy">Package evidence unavailable</p>`;
}

function renderTokens(group) {
  const total = group.observability?.totals?.tokens || {}; const attempts = group.observability?.attempts || [];
  const tokenKeys = [["Input", "input_tokens"], ["Output", "output_tokens"], ["Cache read", "cache_read_tokens"], ["Cache write", "cache_write_tokens"], ["Reasoning", "reasoning_tokens"], ["Billable total", "total_tokens"]];
  const rows = attempts.flatMap((attempt) => { const invocations = attempt.tokens?.invocations || []; return (invocations.length ? invocations : [attempt.tokens || {}]).map((usage) => ({ ...usage, attempt_index: attempt.attempt_index })); });
  return `<div class="token-grid">${tokenKeys.map(([label, key]) => metricTile(label, compact(total[key]))).join("")}</div><div class="table-scroll"><table class="data-table"><thead><tr><th>Attempt</th><th>Invocation</th><th>Source</th><th>Input</th><th>Output</th><th>Cache read</th><th>Reasoning</th><th>Total</th><th>Consistent</th></tr></thead><tbody>${rows.map((usage) => `<tr><td>${Number(usage.attempt_index || 0) + 1}</td><td>${escapeHtml(usage.invocation_kind || usage.session_id || "primary")}</td><td>${escapeHtml(usage.source || "—")}</td><td>${compact(usage.input_tokens)}</td><td>${compact(usage.output_tokens)}</td><td>${compact(usage.cache_read_tokens)}</td><td>${compact(usage.reasoning_tokens)}</td><td>${compact(usage.total_tokens)}</td><td>${usage.provider_total_consistent === false ? statusBadge("mismatch") : "yes"}</td></tr>`).join("")}</tbody></table></div>`;
}

async function renderResources(group, selectedIndex = null) {
  const attempts = group.observability?.attempts || []; const selected = selectedIndex === null ? attempts[attempts.length - 1] : attempts.find((attempt) => Number(attempt.attempt_index || 0) === Number(selectedIndex));
  if (!selected) { $("detail-panel").innerHTML = `<p class="empty-copy">Resource telemetry unavailable</p>`; return; }
  const summary = selected.resources || {};
  $("detail-panel").innerHTML = `<div class="sub-toolbar"><label class="compact-select">Attempt<select id="resource-attempt">${attempts.map((attempt) => `<option value="${Number(attempt.attempt_index || 0)}" ${attempt === selected ? "selected" : ""}>Attempt ${Number(attempt.attempt_index || 0) + 1}</option>`).join("")}</select></label>${coverageBadge(selected.coverage?.resources)}</div><div class="resource-kpis">${metricTile("CPU average", `${number(summary.cpu_avg_percent, 1)}%`)}${metricTile("CPU peak", `${number(summary.cpu_peak_percent, 1)}%`)}${metricTile("Memory average", bytes(summary.memory_avg_bytes))}${metricTile("Memory peak", bytes(summary.memory_peak_bytes))}${metricTile("Network RX / TX", `${bytes(summary.network_rx_bytes)} / ${bytes(summary.network_tx_bytes)}`)}${metricTile("Block read / write", `${bytes(summary.block_read_bytes)} / ${bytes(summary.block_write_bytes)}`)}${metricTile("Peak PIDs", number(summary.pids_peak, 0))}${metricTile("Samples", number(summary.sample_count, 0))}</div><canvas id="resource-chart" class="resource-chart" aria-label="CPU and memory resource timeline"></canvas>`;
  $("resource-attempt").addEventListener("change", (event) => renderResources(group, Number(event.target.value)));
  const response = await api(selected.resources_url); drawResourceChart($("resource-chart"), response.points || []);
}

function renderEvidence(group) {
  const isolation = group.workspace_isolation || {}; const diagnostics = group.diagnostics || {}; const error = diagnostics.execution_error || {};
  return `<div class="evidence-grid"><section><h4>Status axes</h4><pre>${escapeHtml(JSON.stringify(group.status_axes || {}, null, 2))}</pre></section><section><h4>Workspace isolation</h4><pre>${escapeHtml(JSON.stringify(isolation, null, 2))}</pre></section><section><h4>Execution error</h4><pre>${escapeHtml(JSON.stringify(error, null, 2))}</pre></section><section><h4>Observability coverage</h4><pre>${escapeHtml(JSON.stringify(group.observability?.coverage || {}, null, 2))}</pre></section></div>`;
}

function renderDetailPanel() {
  const group = selectedGroup(); if (!group) return;
  if (state.detailTab === "overview") $("detail-panel").innerHTML = renderOverview(group);
  if (state.detailTab === "timeline") $("detail-panel").innerHTML = renderTimeline(group);
  if (state.detailTab === "exec") { $("detail-panel").innerHTML = renderExec(group); const filter = () => { const query = $("exec-search").value.toLowerCase(); const status = $("exec-status").value; document.querySelectorAll("#exec-body tr[data-command]").forEach((row) => { row.hidden = Boolean((query && !row.dataset.command.toLowerCase().includes(query)) || (status && row.dataset.status !== status)); }); }; $("exec-search").addEventListener("input", filter); $("exec-status").addEventListener("change", filter); }
  if (state.detailTab === "packages") $("detail-panel").innerHTML = renderPackages(group);
  if (state.detailTab === "tokens") $("detail-panel").innerHTML = renderTokens(group);
  if (state.detailTab === "resources") renderResources(group).catch((error) => { $("detail-panel").innerHTML = `<p class="error-copy">${escapeHtml(error.message)}</p>`; });
  if (state.detailTab === "evidence") $("detail-panel").innerHTML = renderEvidence(group);
  document.querySelectorAll(".note-edit").forEach((button) => button.addEventListener("click", () => openAnnotation(Number(button.dataset.note))));
}

async function refreshMonitor() {
  if (!state.selectedRun) return;
  try { const monitor = await api(`/api/runs/${encodeURIComponent(state.selectedRun)}/monitor`); renderProgress(monitor.progress); renderActiveAttempts(monitor); if (state.currentRun) state.currentRun.observability = monitor.observability; renderComparison(); } catch (error) { console.error(error); }
}
async function refreshProgress() { return refreshMonitor(); }

async function refreshRuns() { const button = $("refresh-button"); button.classList.add("is-refreshing"); button.disabled = true; button.setAttribute("aria-busy", "true"); try { await loadRuns(); if (state.selectedRun) await selectRun(state.selectedRun); } finally { button.classList.remove("is-refreshing"); button.disabled = false; button.removeAttribute("aria-busy"); } }

async function updateRunMetadata(values) { await api(`/api/runs/${encodeURIComponent(state.selectedRun)}`, { method: "PATCH", body: JSON.stringify(values) }); await loadRuns(); await selectRun(state.selectedRun); }

function openAnnotation(annotationId = null) { state.editingAnnotationId = annotationId; const item = (state.currentRecord.annotations || []).find((annotation) => annotation.id === annotationId) || {}; $("annotation-status").value = item.status || ""; $("annotation-tags").value = (item.tags || []).join(", "); $("annotation-verdict").value = item.manual_verdict || ""; $("annotation-note").value = item.note || ""; $("annotation-dialog").showModal(); }
async function saveAnnotation() { const payload = { run_id: state.selectedRun, record_id: state.selectedRecord, group_id: state.selectedGroup || "", status: $("annotation-status").value, tags: $("annotation-tags").value.split(",").map((item) => item.trim()).filter(Boolean), manual_verdict: $("annotation-verdict").value, note: $("annotation-note").value }; if (state.editingAnnotationId) await api(`/api/annotations/${state.editingAnnotationId}`, { method: "PATCH", body: JSON.stringify(payload) }); else await api("/api/annotations", { method: "POST", body: JSON.stringify(payload) }); await selectRecord(state.selectedRecord); }

$("refresh-button").addEventListener("click", refreshRuns); $("search-input").addEventListener("input", () => { renderRuns(); renderRecords(); }); $("track-filter").addEventListener("change", renderRuns); $("status-filter").addEventListener("change", renderRuns); $("record-sort").addEventListener("change", renderRecords);
$("show-hidden-button").addEventListener("click", async () => { state.includeHidden = !state.includeHidden; $("show-hidden-button").classList.toggle("active", state.includeHidden); await loadRuns({ preserveSelection: false }); });
$("favorite-run").addEventListener("click", () => updateRunMetadata({ favorite: !state.currentRun.favorite })); $("hide-run").addEventListener("click", () => updateRunMetadata({ hidden: !state.currentRun.hidden }));
$("record-back-button").addEventListener("click", () => { state.selectedRecord = null; state.currentRecord = null; $("record-detail").hidden = true; $("run-overview").hidden = false; $("record-back-button").hidden = true; window.scrollTo(0, 0); });
$("detail-tabs").addEventListener("click", (event) => { const button = event.target.closest("button[data-tab]"); if (!button) return; state.detailTab = button.dataset.tab; renderRecordDetail(); });
$("note-button").addEventListener("click", () => openAnnotation()); $("annotation-form").addEventListener("submit", (event) => { if (event.submitter?.value === "cancel") return; event.preventDefault(); saveAnnotation().then(() => $("annotation-dialog").close()); });

loadRuns().catch((error) => { $("run-list").innerHTML = `<p class="error-copy">${escapeHtml(error.message)}</p>`; });
setInterval(refreshProgress, 5000);
