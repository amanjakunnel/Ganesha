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

  /** Shared loading status: label + animated ellipsis (respects reduced motion). */
  const GaneshaLoading = {
    _reducedMotion() {
      try {
        return (
          window.matchMedia &&
          window.matchMedia("(prefers-reduced-motion: reduce)").matches
        );
      } catch (_err) {
        return false;
      }
    },
    _setControl(control, disabled) {
      if (!control) return;
      if (Array.isArray(control)) {
        control.forEach((el) => {
          if (el) el.disabled = disabled;
        });
        return;
      }
      control.disabled = disabled;
    },
    start(el, label, opts) {
      if (!el) return;
      const options = opts || {};
      this._setControl(options.control, true);
      el.hidden = false;
      el.classList.add("loading-status", "is-running");
      el.classList.remove("is-success", "is-error");
      if (this._reducedMotion()) el.classList.add("reduced-motion");
      else el.classList.remove("reduced-motion");
      el.setAttribute("aria-live", "polite");
      el.setAttribute("aria-busy", "true");
      const safe = escapeHtml(label || "Working");
      el.innerHTML = `<span class="loading-label">${safe}</span><span class="loading-ellipsis" aria-hidden="true"></span>`;
    },
    update(el, label) {
      if (!el) return;
      const labelEl = el.querySelector(".loading-label");
      if (labelEl) labelEl.textContent = label || "";
      else this.start(el, label);
    },
    succeed(el, message, opts) {
      if (!el) return;
      const options = opts || {};
      this._setControl(options.control, false);
      el.classList.remove("is-running", "is-error");
      el.classList.add("is-success");
      el.removeAttribute("aria-busy");
      el.textContent = message || "Done.";
    },
    fail(el, message, opts) {
      if (!el) return;
      const options = opts || {};
      this._setControl(options.control, false);
      el.classList.remove("is-running", "is-success");
      el.classList.add("is-error");
      el.removeAttribute("aria-busy");
      el.textContent = message || "Something went wrong.";
    },
    clear(el, opts) {
      if (!el) return;
      const options = opts || {};
      this._setControl(options.control, false);
      el.classList.remove("is-running", "is-success", "is-error", "reduced-motion");
      el.removeAttribute("aria-busy");
      el.textContent = "";
      if (options.hide) el.hidden = true;
    },
  };
  window.GaneshaLoading = GaneshaLoading;

  if (config.page === "job_detail") {
    initJobDetail();
  } else if (config.page === "jobs" || document.getElementById("filters")) {
    initJobsFeed();
  }

  function initJobsFeed() {
    const form = document.getElementById("filters");
    const jobsBody = document.getElementById("jobs-body");
    const resultCount = document.getElementById("result-count");
    const statusMessage = document.getElementById("status-message");
    const fitCounts = document.getElementById("fit-counts");
    const recencyCounts = document.getElementById("recency-counts");
    const pagination = document.getElementById("pagination");
    const pageMeta = document.getElementById("page-meta");
    const prevBtn = document.getElementById("prev-page");
    const nextBtn = document.getElementById("next-page");
    const clearBtn = document.getElementById("clear-filters");
    const sourceSelect = document.getElementById("source");
    const refreshBtn = document.getElementById("refresh-jobs");
    const refreshMeta = document.getElementById("refresh-meta");
    const staleNotice = document.getElementById("stale-notice");
    const refreshProgress = document.getElementById("refresh-progress");
    const PREF_KEY = "ganesha.dashboard.filters";

    const fields = ["fit", "recency", "q", "source", "location", "sort"];
    const RECENCY_PRESETS = new Set(["24h", "48h", "7d", "30d", "all"]);
    let debounceTimer = null;
    let refreshPollTimer = null;
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

    function persistPrefs(params) {
      try {
        const payload = {};
        for (const name of fields) {
          const value = params.get(name);
          if (value) payload[name] = value;
        }
        localStorage.setItem(PREF_KEY, JSON.stringify(payload));
      } catch (_err) {
        /* ignore quota / private mode */
      }
    }

    function loadPrefs() {
      try {
        const raw = localStorage.getItem(PREF_KEY);
        if (!raw) return null;
        return JSON.parse(raw);
      } catch (_err) {
        return null;
      }
    }

    function syncUrl(params) {
      const next = `${window.location.pathname}?${params.toString()}`;
      window.history.replaceState({}, "", next);
      persistPrefs(params);
    }

    function applyParamsToForm(params) {
      for (const name of fields) {
        const el = form.elements.namedItem(name);
        if (!el) continue;
        if (name === "fit") {
          el.value = params.get("fit") || "recommended";
          continue;
        }
        if (name === "recency") {
          const raw = params.get("recency") || "all";
          el.value = RECENCY_PRESETS.has(raw) ? raw : "all";
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

    function formatDateTime(iso) {
      if (!iso) return "never";
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return "never";
      return d.toLocaleString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      });
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
        `Other roles ${counts.other_roles || 0} · Eligible ${counts.all_eligible || 0}`;
    }

    function renderRecencyCounts(counts) {
      if (!recencyCounts || !counts) return;
      recencyCounts.textContent =
        `Recency · 24h ${counts.hours_24 || 0} · 48h ${counts.hours_48 || 0} · ` +
        `7d ${counts.days_7 || 0} · 30d ${counts.days_30 || 0} · ` +
        `All ${counts.all || 0} · ` +
        `Unknown/approx date excluded from time presets ${counts.unknown_or_approx || 0}`;
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
        const roleNote =
          job.role_family_status === "role_family_mismatch"
            ? `<div class="fit-reason muted">Other role${
                job.role_matched_family
                  ? " · matched " + escapeHtml(job.role_matched_family)
                  : " · unmatched"
              }</div>`
            : job.role_matched_family
              ? `<div class="fit-reason muted">Family: ${escapeHtml(
                  job.role_matched_family
                )}</div>`
              : "";
        const newBadge = job.is_new_since_refresh
          ? `<span class="new-badge">New</span>`
          : "";
        const verifyBadge = job.description_badge
          ? `<span class="verify-badge">${escapeHtml(job.description_badge)}</span>`
          : "";
        const recencyLabel = job.recency_label || "First seen";
        const recencyDate = formatDate(job.recency_at || job.date_found);
        tr.innerHTML = `
          <td class="title-cell" data-label="Title">${titleHtml}${newBadge}${verifyBadge}${reason}${roleNote}</td>
          <td data-label="Company">${escapeHtml(job.company)}</td>
          <td data-label="Fit">${fitBadge(job.fit_band, job.fit_score)}</td>
          <td data-label="Location"><span class="location-label">${escapeHtml(job.location)}</span></td>
          <td data-label="Source"><span class="source-badge">${escapeHtml(job.source_label || job.source)}</span></td>
          <td data-label="Recency"><span class="recency-label">${escapeHtml(recencyLabel)}</span> ${escapeHtml(recencyDate)}</td>
          <td data-label="Details"><a class="btn" href="${escapeHtml(detailHref)}">Open details</a></td>
        `;
        jobsBody.appendChild(tr);
      }
    }

    function renderRefreshPanel(status) {
      if (!refreshMeta) return;
      const overall = status.overall_last_refreshed;
      const sources = status.sources || [];
      const sourceBits = sources
        .map((s) => {
          const when = s.last_sync ? formatDateTime(s.last_sync) : "never";
          const stale = s.is_stale ? " (stale)" : "";
          return `${escapeHtml(s.label)}: ${escapeHtml(when)}${stale}`;
        })
        .join(" · ");
      refreshMeta.innerHTML =
        `Last refreshed: <strong>${escapeHtml(formatDateTime(overall))}</strong>` +
        (sourceBits ? `<br><span class="muted">${sourceBits}</span>` : "");
      if (staleNotice) {
        staleNotice.hidden = !status.is_stale;
        if (status.is_stale) {
          const hours = status.stale_threshold_hours || 24;
          staleNotice.textContent =
            `Feed is older than ${hours} hours. Click Refresh jobs to sync SimplifyJobs.`;
        }
      }
      const active = status.active;
      if (refreshProgress) {
        refreshProgress.classList.add("loading-status");
        if (active && active.status === "running") {
          refreshProgress.hidden = false;
          if (refreshBtn) refreshBtn.disabled = true;
          if (refreshProgress.classList.contains("is-running")) {
            GaneshaLoading.update(
              refreshProgress,
              active.progress || "Refreshing"
            );
          } else {
            GaneshaLoading.start(
              refreshProgress,
              active.progress || "Refreshing",
              { control: refreshBtn }
            );
          }
        } else if (active && active.status === "completed") {
          refreshProgress.hidden = false;
          if (refreshBtn) refreshBtn.disabled = false;
          refreshProgress.classList.remove("is-running", "is-error");
          refreshProgress.classList.add("is-success");
          refreshProgress.removeAttribute("aria-busy");
          refreshProgress.innerHTML = renderSourceResults(active);
        } else if (active && active.status === "failed") {
          refreshProgress.hidden = false;
          if (refreshBtn) refreshBtn.disabled = false;
          refreshProgress.classList.remove("is-running", "is-success");
          refreshProgress.classList.add("is-error");
          refreshProgress.removeAttribute("aria-busy");
          refreshProgress.innerHTML = renderSourceResults(active);
        } else if (status.last_completed) {
          refreshProgress.hidden = false;
          if (refreshBtn) refreshBtn.disabled = false;
          refreshProgress.classList.remove("is-running");
          refreshProgress.removeAttribute("aria-busy");
          refreshProgress.innerHTML = renderSourceResults(status.last_completed);
        } else {
          refreshProgress.hidden = true;
          if (refreshBtn) refreshBtn.disabled = false;
        }
      }
    }

    function renderSourceResults(state) {
      const lines = (state.sources || []).map((s) => {
        if (s.status === "skipped") {
          return `<div><strong>${escapeHtml(s.label)}</strong>: skipped — ${escapeHtml(s.error_summary || "static")}</div>`;
        }
        if (s.status === "failed") {
          return `<div class="refresh-fail"><strong>${escapeHtml(s.label)}</strong>: failed — ${escapeHtml(s.error_summary || "error")}</div>`;
        }
        return (
          `<div><strong>${escapeHtml(s.label)}</strong>: ${escapeHtml(s.status)} · ` +
          `fetched ${s.fetched || 0}, new ${s.new || 0}, updated ${s.updated || 0}, ` +
          `closed ${s.closed || 0}</div>`
        );
      });
      const msg = state.message ? `<div class="muted">${escapeHtml(state.message)}</div>` : "";
      return msg + lines.join("");
    }

    async function loadRefreshStatus() {
      try {
        const response = await fetch("/api/jobs/refresh/status");
        if (!response.ok) return null;
        const data = await response.json();
        renderRefreshPanel(data);
        return data;
      } catch (_err) {
        return null;
      }
    }

    async function pollRefreshUntilDone() {
      clearTimeout(refreshPollTimer);
      const data = await loadRefreshStatus();
      if (data && data.active && data.active.status === "running") {
        refreshPollTimer = setTimeout(pollRefreshUntilDone, 1500);
        return;
      }
      await loadJobs();
    }

    async function triggerRefresh() {
      if (refreshProgress) {
        refreshProgress.hidden = false;
        GaneshaLoading.start(refreshProgress, "Starting refresh", {
          control: refreshBtn,
        });
      } else if (refreshBtn) {
        refreshBtn.disabled = true;
      }
      try {
        const response = await fetch("/api/jobs/refresh", { method: "POST" });
        const data = await response.json();
        if (response.status === 409) {
          setStatus("empty", "A refresh is already running.");
          pollRefreshUntilDone();
          return;
        }
        if (response.status === 429) {
          const msg = data.message || "Refresh rate-limited.";
          setStatus("error", escapeHtml(msg));
          if (refreshProgress) {
            GaneshaLoading.fail(refreshProgress, msg, { control: refreshBtn });
          } else if (refreshBtn) {
            refreshBtn.disabled = false;
          }
          return;
        }
        if (!response.ok && response.status !== 202) {
          const msg = data.message || data.error || "Refresh failed.";
          setStatus("error", escapeHtml(msg));
          if (refreshProgress) {
            GaneshaLoading.fail(refreshProgress, msg, { control: refreshBtn });
          } else if (refreshBtn) {
            refreshBtn.disabled = false;
          }
          return;
        }
        pollRefreshUntilDone();
      } catch (_err) {
        setStatus("error", "Could not start refresh.");
        if (refreshProgress) {
          GaneshaLoading.fail(refreshProgress, "Could not start refresh.", {
            control: refreshBtn,
          });
        } else if (refreshBtn) {
          refreshBtn.disabled = false;
        }
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
        renderRecencyCounts(data.recency_counts);
        if (refreshMeta && data.last_refreshed !== undefined) {
          renderRefreshPanel({
            overall_last_refreshed: data.last_refreshed,
            is_stale: data.is_stale,
            stale_threshold_hours: data.stale_threshold_hours,
            sources: [],
            active: null,
            last_completed: null,
          });
          loadRefreshStatus();
        }
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
        else if (name === "recency") el.value = "all";
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
    if (refreshBtn) refreshBtn.addEventListener("click", triggerRefresh);
    for (const name of ["fit", "recency", "source", "sort"]) {
      const el = form.elements.namedItem(name);
      if (el) {
        el.addEventListener("change", () => {
          currentPage = 1;
          loadJobs();
        });
      }
    }
    for (const name of ["q", "location"]) {
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

    const urlParams = new URLSearchParams(window.location.search);
    if (![...urlParams.keys()].length) {
      const prefs = loadPrefs();
      if (prefs) {
        for (const [k, v] of Object.entries(prefs)) {
          if (v != null) urlParams.set(k, String(v));
        }
      }
    }
    applyParamsToForm(urlParams);
    loadJobs();
  }

  function initJobDetail() {
    const jobId = config.jobId;
    const statusMessage = document.getElementById("status-message");
    const resumeBtn = document.getElementById("btn-generate-resume");
    const coverBtn = document.getElementById("btn-generate-cover");
    const prepareBtn = document.getElementById("btn-prepare-application");
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
    const applicationPanel = document.getElementById("application-panel");
    const applicationBody = document.getElementById("application-body");
    const applicationActions = document.getElementById("application-actions");
    const btnAppOpen = document.getElementById("btn-app-open");
    const btnAppFill = document.getElementById("btn-app-fill");
    const btnAppReveal = document.getElementById("btn-app-reveal");
    const btnAppSubmitted = document.getElementById("btn-app-submitted");
    const btnAppNeeds = document.getElementById("btn-app-needs");
    const btnAppCancel = document.getElementById("btn-app-cancel");

    let pendingAction = null;
    let detailData = null;
    let inFlight = false;
    let activeAttemptId = null;
    let preparePreviewData = null;

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
      if (prepareBtn) prepareBtn.disabled = busy;
      if (confirmGenerate) confirmGenerate.disabled = busy;
    }

    function formatUnresolved(item) {
      if (!item || typeof item !== "object") return String(item || "");
      if (item.kind === "sensitive_question_category") {
        return `Answer manually on employer form: ${item.category || "sensitive question"}`;
      }
      if (item.kind === "final_submission") {
        return "Final submission stays manual — Ganesha never clicks Submit.";
      }
      if (item.action) return String(item.action);
      return JSON.stringify(item);
    }

    function renderAttempt(attempt) {
      if (!applicationPanel || !applicationBody) return;
      activeAttemptId = attempt.attempt_id;
      applicationPanel.hidden = false;
      const terminal = ["cancelled", "submitted_user_confirmed", "failed"].includes(
        attempt.status
      );
      if (applicationActions) applicationActions.hidden = terminal;
      if (btnAppFill) {
        btnAppFill.hidden = terminal || !attempt.controlled_autofill_supported;
      }
      const resume = (attempt.package && attempt.package.resume) || {};
      const cover = (attempt.package && attempt.package.cover_letter) || null;
      const autofill = (attempt.package && attempt.package.controlled_autofill) || null;
      const unresolved = (attempt.unresolved || [])
        .slice(0, 8)
        .map((item) => `<li>${escapeHtml(formatUnresolved(item))}</li>`)
        .join("");
      const filledSummary = autofill && autofill.filled
        ? autofill.filled
            .filter((f) => f.status === "filled")
            .slice(0, 8)
            .map((f) => `<li>Filled: ${escapeHtml(f.label || f.field_key)}</li>`)
            .join("")
        : "";
      applicationBody.innerHTML = `
        <ul class="result-list">
          <li><strong>${escapeHtml(attempt.company || "")}</strong> — ${escapeHtml(attempt.role || "")}</li>
          <li>Status: <strong>${escapeHtml(attempt.status || "")}</strong></li>
          <li>Mode: Chrome + Simplify handoff available; controlled Greenhouse fill when supported</li>
          <li>Application URL: <code>${escapeHtml(attempt.application_url || "")}</code></li>
          <li>Resume: <code>${escapeHtml(resume.pdf_path || resume.docx_path || "missing")}</code>
            ${resume.validation_ok ? "(validated)" : ""}</li>
          <li>Cover letter: ${
            attempt.include_cover_letter && cover
              ? `<code>${escapeHtml(cover.pdf_path || cover.docx_path || "")}</code>`
              : "optional — not included"
          }</li>
          <li>Simplify: user-invoked in your Chrome profile; Ganesha cannot observe completion.</li>
          ${
            attempt.controlled_autofill_supported
              ? "<li>Controlled fill: Greenhouse adapter can fill supported fields only; never submits.</li>"
              : ""
          }
        </ul>
        <p class="muted">Sensitive employer questions require your explicit answers on the form.</p>
        ${filledSummary ? `<ul class="result-list">${filledSummary}</ul>` : ""}
        ${unresolved ? `<ul class="result-list">${unresolved}</ul>` : ""}
      `;
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
        const badge = data.description_badge || (job && job.description_badge);
        if (badge || data.description_incomplete) {
          warn.hidden = false;
          warn.textContent = badge
            ? `${badge}. Tailoring and eligibility may change once the full posting is fetched.`
            : "Job description looks incomplete. Tailoring quality may be limited.";
        } else {
          warn.hidden = true;
        }
        const eligEl = document.getElementById("job-eligibility");
        if (eligEl) {
          const reason = data.eligibility_reason
            ? ` (${String(data.eligibility_reason).split("_").join(" ")})`
            : "";
          eligEl.textContent = `${data.eligibility || "—"}${reason}`;
        }
        const badgesEl = document.getElementById("job-badges");
        if (badgesEl) {
          const chips = [];
          if (badge) {
            chips.push(`<span class="verify-badge">${escapeHtml(badge)}</span>`);
          }
          for (const flag of data.eligibility_flags || []) {
            chips.push(
              `<span class="auth-flag-badge">${escapeHtml(String(flag).split("_").join(" "))}</span>`
            );
          }
          for (const item of data.eligibility_flag_excerpts || []) {
            if (item && item.excerpt) {
              chips.push(
                `<span class="auth-flag-badge" title="${escapeHtml(item.excerpt)}">${escapeHtml(
                  String(item.flag || "note").split("_").join(" ")
                )}</span>`
              );
            }
          }
          badgesEl.innerHTML = chips.join(" ");
          badgesEl.hidden = chips.length === 0;
        }
        if (data.enrichment && data.enrichment.title_updated) {
          document.getElementById("job-title").textContent = job.title || "Untitled";
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
      confirmList.innerHTML = `<li><span class="loading-status is-running" id="preview-loading">Loading preview<span class="loading-ellipsis" aria-hidden="true"></span></span></li>`;
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

    async function openPrepareConfirm() {
      pendingAction = "prepare";
      confirmTitle.textContent = "Prepare application";
      confirmGenerate.disabled = true;
      confirmList.innerHTML = `<li><span class="loading-status is-running">Loading preparation preview<span class="loading-ellipsis" aria-hidden="true"></span></span></li>`;
      confirmPanel.hidden = false;
      resultPanel.hidden = true;
      try {
        const response = await fetch(
          `/api/jobs/${encodeURIComponent(jobId)}/application/prepare-preview`
        );
        const data = await response.json();
        preparePreviewData = data;
        if (!response.ok) {
          confirmList.innerHTML = `<li>${escapeHtml(data.error || "Preview failed")}</li>`;
          return;
        }
        const items = [
          `<strong>${escapeHtml(data.company || "")}</strong> — ${escapeHtml(data.role || "")}`,
          `Destination: <code>${escapeHtml(data.application_url || "missing")}</code>`,
          `Resume: ${
            data.resume
              ? `<code>${escapeHtml(data.resume.pdf_path || data.resume.docx_path || "")}</code> (reuse)`
              : "<span class='warn-text'>Generate a job-specific resume first</span>"
          }`,
          `Cover letter: optional${
            data.cover_letter
              ? ` — available stamp ${escapeHtml(data.cover_letter.stamp || "")}`
              : " — none on file"
          }`,
          `Mode: Chrome + Simplify assisted handoff (Ganesha does not control Simplify)`,
        ];
        if (data.fit_warning) {
          items.push(`<span class="warn-text">Fit warning: ${escapeHtml(data.fit_warning)}</span>`);
        }
        if (data.block_reason) {
          items.push(`<span class="warn-text">${escapeHtml(data.block_reason)}</span>`);
        }
        if ((data.active_attempts || []).length) {
          items.push(
            `<span class="warn-text">Active attempt exists (${escapeHtml(
              data.active_attempts[0].attempt_id
            )}). Confirming will force a new attempt.</span>`
          );
        }
        if (data.previously_applied) {
          items.push(
            `<span class="warn-text">${escapeHtml(
              data.previously_applied.message || "Previously applied"
            )}</span>`
          );
        }
        (data.simplify_limits || []).slice(0, 3).forEach((line) => {
          items.push(escapeHtml(line));
        });
        confirmList.innerHTML = items.map((i) => `<li>${i}</li>`).join("");
        confirmList.innerHTML += `
          <li><label><input type="checkbox" id="prep-include-cover"> Include optional cover letter</label></li>
          <li><label><input type="checkbox" id="prep-ack-fit" ${
            data.fit_warning ? "" : "checked"
          }> Acknowledge fit warning / proceed</label></li>
          <li><label><input type="checkbox" id="prep-ack-previous" ${
            data.previously_applied ? "" : "checked"
          }> Acknowledge previously-applied warning / proceed</label></li>
        `;
        confirmGenerate.disabled = !data.can_prepare;
        confirmGenerate.textContent = "Prepare";
      } catch (_err) {
        confirmList.innerHTML = "<li>Could not load application preview.</li>";
      }
    }

    if (prepareBtn) {
      prepareBtn.addEventListener("click", () => {
        if (inFlight) return;
        openPrepareConfirm();
      });
    }

    cancelGenerate.addEventListener("click", () => {
      confirmPanel.hidden = true;
      pendingAction = null;
      confirmGenerate.textContent = "Generate";
    });

    confirmGenerate.addEventListener("click", async () => {
      if (!pendingAction || inFlight) return;
      setBusy(true);
      const busyLabel =
        pendingAction === "prepare"
          ? "Preparing application package"
          : pendingAction === "resume"
            ? "Generating tailored resume"
            : "Generating tailored cover letter";
      GaneshaLoading.start(statusMessage, busyLabel, {
        control: [resumeBtn, coverBtn, prepareBtn, confirmGenerate],
      });
      statusMessage.hidden = false;
      try {
        let response;
        if (pendingAction === "prepare") {
          const includeCover = !!(document.getElementById("prep-include-cover") || {}).checked;
          const ackFit = !!(document.getElementById("prep-ack-fit") || {}).checked;
          const ackPrev = !!(document.getElementById("prep-ack-previous") || {}).checked;
          const forceNew = !!(preparePreviewData && (preparePreviewData.active_attempts || []).length);
          response = await fetch(
            `/api/jobs/${encodeURIComponent(jobId)}/application/prepare`,
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                confirm: true,
                include_cover_letter: includeCover,
                acknowledge_fit_warning: ackFit || !(preparePreviewData && preparePreviewData.fit_warning),
                acknowledge_previous_application:
                  ackPrev || !(preparePreviewData && preparePreviewData.previously_applied),
                force_new: forceNew,
              }),
            }
          );
        } else if (pendingAction === "resume") {
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
        confirmGenerate.textContent = "Generate";
        if (pendingAction === "prepare") {
          if (data.status === "prepared" && data.attempt) {
            setStatus("empty", "Application package prepared.");
            renderAttempt(data.attempt);
          } else {
            setStatus("error", escapeHtml(data.error || "Preparation failed."));
            if (applicationPanel) {
              applicationPanel.hidden = false;
              applicationBody.innerHTML = `<p>${escapeHtml(data.error || "Preparation failed.")}</p>`;
            }
          }
        } else {
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
        }
      } catch (_err) {
        setStatus("error", "Request failed.");
      } finally {
        pendingAction = null;
        setBusy(false);
      }
    });

    async function attemptAction(path, successMessage) {
      if (!activeAttemptId || inFlight) return;
      setBusy(true);
      const runningLabel =
        path === "/fill-supported"
          ? "Autofilling supported Greenhouse fields"
          : path === "/open"
            ? "Opening application handoff"
            : "Updating application attempt";
      GaneshaLoading.start(statusMessage, runningLabel, {
        control: [btnAppOpen, btnAppFill, btnAppReveal, btnAppSubmitted, btnAppNeeds, btnAppCancel],
      });
      statusMessage.hidden = false;
      try {
        const response = await fetch(
          `/api/application-attempts/${encodeURIComponent(activeAttemptId)}${path}`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ confirm: true }),
          }
        );
        const data = await response.json();
        if (!response.ok) {
          GaneshaLoading.fail(
            statusMessage,
            data.error || "Action failed.",
            { control: [btnAppOpen, btnAppFill, btnAppReveal, btnAppSubmitted, btnAppNeeds, btnAppCancel] }
          );
          return;
        }
        if (data.attempt) renderAttempt(data.attempt);
        let okMsg = successMessage;
        if (data.instructions) {
          okMsg =
            successMessage +
            " " +
            (data.instructions || []).slice(0, 1).join(" ");
        }
        if (data.simplify_handoff) {
          okMsg =
            "Opened in Chrome. Invoke Simplify yourself if installed, review answers, then confirm submission here. Ganesha did not submit.";
        }
        GaneshaLoading.succeed(statusMessage, okMsg, {
          control: [btnAppOpen, btnAppFill, btnAppReveal, btnAppSubmitted, btnAppNeeds, btnAppCancel],
        });
      } catch (_err) {
        GaneshaLoading.fail(statusMessage, "Action request failed.", {
          control: [btnAppOpen, btnAppFill, btnAppReveal, btnAppSubmitted, btnAppNeeds, btnAppCancel],
        });
      } finally {
        setBusy(false);
      }
    }

    if (btnAppOpen) {
      btnAppOpen.addEventListener("click", () =>
        attemptAction("/open", "Chrome handoff requested.")
      );
    }
    if (btnAppFill) {
      btnAppFill.addEventListener("click", () => {
        if (
          !window.confirm(
            "Fill supported Greenhouse fields in a dedicated Ganesha browser? Ganesha will not click Submit."
          )
        ) {
          return;
        }
        attemptAction(
          "/fill-supported",
          "Controlled fill finished (or paused). Review in the browser; Ganesha did not submit."
        );
      });
    }
    if (btnAppReveal) {
      btnAppReveal.addEventListener("click", () =>
        attemptAction("/reveal-documents", "Revealed selected documents locally.")
      );
    }
    if (btnAppSubmitted) {
      btnAppSubmitted.addEventListener("click", () => {
        if (
          !window.confirm(
            "Confirm only if you already submitted the employer form yourself. Ganesha never submits automatically."
          )
        ) {
          return;
        }
        attemptAction("/mark-submitted", "Marked submitted (user-confirmed).");
      });
    }
    if (btnAppNeeds) {
      btnAppNeeds.addEventListener("click", () =>
        attemptAction("/mark-needs-action", "Marked needs action.")
      );
    }
    if (btnAppCancel) {
      btnAppCancel.addEventListener("click", () =>
        attemptAction("/cancel", "Attempt cancelled.")
      );
    }

    if (coverPlanBtn) {
      coverPlanBtn.addEventListener("click", async () => {
        coverPlanPanel.hidden = false;
        coverPlanBody.innerHTML =
          `<p class="loading-status is-running">Loading cover-letter plan<span class="loading-ellipsis" aria-hidden="true"></span></p>`;
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
