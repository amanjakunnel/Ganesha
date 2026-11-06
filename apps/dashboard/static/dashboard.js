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

  function fitBadge(band, score) {
    const label = band || "unscored";
    const scoreTxt = score == null ? "" : ` · fit ${score}`;
    return `<span class="fit-badge fit-${escapeHtml(label)}">${escapeHtml(label.split("_").join(" "))}${escapeHtml(scoreTxt)}</span>`;
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
    const fitCounts = document.getElementById("fit-counts");
    const pagination = document.getElementById("pagination");
    const pageMeta = document.getElementById("page-meta");
    const prevBtn = document.getElementById("prev-page");
    const nextBtn = document.getElementById("next-page");
    const clearBtn = document.getElementById("clear-filters");
    const sourceSelect = document.getElementById("source");

    const fields = ["fit", "q", "source", "location", "date_from", "date_to", "sort"];
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
      const next = `${window.location.pathname}?${params.toString()}`;
      window.history.replaceState({}, "", next);
    }

    function applyParamsToForm(params) {
      for (const name of fields) {
        const el = form.elements.namedItem(name);
        if (!el) continue;
        if (name === "fit") {
          el.value = params.get("fit") || "recommended";
          continue;
        }
        if (name === "sort") {
          el.value = params.get("sort") || "date_desc";
          continue;
        }
        el.value = params.get(name) || "";
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

    function renderFitCounts(counts) {
      if (!fitCounts || !counts) return;
      fitCounts.textContent =
        `Recommended ${counts.recommended || 0} · Stretch ${counts.stretch || 0} · ` +
        `Needs review ${counts.needs_review || 0} · Low fit ${counts.low_fit || 0} · ` +
        `Eligible ${counts.all_eligible || 0}`;
    }

    function renderRows(items) {
      jobsBody.innerHTML = "";
      for (const job of items) {
        const tr = document.createElement("tr");
        const detailHref = `/jobs/${encodeURIComponent(job.id)}`;
        const titleHtml = `<a href="${escapeHtml(detailHref)}">${escapeHtml(job.title)}</a>`;
        const reason = job.fit_reason
          ? `<div class="fit-reason muted">${escapeHtml(job.fit_reason)}</div>`
          : "";
        tr.innerHTML = `
          <td class="title-cell" data-label="Title">${titleHtml}${reason}</td>
          <td data-label="Company">${escapeHtml(job.company)}</td>
          <td data-label="Fit">${fitBadge(job.fit_band, job.fit_score)}</td>
          <td data-label="Location"><span class="location-label">${escapeHtml(job.location)}</span></td>
          <td data-label="Source"><span class="source-badge">${escapeHtml(job.source_label || job.source)}</span></td>
          <td data-label="Date found">${escapeHtml(formatDate(job.date_found))}</td>
          <td data-label="Details"><a class="btn" href="${escapeHtml(detailHref)}">Open details</a></td>
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
        if (!response.ok) throw new Error(`Request failed (${response.status})`);
        const data = await response.json();
        populateSources(data.available_sources, params.get("source") || "");
        renderFitCounts(data.fit_counts);
        resultCount.textContent = `${data.total} job${data.total === 1 ? "" : "s"}`;
        renderRows(data.items || []);
        updatePagination(data);

        if (data.total === 0) {
          const fit = params.get("fit") || "recommended";
          if (fit === "recommended") {
            setStatus(
              "empty",
              'No jobs currently meet the Recommended cutoff. <button type="button" class="btn linkish" id="include-stretch">Include Stretch jobs</button>'
            );
            const stretchBtn = document.getElementById("include-stretch");
            if (stretchBtn) {
              stretchBtn.addEventListener("click", () => {
                form.elements.namedItem("fit").value = "recommended_stretch";
                currentPage = 1;
                loadJobs();
              });
            }
          } else {
            setStatus("empty", "No jobs match these filters.");
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
        if (name === "fit") el.value = "recommended";
        else if (name === "sort") el.value = "date_desc";
        else el.value = "";
      }
      currentPage = 1;
      loadJobs();
    }

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      currentPage = 1;
      loadJobs();
    });
    clearBtn.addEventListener("click", clearFilters);
    for (const name of ["fit", "source", "sort"]) {
      const el = form.elements.namedItem(name);
      if (el) {
        el.addEventListener("change", () => {
          currentPage = 1;
          loadJobs();
        });
      }
    }
    for (const name of ["q", "location", "date_from", "date_to"]) {
      const el = form.elements.namedItem(name);
      if (!el) continue;
      el.addEventListener("input", () => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
          currentPage = 1;
          loadJobs();
        }, 250);
      });
    }
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
    const resumeBtn = document.getElementById("btn-generate-resume");
    const coverBtn = document.getElementById("btn-generate-cover");
    const coverPlanBtn = document.getElementById("btn-view-cover-plan");
    const coverVoice = document.getElementById("cover-voice");
    const confirmPanel = document.getElementById("confirm-panel");
    const confirmTitle = document.getElementById("confirm-title");
    const confirmList = document.getElementById("confirm-list");
    const confirmGenerate = document.getElementById("confirm-generate");
    const cancelGenerate = document.getElementById("cancel-generate");
    const resultPanel = document.getElementById("result-panel");
    const resultBody = document.getElementById("result-body");
    const coverPlanPanel = document.getElementById("cover-plan-panel");
    const coverPlanBody = document.getElementById("cover-plan-body");
    const generationControls = document.getElementById("generation-controls");
    const eligibilityNote = document.getElementById("generation-eligibility-note");
    const fitWarning = document.getElementById("fit-generation-warning");
    const fitBreakdownPanel = document.getElementById("fit-breakdown-panel");
    const fitBreakdownBody = document.getElementById("fit-breakdown-body");

    let pendingAction = null;
    let detailData = null;
    let inFlight = false;

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

    function selectedVoice() {
      return (coverVoice && coverVoice.value) || "technical_direct";
    }

    function setBusy(busy) {
      inFlight = busy;
      if (resumeBtn) resumeBtn.disabled = busy || !(detailData && detailData.resume_generation && detailData.resume_generation.available);
      if (coverBtn) coverBtn.disabled = busy || !(detailData && detailData.cover_letter_generation && detailData.cover_letter_generation.available);
      if (confirmGenerate) confirmGenerate.disabled = busy;
    }

    function renderFit(fit) {
      const el = document.getElementById("job-fit");
      if (!el) return;
      if (!fit || !fit.band) {
        el.textContent = "Not assessed yet. Run jobs reassess-fit.";
        return;
      }
      el.innerHTML = `${fitBadge(fit.band, fit.score)} <span class="muted">${escapeHtml(fit.short_reason || "")}</span>`;
      if (fit.requirements && fit.requirements.length) {
        fitBreakdownPanel.hidden = false;
        fitBreakdownBody.innerHTML = `<ul class="result-list">${fit.requirements
          .slice(0, 8)
          .map(
            (req) =>
              `<li><strong>${escapeHtml(req.evidence_status)}</strong> (${escapeHtml(req.classification)}): ${escapeHtml(req.requirement)} — ${escapeHtml(req.explanation || "")}</li>`
          )
          .join("")}</ul>`;
      }
      if (fit.band === "low_fit" || fit.band === "stretch") {
        fitWarning.hidden = false;
        fitWarning.textContent =
          fit.band === "low_fit"
            ? "This job is low fit. Generation is still allowed if eligibility passes, but review the gap first."
            : "This job is a stretch match. Generation is allowed; review the gap before using the documents.";
      }
    }

    function applyGenerationAvailability(data) {
      generationControls.hidden = false;
      const resume = data.resume_generation || {};
      const cover = data.cover_letter_generation || {};
      resumeBtn.disabled = !resume.available;
      coverBtn.disabled = !cover.available;
      const notes = [];
      if (!resume.available && resume.block_reason) {
        notes.push(`Resume: ${resume.block_reason}`);
      }
      if (!cover.available && cover.block_reason) {
        notes.push(`Cover letter: ${cover.block_reason}`);
      }
      eligibilityNote.textContent = notes.join(" ");
    }

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
        detailData = data;
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
        renderFit(data.fit || {});
        applyGenerationAvailability(data);
      } catch (_err) {
        setStatus(
          "error",
          escapeHtml(config.dbUnavailableMessage || "Database unavailable.")
        );
      }
    }

    async function openResumeConfirm() {
      pendingAction = "resume";
      confirmTitle.textContent = "Confirm resume generation";
      confirmGenerate.disabled = true;
      confirmList.innerHTML = "<li>Loading preview…</li>";
      confirmPanel.hidden = false;
      resultPanel.hidden = true;
      try {
        const response = await fetch(
          `/api/jobs/${encodeURIComponent(jobId)}/resume/preview`
        );
        const previewData = await response.json();
        if (!response.ok) {
          confirmList.innerHTML = `<li>${escapeHtml(previewData.error || "Preview failed")}</li>`;
          return;
        }
        if (previewData.status === "blocked" || previewData.block_reason) {
          confirmList.innerHTML = `<li><span class="warn-text">${escapeHtml(
            previewData.block_reason ||
              "Resume generation is unavailable because this job does not meet your configured eligibility policy."
          )}</span></li>`;
          confirmGenerate.disabled = true;
          return;
        }
        const items = [
          `<strong>${escapeHtml(previewData.title || "")}</strong> at ${escapeHtml(previewData.company || "Unknown")}`,
          `Recommended track: <strong>${escapeHtml(previewData.track || "")}</strong>`,
          `Verified source evidence: ${escapeHtml(String(previewData.evidence_count || 0))} facts`,
        ];
        if (detailData && detailData.fit && detailData.fit.band) {
          items.push(
            `Fit: ${escapeHtml(detailData.fit.band)} (${escapeHtml(String(detailData.fit.score ?? ""))}) — ${escapeHtml(detailData.fit.short_reason || "")}`
          );
        }
        confirmList.innerHTML = items.map((i) => `<li>${i}</li>`).join("");
        confirmGenerate.disabled = !previewData.can_generate;
      } catch (_err) {
        confirmList.innerHTML = "<li>Could not load generation preview.</li>";
      }
    }

    async function openCoverConfirm() {
      pendingAction = "cover";
      confirmTitle.textContent = "Confirm cover-letter generation";
      confirmList.innerHTML = `
        <li>Generate a cover letter for this job using verified evidence.</li>
        <li>Voice: <strong>${escapeHtml(selectedVoice())}</strong></li>
        <li>Missing company research will not block a job-description-based letter.</li>
      `;
      if (detailData && detailData.fit && (detailData.fit.band === "low_fit" || detailData.fit.band === "stretch")) {
        confirmList.innerHTML += `<li><span class="warn-text">${escapeHtml(detailData.fit.short_reason || "Fit warning")}</span></li>`;
      }
      confirmGenerate.disabled = false;
      confirmPanel.hidden = false;
      resultPanel.hidden = true;
    }

    resumeBtn.addEventListener("click", () => {
      if (inFlight || resumeBtn.disabled) return;
      openResumeConfirm();
    });
    coverBtn.addEventListener("click", () => {
      if (inFlight || coverBtn.disabled) return;
      openCoverConfirm();
    });
    cancelGenerate.addEventListener("click", () => {
      confirmPanel.hidden = true;
      pendingAction = null;
    });

    confirmGenerate.addEventListener("click", async () => {
      if (!pendingAction || inFlight) return;
      setBusy(true);
      setStatus("empty", pendingAction === "resume" ? "Generating tailored resume…" : "Generating tailored cover letter…");
      try {
        let response;
        if (pendingAction === "resume") {
          response = await fetch(
            `/api/jobs/${encodeURIComponent(jobId)}/resume/generate`,
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                confirm: true,
                deterministic_only: false,
              }),
            }
          );
        } else {
          response = await fetch(
            `/api/jobs/${encodeURIComponent(jobId)}/cover-letter/generate`,
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                confirm: true,
                voice: selectedVoice(),
                deterministic_only: false,
              }),
            }
          );
        }
        const data = await response.json();
        confirmPanel.hidden = true;
        resultPanel.hidden = false;
        if (data.status === "succeeded") {
          setStatus(
            "empty",
            pendingAction === "resume"
              ? "Resume generated successfully."
              : "Cover letter generated"
          );
          if (pendingAction === "resume") {
            resultBody.innerHTML = `
              <ul class="result-list">
                <li>DOCX generated</li>
                <li>One-page PDF generated</li>
                <li>Internal DOCX: <code>${escapeHtml(data.docx_path || "")}</code></li>
                <li>Internal PDF: <code>${escapeHtml(data.pdf_path || "")}</code></li>
                <li>Desktop PDF: <code>${escapeHtml(data.desktop_pdf_path || "")}</code></li>
                <li>Track: ${escapeHtml(data.track || "")}</li>
              </ul>
            `;
          } else {
            resultBody.innerHTML = `
              <ul class="result-list">
                <li>Cover letter generated</li>
                <li>Voice: ${escapeHtml(data.voice || "")}</li>
                <li>Selected evidence: ${escapeHtml(String(data.evidence_count || 0))}</li>
                <li>Company evidence used: ${data.company_evidence_used ? "yes" : "no"}</li>
                <li>Internal DOCX: <code>${escapeHtml(data.docx_path || "")}</code></li>
                <li>Internal PDF: <code>${escapeHtml(data.pdf_path || "")}</code></li>
                <li>Desktop PDF: <code>${escapeHtml(data.desktop_pdf_path || "")}</code></li>
              </ul>
            `;
          }
        } else {
          setStatus("error", escapeHtml(data.error || "Generation failed."));
          resultBody.innerHTML = `<p>${escapeHtml(data.error || "Generation failed.")}</p>`;
        }
      } catch (_err) {
        setStatus("error", "Generation request failed.");
      } finally {
        pendingAction = null;
        setBusy(false);
      }
    });

    if (coverPlanBtn) {
      coverPlanBtn.addEventListener("click", async () => {
        coverPlanPanel.hidden = false;
        coverPlanBody.innerHTML = "<p>Loading cover-letter plan…</p>";
        try {
          const response = await fetch(
            `/api/jobs/${encodeURIComponent(jobId)}/cover-letter/plan?voice=${encodeURIComponent(selectedVoice())}`
          );
          const data = await response.json();
          if (!response.ok) {
            coverPlanBody.innerHTML = `<p>${escapeHtml(data.error || "Plan unavailable.")}</p>`;
            return;
          }
          const primary = data.primary ? data.primary.label : "none";
          const secondary = data.secondary ? data.secondary.label : "none";
          const companyCount = (data.company_evidence || []).length;
          const warnings = (data.warnings || [])
            .map((warning) => `<li>${escapeHtml(warning)}</li>`)
            .join("");
          coverPlanBody.innerHTML = `
            <ul class="result-list">
              <li>Role: ${escapeHtml(data.role || "")}</li>
              <li>Company: ${escapeHtml(data.company || "")}</li>
              <li>Voice: ${escapeHtml(data.voice || "")}</li>
              <li>Primary evidence: ${escapeHtml(primary)}</li>
              <li>Secondary evidence: ${escapeHtml(secondary)}</li>
              <li>Official company evidence: ${companyCount}</li>
            </ul>
            ${warnings ? `<ul>${warnings}</ul>` : ""}
          `;
        } catch (_err) {
          coverPlanBody.innerHTML = "<p>Could not load the cover-letter plan.</p>";
        }
      });
    }

    loadDetail();
  }
})();
