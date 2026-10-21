(() => {
  const config = window.GANESHA_DASHBOARD || {};

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

  if (config.page === "job_detail") {
    initJobDetail();
  } else {
    initJobsFeed();
  }

  function initJobsFeed() {
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
        const detailHref = `/jobs/${encodeURIComponent(job.id)}`;
        const titleHtml = `<a href="${escapeHtml(detailHref)}">${escapeHtml(job.title)}</a>`;
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
  }

  function initJobDetail() {
    const jobId = config.jobId;
    const statusMessage = document.getElementById("status-message");
    const actionsToggle = document.getElementById("actions-toggle");
    const actionsPanel = document.getElementById("actions-panel");
    const generateBtn = document.getElementById("action-generate-resume");
    const confirmPanel = document.getElementById("confirm-panel");
    const confirmList = document.getElementById("confirm-list");
    const confirmGenerate = document.getElementById("confirm-generate");
    const cancelGenerate = document.getElementById("cancel-generate");
    const resultPanel = document.getElementById("result-panel");
    const resultBody = document.getElementById("result-body");
    let previewData = null;

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

    function closeActions() {
      actionsPanel.hidden = true;
      actionsToggle.setAttribute("aria-expanded", "false");
    }

    actionsToggle.addEventListener("click", () => {
      const open = actionsPanel.hidden;
      actionsPanel.hidden = !open;
      actionsToggle.setAttribute("aria-expanded", open ? "true" : "false");
    });

    document.addEventListener("click", (event) => {
      const menu = document.getElementById("actions-menu");
      if (menu && !menu.contains(event.target)) closeActions();
    });

    async function loadDetail() {
      try {
        const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
        if (response.status === 503) {
          setStatus(
            "error",
            escapeHtml(config.dbUnavailableMessage || "Database unavailable.")
          );
          return;
        }
        if (response.status === 404) {
          setStatus("error", "Job not found.");
          return;
        }
        if (!response.ok) throw new Error("load failed");
        const data = await response.json();
        const job = data.job || {};
        document.getElementById("job-title").textContent = job.title || "Untitled";
        document.getElementById("job-company").textContent = job.company || "";
        document.getElementById("job-location").textContent = job.location || "—";
        document.getElementById("job-source").textContent =
          job.source_label || job.source || "—";
        document.getElementById("job-track").textContent =
          data.recommended_track || "general";
        const posting = document.getElementById("job-posting");
        if (job.job_url) {
          posting.innerHTML = `<a class="posting-link" href="${escapeHtml(job.job_url)}" target="_blank" rel="noopener noreferrer">Open original posting ↗</a>`;
        } else {
          posting.innerHTML = '<span class="muted">Unavailable</span>';
        }
        document.getElementById("detail-meta").hidden = false;
        document.getElementById("job-description").textContent =
          data.description_text || "(No description stored)";
        const warn = document.getElementById("desc-warning");
        if (data.description_incomplete) {
          warn.hidden = false;
          warn.textContent =
            "Job description looks incomplete. Tailoring quality may be limited.";
        }
      } catch (_err) {
        setStatus(
          "error",
          escapeHtml(config.dbUnavailableMessage || "Database unavailable.")
        );
      }
    }

    generateBtn.addEventListener("click", async () => {
      closeActions();
      resultPanel.hidden = true;
      setStatus("", "");
      confirmGenerate.disabled = true;
      confirmList.innerHTML = "<li>Loading preview…</li>";
      confirmPanel.hidden = false;
      try {
        const response = await fetch(
          `/api/jobs/${encodeURIComponent(jobId)}/resume/preview`
        );
        previewData = await response.json();
        if (!response.ok) {
          confirmList.innerHTML = `<li>${escapeHtml(previewData.error || "Preview failed")}</li>`;
          return;
        }
        const providerLabel =
          previewData.provider === "deterministic"
            ? "Deterministic only"
            : `Semantic provider (${escapeHtml(previewData.provider)})`;
        const items = [
          `<strong>${escapeHtml(previewData.title || "")}</strong> at ${escapeHtml(previewData.company || "Unknown")}`,
          `Recommended track: <strong>${escapeHtml(previewData.track || "")}</strong>`,
          `Verified source evidence: ${escapeHtml(String(previewData.evidence_count || 0))} facts`,
          `Strategy mode: ${providerLabel}`,
        ];
        if (previewData.insufficient_description) {
          items.push(
            '<span class="warn-text">Warning: job description is incomplete.</span>'
          );
        }
        if (previewData.warnings && previewData.warnings.length) {
          for (const w of previewData.warnings.slice(0, 3)) {
            items.push(`<span class="warn-text">${escapeHtml(w)}</span>`);
          }
        }
        if (previewData.block_reason) {
          items.push(`<span class="warn-text">${escapeHtml(previewData.block_reason)}</span>`);
        }
        confirmList.innerHTML = items.map((i) => `<li>${i}</li>`).join("");
        confirmGenerate.disabled = !previewData.can_generate;
      } catch (_err) {
        confirmList.innerHTML = "<li>Could not load generation preview.</li>";
      }
    });

    cancelGenerate.addEventListener("click", () => {
      confirmPanel.hidden = true;
      previewData = null;
    });

    confirmGenerate.addEventListener("click", async () => {
      if (!previewData || !previewData.can_generate) return;
      confirmGenerate.disabled = true;
      setStatus("empty", "Generating tailored resume…");
      try {
        const response = await fetch(
          `/api/jobs/${encodeURIComponent(jobId)}/resume/generate`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              confirm: true,
              track: previewData.track,
              deterministic_only: previewData.provider === "deterministic",
            }),
          }
        );
        const data = await response.json();
        confirmPanel.hidden = true;
        resultPanel.hidden = false;
        if (data.status === "succeeded") {
          setStatus("empty", "Resume generated successfully.");
          const gapsHtml =
            data.gaps && data.gaps.length
              ? `<details class="gaps-details"><summary>Warnings / gaps (${data.gaps.length})</summary><ul>${data.gaps
                  .map((g) => `<li>${escapeHtml(g)}</li>`)
                  .join("")}</ul></details>`
              : "";
          resultBody.innerHTML = `
            <ul class="result-list">
              <li>DOCX generated</li>
              <li>One-page PDF generated</li>
              <li>Internal DOCX: <code>${escapeHtml(data.docx_path || "")}</code></li>
              <li>Internal PDF: <code>${escapeHtml(data.pdf_path || "")}</code></li>
              <li>Desktop PDF: <code>${escapeHtml(data.desktop_pdf_path || "")}</code></li>
              <li>Track: ${escapeHtml(data.track || "")}</li>
              <li>Sources: ${escapeHtml((data.source_files || []).join(", "))}</li>
              <li>Provider: ${escapeHtml(data.provider || "")}</li>
            </ul>
            ${gapsHtml}
          `;
        } else {
          setStatus("error", escapeHtml(data.error || "Generation failed."));
          const msgs = (data.validation && data.validation.messages) || [];
          resultBody.innerHTML = `
            <p>Generation did not succeed. Invalid PDFs are not copied to Desktop.</p>
            <ul>${msgs.map((m) => `<li>${escapeHtml(m)}</li>`).join("")}</ul>
          `;
        }
      } catch (_err) {
        setStatus("error", "Generation request failed.");
      } finally {
        confirmGenerate.disabled = false;
      }
    });

    loadDetail();
  }
})();
