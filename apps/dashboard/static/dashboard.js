(() => {
  const config = window.GANESHA_DASHBOARD || {};
  const form = document.getElementById("filters");
  const jobsBody = document.getElementById("jobs-body");
  const resultCount = document.getElementById("result-count");
  const statusMessage = document.getElementById("status-message");
  const pagination = document.getElementById("pagination");
  const pageMeta = document.getElementById("page-meta");
  const prevBtn = document.getElementById("prev-page");
  const nextBtn = document.getElementById("next-page");
  const clearBtn = document.getElementById("clear-filters");
  const sourceSelect = document.getElementById("source");

  const fields = ["q", "source", "location", "date_from", "date_to", "sort"];
  let debounceTimer = null;
  let currentPage = 1;
  const pageSize = 50;

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function formatDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  }

  function readParamsFromForm() {
    const params = new URLSearchParams();
    for (const name of fields) {
      const el = form.elements.namedItem(name);
      if (!el) continue;
      const value = String(el.value || "").trim();
      if (value) params.set(name, value);
    }
    params.set("page", String(currentPage));
    params.set("page_size", String(pageSize));
    return params;
  }

  function syncUrl(params) {
    const next = new URL(window.location.href);
    next.search = params.toString();
    window.history.replaceState({}, "", next);
  }

  function applyParamsToForm(params) {
    for (const name of fields) {
      const el = form.elements.namedItem(name);
      if (!el) continue;
      if (name === "sort") {
        el.value = params.get(name) || "date_desc";
      } else {
        el.value = params.get(name) || "";
      }
    }
    currentPage = Math.max(1, parseInt(params.get("page") || "1", 10) || 1);
  }

  function setStatus(kind, html) {
    if (!html) {
      statusMessage.hidden = true;
      statusMessage.textContent = "";
      statusMessage.className = "status-message";
      return;
    }
    statusMessage.hidden = false;
    statusMessage.className = `status-message ${kind || ""}`.trim();
    statusMessage.innerHTML = html;
  }

  function populateSources(sources, selected) {
    const current = selected || sourceSelect.value || "";
    sourceSelect.innerHTML = '<option value="">All sources</option>';
    for (const src of sources || []) {
      const opt = document.createElement("option");
      opt.value = src.value;
      opt.textContent = `${src.label} (${src.count})`;
      sourceSelect.appendChild(opt);
    }
    sourceSelect.value = current;
  }

  function renderRows(items) {
    jobsBody.innerHTML = "";
    for (const job of items) {
      const tr = document.createElement("tr");
      const titleHtml = job.job_url
        ? `<a href="${escapeHtml(job.job_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(job.title)}</a>`
        : escapeHtml(job.title);
      const postingHtml = job.job_url
        ? `<a class="posting-link" href="${escapeHtml(job.job_url)}" target="_blank" rel="noopener noreferrer">Open posting ↗</a>`
        : `<span class="muted">Unavailable</span>`;
      tr.innerHTML = `
        <td class="title-cell" data-label="Title">${titleHtml}</td>
        <td data-label="Company">${escapeHtml(job.company)}</td>
        <td data-label="Location"><span class="location-label">${escapeHtml(job.location)}</span></td>
        <td data-label="Source"><span class="source-badge">${escapeHtml(job.source_label || job.source)}</span></td>
        <td data-label="Date found">${escapeHtml(formatDate(job.date_found))}</td>
        <td data-label="Original posting">${postingHtml}</td>
      `;
      jobsBody.appendChild(tr);
    }
  }

  function updatePagination(data) {
    if (!data.total) {
      pagination.hidden = true;
      return;
    }
    pagination.hidden = false;
    pageMeta.textContent = `Page ${data.page} of ${data.total_pages} · ${data.total} total`;
    prevBtn.disabled = data.page <= 1;
    nextBtn.disabled = data.page >= data.total_pages;
  }

  async function loadJobs() {
    const params = readParamsFromForm();
    syncUrl(params);
    resultCount.textContent = "Loading…";
    setStatus("", "");

    try {
      const response = await fetch(`/api/jobs?${params.toString()}`);
      if (response.status === 503) {
        resultCount.textContent = "0 jobs";
        jobsBody.innerHTML = "";
        pagination.hidden = true;
        setStatus(
          "error",
          escapeHtml(config.dbUnavailableMessage || "Database unavailable.")
        );
        return;
      }
      if (!response.ok) {
        throw new Error(`Request failed (${response.status})`);
      }
      const data = await response.json();
      populateSources(data.available_sources, params.get("source") || "");
      resultCount.textContent = `${data.total} job${data.total === 1 ? "" : "s"}`;
      renderRows(data.items || []);
      updatePagination(data);

      if (data.total === 0) {
        const hasFilters = fields.some((name) => {
          if (name === "sort") return false;
          const el = form.elements.namedItem(name);
          return el && String(el.value || "").trim();
        });
        if (hasFilters) {
          setStatus(
            "empty",
            'No jobs match these filters. <button type="button" class="btn linkish" id="empty-clear">Clear filters</button>'
          );
          const emptyClear = document.getElementById("empty-clear");
          if (emptyClear) emptyClear.addEventListener("click", clearFilters);
        } else {
          setStatus(
            "empty",
            "No jobs have been imported yet. Import or sync a source, then refresh this dashboard."
          );
        }
      }
    } catch (_err) {
      resultCount.textContent = "0 jobs";
      jobsBody.innerHTML = "";
      pagination.hidden = true;
      setStatus(
        "error",
        escapeHtml(config.dbUnavailableMessage || "Database unavailable.")
      );
    }
  }

  function clearFilters() {
    for (const name of fields) {
      const el = form.elements.namedItem(name);
      if (!el) continue;
      el.value = name === "sort" ? "date_desc" : "";
    }
    currentPage = 1;
    loadJobs();
  }

  function scheduleLoad() {
    window.clearTimeout(debounceTimer);
    debounceTimer = window.setTimeout(() => {
      currentPage = 1;
      loadJobs();
    }, 300);
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    currentPage = 1;
    loadJobs();
  });

  for (const name of ["q", "location"]) {
    const el = form.elements.namedItem(name);
    if (el) el.addEventListener("input", scheduleLoad);
  }
  for (const name of ["source", "date_from", "date_to", "sort"]) {
    const el = form.elements.namedItem(name);
    if (el) {
      el.addEventListener("change", () => {
        currentPage = 1;
        loadJobs();
      });
    }
  }

  clearBtn.addEventListener("click", clearFilters);
  prevBtn.addEventListener("click", () => {
    if (currentPage > 1) {
      currentPage -= 1;
      loadJobs();
    }
  });
  nextBtn.addEventListener("click", () => {
    currentPage += 1;
    loadJobs();
  });

  applyParamsToForm(new URLSearchParams(window.location.search));
  loadJobs();
})();
