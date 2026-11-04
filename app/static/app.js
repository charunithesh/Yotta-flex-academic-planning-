const $ = (selector) => document.querySelector(selector);
const number = (value) => Number(value || 0).toLocaleString();
let activeRunId = null;

function showNotice(message, type = "success") {
  const notice = $("#notice");
  notice.textContent = message;
  notice.className = `notice ${type}`;
  notice.hidden = false;
  window.setTimeout(() => { notice.hidden = true; }, 6500);
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  let body;
  try {
    body = await response.json();
  } catch {
    throw new Error(`Request failed (${response.status}); the server returned an invalid response.`);
  }
  if (!response.ok) throw new Error(body.detail || `Request failed (${response.status})`);
  return body;
}

function getPlanningInput() {
  const targetTerm = $("#termInput").value.trim();
  if (!/^\d{4}-(Spring|Summer|Fall)$/.test(targetTerm)) {
    throw new Error("Enter a target term in YYYY-Spring, YYYY-Summer, or YYYY-Fall format.");
  }

  const seed = Number($("#seedInput").value);
  if (!Number.isInteger(seed) || seed < 0 || seed > 2147483647) {
    throw new Error("Seed must be a whole number between 0 and 2147483647.");
  }
  return { target_term: targetTerm, seed };
}

function setBusy(button, busy, text) {
  button.disabled = busy;
  button.dataset.originalText ||= button.textContent;
  button.textContent = busy ? text : button.dataset.originalText;
}

function renderMetrics(counts = {}) {
  $("#programCount").textContent = number(counts.programs);
  $("#courseCount").textContent = number(counts.courses);
  $("#facultyCount").textContent = number(counts.faculty);
  $("#planCount").textContent = number(counts.student_plans);
}

function renderLatest(run) {
  if (!run) {
    $("#latestTerm").textContent = "No run yet";
    $("#latestStatus").textContent = "AWAITING DATA";
    $("#latestStatus").className = "status-pill status-idle";
    $("#forecastSeats").textContent = "—";
    $("#allocatedSeats").textContent = "—";
    $("#unmetSeats").textContent = "—";
    $("#reconcileLabel").textContent = "Waiting for a run";
    $("#utilizationLabel").textContent = "—";
    $("#utilizationBar").style.width = "0";
    $("#latestRunLink").textContent = "";
    return;
  }
  $("#latestTerm").textContent = `${run.target_term} allocation`;
  $("#latestStatus").textContent = run.unmet_seats ? "REVIEW EXCEPTIONS" : "READY FOR REVIEW";
  $("#latestStatus").className = `status-pill ${run.unmet_seats ? "status-warning" : "status-success"}`;
  $("#forecastSeats").textContent = number(run.forecasted_enrollment);
  $("#allocatedSeats").textContent = number(run.allocated_seats);
  $("#unmetSeats").textContent = number(run.unmet_seats);
  $("#reconcileLabel").textContent = run.reconciles ? "Balanced" : "Needs review";
  const coverage = run.forecasted_enrollment
    ? Math.round((run.allocated_seats / run.forecasted_enrollment) * 100)
    : 0;
  const utilization = Math.round((run.seat_utilization || 0) * 1000) / 10;
  $("#utilizationLabel").textContent = `${utilization}% · ${coverage}%`;
  $("#utilizationBar").style.width = `${Math.min(coverage, 100)}%`;
  $("#latestRunLink").innerHTML = `<span>Run ${escapeHtml(run.run_id)}</span>`;
}

function renderRuns(runs) {
  $("#runCountLabel").textContent = `${runs.length} run${runs.length === 1 ? "" : "s"}`;
  if (!runs.length) {
    $("#runsTable").innerHTML = '<tr><td class="empty-cell" colspan="6">Generate source data, then create a forecast run.</td></tr>';
    return;
  }
  $("#runsTable").innerHTML = runs.map((run) => `
    <tr>
      <td><button class="run-link" data-run="${escapeHtml(run.run_id)}">${escapeHtml(run.target_term)} allocation</button><span class="run-id">${escapeHtml(run.run_id)}</span></td>
      <td>${run.window_terms.length ? run.window_terms.map(escapeHtml).join(", ") : "Current interest only"}</td>
      <td>${number(run.forecasted_enrollment)}</td>
      <td>${number(run.section_count)}</td>
      <td>${number(run.unmet_seats)}</td>
      <td><span class="table-status ${run.reconciles ? "" : "invalid"}"><span>${run.reconciles ? "●" : "!"}</span>${run.reconciles ? "Balanced" : "Review"}</span></td>
    </tr>`).join("");
  document.querySelectorAll(".run-link").forEach((button) => {
    button.addEventListener("click", () => loadRun(button.dataset.run));
  });
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

async function loadRun(runId) {
  const [detail, validation] = await Promise.all([
    request(`/api/runs/${encodeURIComponent(runId)}`),
    request(`/api/runs/${encodeURIComponent(runId)}/validation`),
  ]);
  activeRunId = runId;
  renderLatest(detail);
  $("#latestStatus").textContent = validation.valid
    ? (detail.unmet_seats ? "REVIEW EXCEPTIONS" : "READY FOR REVIEW")
    : "VALIDATION FAILED";
  $("#latestStatus").className = `status-pill ${validation.valid ? (detail.unmet_seats ? "status-warning" : "status-success") : "status-warning"}`;
  $("#latestRunLink").textContent = `${detail.run_id} · ${detail.assignment_count} assignments · ${validation.issues.length} constraint violations`;
  $("#forecastMethodNote").textContent = `${detail.forecasting_method} · ${detail.forecast_timestamp}`;
  $("#forecastTable").innerHTML = detail.forecasts.slice(0, 18).map((row) => `
    <tr><td>${escapeHtml(row.course_id)} · ${escapeHtml(row.course)}</td><td>${number(Math.round(row.historical_demand))}</td><td>${number(row.current_interest)}</td><td><strong>${number(row.forecast_enrollment)}</strong></td><td>${number(row.recommended_section_count)}</td></tr>
  `).join("") || '<tr><td class="empty-cell" colspan="5">No courses in this forecast.</td></tr>';
  $("#unmetTable").innerHTML = detail.unmet_demand.map((row) => `
    <tr><td>${escapeHtml(row.course_id)} · ${escapeHtml(row.course)}</td><td>${number(row.unmet_seats)}</td><td title="${escapeHtml(row.details)}">${escapeHtml(row.reason.replaceAll("_", " "))}</td></tr>
  `).join("") || '<tr><td class="empty-cell" colspan="3">All forecast demand was assigned.</td></tr>';
  $("#unmetBadge").textContent = `${detail.unmet_course_count} course${detail.unmet_course_count === 1 ? "" : "s"}`;
}

function renderForecastEvaluation(result) {
  const summary = result.overall;
  const formatMetric = (value) => value === null ? "N/A" : number(value);
  $("#evaluationObservationCount").textContent = `${number(summary.observations)} course-term observations`;
  $("#evaluationMae").textContent = formatMetric(summary.mae);
  $("#evaluationRmse").textContent = formatMetric(summary.rmse);
  $("#evaluationWape").textContent = summary.wape === null
    ? "N/A"
    : `${(summary.wape * 100).toFixed(1)}%`;
  $("#evaluationMethodNote").textContent = `${result.method}: ${result.leakage_prevention}`;
  $("#evaluationTable").innerHTML = result.evaluations.map((item) => `
    <tr><td>${escapeHtml(item.term)}</td><td>${escapeHtml(item.course_id)} · ${escapeHtml(item.course)}</td><td>${item.training_terms.map(escapeHtml).join(", ")}</td><td>${number(item.forecast_enrollment)}</td><td>${number(item.actual_enrollment)}</td></tr>
  `).join("") || '<tr><td class="empty-cell" colspan="5">At least two academic terms are required for a walk-forward evaluation.</td></tr>';
}

async function loadGroundTruth() {
  const result = await request("/api/scenarios/ground-truth");
  $("#scenarioList").innerHTML = result.cases.map((item) => `
    <div class="scenario-item" title="${escapeHtml(item.details)}">
      <strong>${escapeHtml(item.issue_type.replaceAll("_", " "))}</strong>
      <span>${escapeHtml(item.expected_decision.replaceAll("_", " "))}</span>
    </div>`).join("") || '<p class="muted-label">No labeled scenarios available.</p>';
}

async function loadDashboard() {
  const dashboard = await request("/api/dashboard");
  renderMetrics(dashboard.counts);
  renderRuns(dashboard.runs);
  renderLatest(dashboard.runs[0]);
  if (dashboard.runs.length) await loadRun(dashboard.runs[0].run_id);
  const evaluation = await request("/api/forecast/evaluation");
  renderForecastEvaluation(evaluation);
  await loadGroundTruth();
}

$("#refreshButton").addEventListener("click", () => loadDashboard().catch((error) => showNotice(error.message, "error")));

$("#generateButton").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  setBusy(button, true, "Generating data…");
  try {
    const result = await request("/api/data/generate", {
      method: "POST",
      body: JSON.stringify({
        scale: $("#scaleSelect").value,
        seed: Number($("#seedInput").value),
      }),
    });
    await loadDashboard();
    showNotice(`Generated ${number(result.counts.courses)} courses and ${number(result.counts.student_plans)} student plans with seed ${result.counts.seed}.`);
  } catch (error) {
    showNotice(error.message, "error");
  } finally {
    setBusy(button, false);
  }
});

$("#forecastButton").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  let planningInput;
  try {
    planningInput = getPlanningInput();
  } catch (error) {
    showNotice(error.message, "error");
    return;
  }
  setBusy(button, true, "Forecasting & allocating…");
  try {
    const run = await request("/api/forecast", {
      method: "POST",
      body: JSON.stringify(planningInput),
    });
    await loadDashboard();
    await loadRun(run.run_id);
    const allocationMessage = run.forecasted_enrollment > 0 && run.allocated_seats === 0
      ? " No seats could be allocated; review unmet-demand reasons and resource availability."
      : "";
    showNotice(`Run ${run.run_id} completed. ${number(run.allocated_seats)} seats allocated; ${number(run.unmet_seats)} need follow-up.${allocationMessage}`);
  } catch (error) {
    showNotice(error.message, "error");
  } finally {
    setBusy(button, false);
  }
});

loadDashboard().catch((error) => showNotice(`Could not load planning workspace: ${error.message}`, "error"));
