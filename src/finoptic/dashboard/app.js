/* FinOptic dashboard — vanilla JS controller.
 *
 * Talks to the same-origin FastAPI JSON API using RELATIVE URLs:
 *   GET  /healthz
 *   POST /api/v1/ingest             (multipart, field "file")
 *   POST /api/v1/ingest/sample
 *   GET  /api/v1/batches
 *   GET  /api/v1/summary?batch_uid=
 *   GET  /api/v1/findings?batch_uid=&severity=&category=&provider=&limit=&offset=
 *   POST /api/v1/analyze            (body {batch_uid?})
 *   GET  /api/v1/export/remediation.sh?batch_uid=
 *
 * All DOM ids referenced here exist in index.html. No framework, no build.
 */
"use strict";

/* ------------------------------------------------------------------ *
 * App state
 * ------------------------------------------------------------------ */
const state = {
  batchUid: null, // null = "all batches"
  summary: null,
  findings: [],
  charts: { severity: null, category: null },
};

const SEVERITY_ORDER = ["critical", "high", "medium", "low"];
const SEVERITY_COLOR = {
  critical: "#ff5d6c",
  high: "#ff924c",
  medium: "#ffd23e",
  low: "#54c7ec",
};
const CATEGORY_PALETTE = [
  "#5b8cff", "#a472ff", "#2fbf71", "#ffb23e",
  "#ff5d6c", "#54c7ec", "#e879f9", "#34d399",
];

const TOP_FINDINGS_LIMIT = 25;

/* ------------------------------------------------------------------ *
 * Tiny DOM helpers
 * ------------------------------------------------------------------ */
const $ = (id) => document.getElementById(id);

function show(el, visible) {
  if (!el) return;
  if (visible) el.removeAttribute("hidden");
  else el.setAttribute("hidden", "");
}

/** Escape text for safe insertion into HTML. */
function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

/** Compact USD currency formatting with sensible precision. */
function fmtMoney(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return "$0";
  const abs = Math.abs(v);
  const opts =
    abs >= 100
      ? { maximumFractionDigits: 0 }
      : { minimumFractionDigits: 2, maximumFractionDigits: 2 };
  return "$" + v.toLocaleString("en-US", opts);
}

function fmtInt(n) {
  const v = Number(n);
  return Number.isFinite(v) ? v.toLocaleString("en-US") : "0";
}

function titleCase(s) {
  return String(s ?? "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function fmtTimestamp(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString("en-US", {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

/* ------------------------------------------------------------------ *
 * Toasts
 * ------------------------------------------------------------------ */
function toast(message, { kind = "info", title = "", timeout = 5000 } = {}) {
  const host = $("toast-host");
  if (!host) return;
  const el = document.createElement("div");
  el.className = `toast toast--${kind}`;
  el.setAttribute("role", kind === "error" ? "alert" : "status");
  const icon = kind === "error" ? "!" : kind === "success" ? "✓" : "i";
  el.innerHTML = `
    <span class="toast__icon">${esc(icon)}</span>
    <div class="toast__body">
      ${title ? `<div class="toast__title">${esc(title)}</div>` : ""}
      <div>${esc(message)}</div>
    </div>
    <button class="toast__close" aria-label="Dismiss">×</button>`;
  const remove = () => {
    el.classList.add("is-leaving");
    setTimeout(() => el.remove(), 260);
  };
  el.querySelector(".toast__close").addEventListener("click", remove);
  host.appendChild(el);
  if (timeout) setTimeout(remove, timeout);
}

/* ------------------------------------------------------------------ *
 * Fetch wrapper — robust error handling
 * ------------------------------------------------------------------ */
async function api(path, { method = "GET", body, headers, isForm = false } = {}) {
  const opts = { method, headers: { ...(headers || {}) } };
  if (body !== undefined) {
    if (isForm) {
      opts.body = body; // browser sets multipart boundary
    } else {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
  }

  let res;
  try {
    res = await fetch(path, opts);
  } catch (networkErr) {
    throw new Error(`Network error contacting ${path}: ${networkErr.message}`);
  }

  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const data = await res.json();
      if (data && data.detail) {
        detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
      }
    } catch (_) {
      /* non-JSON error body — keep status line */
    }
    throw new Error(detail);
  }

  if (res.status === 204) return null;
  const ctype = res.headers.get("content-type") || "";
  return ctype.includes("application/json") ? res.json() : res.text();
}

/* ------------------------------------------------------------------ *
 * Button busy-state
 * ------------------------------------------------------------------ */
function setBusy(btn, busy) {
  if (!btn) return;
  const spinner = btn.querySelector(".btn__spinner");
  if (busy) {
    btn.setAttribute("aria-busy", "true");
    btn.setAttribute("disabled", "");
    show(spinner, true);
  } else {
    btn.removeAttribute("aria-busy");
    btn.removeAttribute("disabled");
    show(spinner, false);
  }
}

/* ------------------------------------------------------------------ *
 * Health
 * ------------------------------------------------------------------ */
async function loadHealth() {
  const pill = $("health-pill");
  const text = $("health-text");
  try {
    const h = await api("/healthz"); // {status, version, llm_enabled}
    pill.className = "pill pill--ok";
    text.textContent = h.llm_enabled ? "online · LLM" : "online";
    if (h.version) $("footer-version").textContent = "v" + h.version;
  } catch (err) {
    pill.className = "pill pill--bad";
    text.textContent = "offline";
  }
}

/* ------------------------------------------------------------------ *
 * Batches
 * ------------------------------------------------------------------ */
async function loadBatches() {
  let batches = [];
  try {
    batches = await api("/api/v1/batches"); // [{batch_uid, source_filename, ...}]
  } catch (err) {
    // Non-fatal: dashboard still works against the "all batches" summary.
    return;
  }
  const sel = $("batch-select");
  const bar = $("batch-bar");
  if (!Array.isArray(batches) || batches.length === 0) {
    show(bar, false);
    return;
  }

  const opts = ['<option value="">All batches</option>'];
  for (const b of batches) {
    const label = `${b.source_filename || b.batch_uid} · ${b.provider || "—"} · ${fmtInt(
      b.finding_count
    )} findings · ${fmtMoney(b.total_monthly_waste)}/mo`;
    opts.push(`<option value="${esc(b.batch_uid)}">${esc(label)}</option>`);
  }
  sel.innerHTML = opts.join("");
  sel.value = state.batchUid || "";
  show(bar, true);
}

/* ------------------------------------------------------------------ *
 * Summary + findings load & render
 * ------------------------------------------------------------------ */
function buildQuery(params) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== null && v !== undefined && v !== "") q.set(k, v);
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

async function loadData() {
  const summaryQ = buildQuery({ batch_uid: state.batchUid });
  const findingsQ = buildQuery({ batch_uid: state.batchUid, limit: TOP_FINDINGS_LIMIT, offset: 0 });

  let summary = null;
  let findingsResp = null;
  try {
    [summary, findingsResp] = await Promise.all([
      api(`/api/v1/summary${summaryQ}`),
      api(`/api/v1/findings${findingsQ}`),
    ]);
  } catch (err) {
    toast(err.message, { kind: "error", title: "Failed to load data" });
    return;
  }

  state.summary = summary;
  // findings endpoint -> {items, total, limit, offset}
  state.findings =
    findingsResp && Array.isArray(findingsResp.items) ? findingsResp.items : [];
  const totalFindings =
    findingsResp && typeof findingsResp.total === "number"
      ? findingsResp.total
      : state.findings.length;

  renderKpis(summary);
  renderGeneratedAt(summary);
  updateExportLink();

  const hasData = (summary && (summary.finding_count > 0 || summary.resource_count > 0)) ||
    state.findings.length > 0;

  show($("empty-state"), !hasData);
  show($("content"), hasData);

  if (hasData) {
    renderSeverityChart(summary.by_severity || {});
    renderCategoryChart(summary.by_category || {});
    renderFindings(state.findings, totalFindings);
  }
}

function renderKpis(summary) {
  const s = summary || {};
  $("kpi-monthly").textContent = fmtMoney(s.total_monthly_waste || 0);
  $("kpi-annual").textContent = fmtMoney(s.total_annual_savings || 0);
  $("kpi-findings").textContent = fmtInt(s.finding_count || 0);
  $("kpi-resources").textContent = fmtInt(s.resource_count || 0);

  const provBits = Object.entries(s.by_provider || {})
    .filter(([, v]) => Number(v) > 0)
    .map(([k]) => titleCase(k));
  $("kpi-monthly-hint").textContent = provBits.length
    ? `across ${provBits.join(" + ")}`
    : "across detected resources";

  const critHigh =
    (s.by_severity && (Number(s.by_severity.critical || 0) + Number(s.by_severity.high || 0))) || 0;
  $("kpi-findings-hint").textContent = critHigh
    ? `${fmtMoney(critHigh)}/mo critical + high`
    : "units of waste";
}

function renderGeneratedAt(summary) {
  const ts = summary && summary.generated_at ? fmtTimestamp(summary.generated_at) : "";
  $("generated-at").textContent = ts ? `Updated ${ts}` : "";
}

/* ------------------------------------------------------------------ *
 * Charts
 * ------------------------------------------------------------------ */
function chartsReady() {
  return typeof window.Chart !== "undefined";
}

const CHART_FONT = { family: getComputedStyle(document.body).fontFamily, size: 12 };

function destroyChart(key) {
  if (state.charts[key]) {
    state.charts[key].destroy();
    state.charts[key] = null;
  }
}

function renderSeverityChart(bySeverity) {
  if (!chartsReady()) return;
  const canvas = $("chart-severity");
  if (!canvas) return;

  const labels = [];
  const data = [];
  const colors = [];
  for (const sev of SEVERITY_ORDER) {
    const val = Number(bySeverity[sev] || 0);
    if (val > 0) {
      labels.push(titleCase(sev));
      data.push(Math.round(val * 100) / 100);
      colors.push(SEVERITY_COLOR[sev]);
    }
  }

  destroyChart("severity");
  if (data.length === 0) return;

  state.charts.severity = new Chart(canvas, {
    type: "doughnut",
    data: {
      labels,
      datasets: [
        {
          data,
          backgroundColor: colors,
          borderColor: "#0b0f17",
          borderWidth: 3,
          hoverOffset: 6,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "62%",
      plugins: {
        legend: {
          position: "bottom",
          labels: { color: "#9aa7bd", font: CHART_FONT, padding: 14, usePointStyle: true },
        },
        tooltip: {
          callbacks: { label: (c) => ` ${c.label}: ${fmtMoney(c.parsed)}/mo` },
        },
      },
    },
  });
}

function renderCategoryChart(byCategory) {
  if (!chartsReady()) return;
  const canvas = $("chart-category");
  if (!canvas) return;

  const entries = Object.entries(byCategory)
    .map(([k, v]) => [k, Number(v) || 0])
    .filter(([, v]) => v > 0)
    .sort((a, b) => b[1] - a[1]);

  destroyChart("category");
  if (entries.length === 0) return;

  const labels = entries.map(([k]) => titleCase(k));
  const data = entries.map(([, v]) => Math.round(v * 100) / 100);
  const colors = entries.map((_, i) => CATEGORY_PALETTE[i % CATEGORY_PALETTE.length]);

  state.charts.category = new Chart(canvas, {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          label: "Monthly waste",
          data,
          backgroundColor: colors,
          borderRadius: 6,
          maxBarThickness: 46,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (c) => ` ${fmtMoney(c.parsed.y)}/mo` } },
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: { color: "#9aa7bd", font: CHART_FONT },
        },
        y: {
          beginAtZero: true,
          grid: { color: "rgba(35,48,71,0.5)" },
          ticks: {
            color: "#9aa7bd",
            font: CHART_FONT,
            callback: (v) => fmtMoney(v),
          },
        },
      },
    },
  });
}

/* ------------------------------------------------------------------ *
 * Findings table
 * ------------------------------------------------------------------ */
function renderFindings(items, total) {
  const body = $("findings-body");
  const meta = $("findings-meta");
  body.innerHTML = "";

  if (!items || items.length === 0) {
    body.innerHTML = `<tr><td colspan="6" class="empty-cell">No findings for this selection.</td></tr>`;
    meta.textContent = "";
    return;
  }

  meta.textContent =
    total > items.length
      ? `showing top ${items.length} of ${fmtInt(total)} findings`
      : `${fmtInt(items.length)} finding${items.length === 1 ? "" : "s"}`;

  const frag = document.createDocumentFragment();

  items.forEach((f, idx) => {
    const sev = String(f.severity || "low").toLowerCase();
    const rem = f.remediation || {};
    const cmds = Array.isArray(rem.cli_commands) ? rem.cli_commands : [];
    const detailId = `rem-${idx}`;

    const region = f.region ? esc(f.region) : "";
    const account = f.account_id ? esc(f.account_id) : "";
    const metaBits = [esc(String(f.provider || "").toUpperCase()), region, account]
      .filter(Boolean)
      .join(" · ");

    // --- main row ---
    const tr = document.createElement("tr");
    tr.className = "row-main";
    tr.innerHTML = `
      <td class="col-sev">
        <span class="sev sev--${esc(sev)}">${esc(titleCase(sev))}</span>
      </td>
      <td class="col-res">
        <span class="res-id">${esc(f.resource_id || "—")}</span>
        ${f.title ? `<span class="res-title">${esc(f.title)}</span>` : ""}
        ${metaBits ? `<span class="res-meta">${metaBits}</span>` : ""}
      </td>
      <td class="col-type"><span class="type-tag">${esc(f.resource_type || "—")}</span></td>
      <td class="col-cat"><span class="cat-tag">${esc(titleCase(f.category || "—"))}</span></td>
      <td class="col-cost num">${fmtMoney(f.monthly_cost || 0)}</td>
      <td class="col-rem rem-cell"></td>`;

    // remediation cell
    const remCell = tr.querySelector(".rem-cell");
    if (cmds.length || rem.strategy) {
      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "rem-toggle";
      toggle.setAttribute("aria-expanded", "false");
      toggle.setAttribute("aria-controls", detailId);
      const label = cmds.length
        ? `${cmds.length} command${cmds.length === 1 ? "" : "s"}`
        : "View remediation";
      toggle.innerHTML = `
        <span class="rem-toggle__chevron">›</span>
        <span>${esc(label)}</span>`;
      remCell.appendChild(toggle);
      if (rem.strategy) {
        const strat = document.createElement("span");
        strat.className = "rem-strategy";
        strat.textContent = rem.strategy;
        remCell.appendChild(strat);
      }
      toggle.addEventListener("click", () => toggleRemediation(toggle, detailId));
    } else {
      remCell.innerHTML = `<span class="empty-cell">—</span>`;
    }

    frag.appendChild(tr);

    // --- detail row (hidden until expanded) ---
    const detailTr = document.createElement("tr");
    detailTr.className = "rem-detail-row";
    detailTr.id = detailId;
    detailTr.hidden = true;
    detailTr.innerHTML = buildRemediationDetail(rem, cmds);
    frag.appendChild(detailTr);

    // wire copy button
    const copyBtn = detailTr.querySelector(".rem-copy");
    if (copyBtn) {
      copyBtn.addEventListener("click", () => copyCommands(cmds, copyBtn));
    }
  });

  body.appendChild(frag);
}

function buildRemediationDetail(rem, cmds) {
  const risk = String(rem.risk || "low").toLowerCase();
  const reversible = rem.reversible === false ? "irreversible" : "reversible";
  const codeLines = cmds.length
    ? cmds.map((c) => esc(c)).join("\n")
    : '<span class="cmt"># No CLI commands provided — see API logic / notes.</span>';

  const apiNote =
    !cmds.length && rem.api_logic
      ? `<span class="cmt"># API: ${esc(rem.api_logic)}</span>`
      : "";

  const notes = rem.notes
    ? `<div class="rem-notes">${esc(rem.notes)}</div>`
    : "";

  return `
    <td colspan="6">
      <div class="rem-detail">
        <div class="rem-detail__head">
          <span class="rem-detail__title">Remediation commands</span>
          <span class="rem-detail__chips">
            <span class="risk-tag risk-tag--${esc(risk)}">${esc(risk)} risk</span>
            <span class="risk-tag risk-tag--${reversible === "reversible" ? "low" : "high"}">${reversible}</span>
            <button type="button" class="rem-copy btn btn--ghost btn--sm" ${cmds.length ? "" : "disabled"}>Copy</button>
          </span>
        </div>
        <pre class="code-block">${codeLines}${apiNote ? "\n" + apiNote : ""}</pre>
        ${notes}
      </div>
    </td>`;
}

function toggleRemediation(toggle, detailId) {
  const detail = $(detailId);
  if (!detail) return;
  const open = toggle.getAttribute("aria-expanded") === "true";
  toggle.setAttribute("aria-expanded", String(!open));
  detail.hidden = open;
}

async function copyCommands(cmds, btn) {
  const text = (cmds || []).join("\n");
  const original = btn.textContent;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      // Fallback for non-secure contexts.
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    btn.textContent = "Copied!";
    toast("Remediation commands copied to clipboard.", { kind: "success", timeout: 2500 });
  } catch (err) {
    btn.textContent = "Copy failed";
    toast("Could not copy to clipboard.", { kind: "error" });
  }
  setTimeout(() => {
    btn.textContent = original;
  }, 1800);
}

/* ------------------------------------------------------------------ *
 * Export link
 * ------------------------------------------------------------------ */
function updateExportLink() {
  const link = $("export-link");
  if (!link) return;
  link.href = `/api/v1/export/remediation.sh${buildQuery({ batch_uid: state.batchUid })}`;
}

/* ------------------------------------------------------------------ *
 * Ingest: sample + upload
 * ------------------------------------------------------------------ */
async function loadSample(btn) {
  setBusy(btn, true);
  try {
    const resp = await api("/api/v1/ingest/sample", { method: "POST" });
    afterIngest(resp, "Sample data loaded");
  } catch (err) {
    toast(err.message, { kind: "error", title: "Could not load sample" });
  } finally {
    setBusy(btn, false);
  }
}

async function uploadFile(file) {
  if (!file) return;
  const form = new FormData();
  form.append("file", file, file.name); // field name MUST be "file"

  // Reflect busy on whichever upload buttons exist.
  const labels = document.querySelectorAll(".file-btn");
  labels.forEach((l) => l.setAttribute("aria-busy", "true"));
  toast(`Uploading ${file.name}…`, { kind: "info", timeout: 2500 });

  try {
    const resp = await api("/api/v1/ingest", { method: "POST", body: form, isForm: true });
    afterIngest(resp, `Ingested ${file.name}`);
  } catch (err) {
    toast(err.message, { kind: "error", title: "Upload failed" });
  } finally {
    labels.forEach((l) => l.removeAttribute("aria-busy"));
  }
}

/** Common post-ingest flow: select the new batch, refresh, reset analysis. */
async function afterIngest(resp, successTitle) {
  // IngestResponse: {batch_uid, source_filename, provider, record_count,
  //   finding_count, total_monthly_waste, total_annual_savings, by_severity}
  if (resp && resp.batch_uid) {
    state.batchUid = resp.batch_uid;
  }
  resetAnalysis();
  toast(
    `${fmtInt(resp && resp.finding_count)} findings · ${fmtMoney(
      resp && resp.total_monthly_waste
    )}/mo waste detected.`,
    { kind: "success", title: successTitle }
  );
  await loadBatches();
  await loadData();
}

/* ------------------------------------------------------------------ *
 * AI analysis
 * ------------------------------------------------------------------ */
function resetAnalysis() {
  show($("analysis-result"), false);
  show($("analysis-empty"), true);
}

async function runAnalysis(btn) {
  setBusy(btn, true);
  try {
    const resp = await api("/api/v1/analyze", {
      method: "POST",
      body: state.batchUid ? { batch_uid: state.batchUid } : {},
    });
    renderAnalysis(resp);
  } catch (err) {
    toast(err.message, { kind: "error", title: "Analysis failed" });
  } finally {
    setBusy(btn, false);
  }
}

function renderAnalysis(resp) {
  // AnalyzeResponse: {batch_uid, mode, provider, executive_summary,
  //   prioritized_runbook:[str], total_monthly_waste, total_annual_savings, generated_at}
  if (!resp) return;
  const modeBadge = $("analysis-mode");
  const provBadge = $("analysis-provider");

  const mode = String(resp.mode || "deterministic");
  modeBadge.textContent = mode === "llm" ? "LLM analysis" : "Deterministic";
  const provider = String(resp.provider || "none");
  if (provider && provider !== "none") {
    provBadge.textContent = titleCase(provider);
    show(provBadge, true);
  } else {
    show(provBadge, false);
  }

  $("analysis-summary").textContent = resp.executive_summary || "No summary returned.";

  const ol = $("analysis-runbook");
  ol.innerHTML = "";
  const steps = Array.isArray(resp.prioritized_runbook) ? resp.prioritized_runbook : [];
  if (steps.length === 0) {
    ol.innerHTML = `<li>No runbook steps were generated.</li>`;
  } else {
    const frag = document.createDocumentFragment();
    for (const step of steps) {
      const li = document.createElement("li");
      li.textContent = String(step);
      frag.appendChild(li);
    }
    ol.appendChild(frag);
  }

  show($("analysis-empty"), false);
  show($("analysis-result"), true);
  toast("Analysis complete.", { kind: "success", timeout: 2500 });
}

/* ------------------------------------------------------------------ *
 * Wiring
 * ------------------------------------------------------------------ */
function wireEvents() {
  $("btn-load-sample").addEventListener("click", (e) => loadSample(e.currentTarget));
  const sample2 = $("btn-load-sample-2");
  if (sample2) sample2.addEventListener("click", (e) => loadSample(e.currentTarget));

  const onFile = (e) => {
    const file = e.target.files && e.target.files[0];
    uploadFile(file);
    e.target.value = ""; // allow re-uploading the same file
  };
  $("file-input").addEventListener("change", onFile);
  const fi2 = $("file-input-2");
  if (fi2) fi2.addEventListener("change", onFile);

  $("btn-analyze").addEventListener("click", (e) => runAnalysis(e.currentTarget));

  $("batch-select").addEventListener("change", (e) => {
    state.batchUid = e.target.value || null;
    resetAnalysis();
    loadData();
  });
}

/* ------------------------------------------------------------------ *
 * Boot
 * ------------------------------------------------------------------ */
async function boot() {
  wireEvents();
  resetAnalysis();
  await loadHealth();
  await loadBatches();
  await loadData();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
