const titleByDataset = {
  programs: "Programs",
  courses: "Course catalog",
  prerequisites: "Course prerequisites",
  student_plans: "Student plans",
  registration_interests: "Registration interest",
  faculty: "Faculty directory",
  faculty_skills: "Faculty skills",
  availabilities: "Faculty availability",
  rooms: "Room inventory",
  forecast_runs: "Forecast runs",
  forecasts: "Course forecasts",
  sections: "Proposed sections",
  workload_assignments: "Faculty assignments",
  unmet_demand: "Unmet demand",
  state_events: "Decision audit trail",
  ground_truth_cases: "Ground-truth scenarios",
};

const dataset = decodeURIComponent(window.location.pathname.split("/").filter(Boolean).pop() || "");
const state = { offset: 0, limit: 25, search: "", sort: "", direction: "asc", total: 0, debounce: null };
const $ = (selector) => document.querySelector(selector);
const number = (value) => Number(value || 0).toLocaleString();

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

function formatColumn(column) {
  return column.replaceAll("_", " ");
}

async function loadTable() {
  const error = $("#errorBanner");
  error.hidden = true;
  $("#tableBody").innerHTML = '<tr><td class="loading-cell">Loading records…</td></tr>';
  const params = new URLSearchParams({
    offset: String(state.offset),
    limit: String(state.limit),
    search: state.search,
    sort: state.sort,
    direction: state.direction,
  });
  try {
    const response = await fetch(`/api/tables/${encodeURIComponent(dataset)}?${params}`);
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || `Could not load table (${response.status}).`);
    state.total = result.total;
    renderTable(result);
  } catch (reason) {
    $("#tableHead").innerHTML = "";
    $("#tableBody").innerHTML = '<tr><td class="empty-cell">The table could not be loaded.</td></tr>';
    $("#rangeLabel").textContent = "No data loaded";
    $("#pageLabel").textContent = "Page —";
    error.textContent = reason.message;
    error.hidden = false;
  }
}

function renderTable(result) {
  const title = titleByDataset[result.dataset] || formatColumn(result.dataset);
  document.title = `${title} | Yotta Flex`;
  $("#pageTitle").textContent = title;
  $("#pageDescription").textContent = `${number(result.total)} records · ${result.columns.length} fields · Search, sort, and browse the dataset.`;
  $("#totalRecords").textContent = number(result.total);
  $("#totalColumns").textContent = number(result.columns.length);
  $("#downloadCsv").href = result.csv_url;
  $("#downloadCsv").download = `${result.dataset}.csv`;

  $("#tableHead").innerHTML = `<tr>${result.columns.map((column) => {
    const active = result.sort === column;
    const arrow = active ? (result.direction === "asc" ? "↑" : "↓") : "↕";
    const ariaSort = active ? (result.direction === "asc" ? "ascending" : "descending") : "none";
    return `<th scope="col"><button type="button" data-sort="${escapeHtml(column)}" aria-sort="${ariaSort}" title="Sort by ${escapeHtml(formatColumn(column))}">${escapeHtml(formatColumn(column))}<span aria-hidden="true">${arrow}</span></button></th>`;
  }).join("")}</tr>`;

  if (!result.rows.length) {
    const message = result.search ? "No records match your search." : "This table has no records yet.";
    $("#tableBody").innerHTML = `<tr><td class="empty-cell" colspan="${result.columns.length}">${message}</td></tr>`;
  } else {
    $("#tableBody").innerHTML = result.rows.map((row) => `<tr>${result.columns.map((column) => {
      const value = row[column];
      if (value === null || value === undefined || value === "") {
        return '<td><span class="null-value">—</span></td>';
      }
      return `<td title="${escapeHtml(value)}">${escapeHtml(value)}</td>`;
    }).join("")}</tr>`).join("");
  }

  const first = result.total ? result.offset + 1 : 0;
  const last = Math.min(result.offset + result.limit, result.total);
  const pages = Math.max(1, Math.ceil(result.total / result.limit));
  const page = Math.floor(result.offset / result.limit) + 1;
  $("#rangeLabel").textContent = result.search
    ? `Showing ${number(first)}–${number(last)} of ${number(result.total)} matches`
    : `Showing ${number(first)}–${number(last)} of ${number(result.total)} records`;
  $("#currentPage").textContent = `${page} / ${pages}`;
  $("#pageLabel").textContent = `Page ${number(page)} of ${number(pages)}`;
  $("#previousPage").disabled = result.offset === 0;
  $("#nextPage").disabled = result.offset + result.limit >= result.total;
  $("#sortHint").textContent = result.sort
    ? `Sorted by ${formatColumn(result.sort)} (${result.direction})`
    : "Click a column heading to sort";
  $("#tableHead").querySelectorAll("[data-sort]").forEach((button) => {
    button.addEventListener("click", () => {
      const next = button.dataset.sort;
      state.direction = state.sort === next && state.direction === "asc" ? "desc" : "asc";
      state.sort = next;
      state.offset = 0;
      loadTable();
    });
  });
}

$("#searchInput").addEventListener("input", (event) => {
  window.clearTimeout(state.debounce);
  state.debounce = window.setTimeout(() => {
    state.search = event.target.value.trim();
    state.offset = 0;
    loadTable();
  }, 250);
});

$("#searchInput").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    window.clearTimeout(state.debounce);
    state.search = event.target.value.trim();
    state.offset = 0;
    loadTable();
  }
});

$("#pageSize").addEventListener("change", (event) => {
  state.limit = Number(event.target.value);
  state.offset = 0;
  loadTable();
});

$("#previousPage").addEventListener("click", () => {
  state.offset = Math.max(0, state.offset - state.limit);
  loadTable();
});

$("#nextPage").addEventListener("click", () => {
  if (state.offset + state.limit < state.total) {
    state.offset += state.limit;
    loadTable();
  }
});

if (titleByDataset[dataset]) {
  loadTable();
} else {
  $("#pageTitle").textContent = "Table not found";
  $("#pageDescription").textContent = "Return to the planning dashboard and choose an available dataset.";
  $("#errorBanner").textContent = "This dataset is not available.";
  $("#errorBanner").hidden = false;
}
