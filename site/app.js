import {
  visibleRoles, sortRoles, safeUrl, formatDate, searchRoles, daysUntil, urgency, deadlineBucket, STAGES, stageById,
  toRole, programStatus, DEADLINES,
} from "./filters.js";
import {
  createTracker, prefs, createProfile, createCrm, createDismissed, exportAll, importAll, RELATIONSHIP_STAGES,
} from "./store.js";
import { assess, OUTCOMES, suggestions, REGIONS } from "./eligibility.js";
import { rank } from "./relevance.js";
import { compose, mailto, DRAFT_KINDS } from "./outreach.js";

// --- reference data -----------------------------------------------------------------------------------------------
// Labels match tracker/taxonomy.py.
const SECTORS = {
  investment_banking: "Investment banking", m_and_a: "M&A advisory", ecm: "Equity capital markets",
  dcm: "Debt capital markets", sales_trading: "Sales & trading", research: "Research", asset_management: "Asset management",
  wealth_management: "Wealth management", private_banking: "Private banking", private_equity: "Private equity",
  venture_capital: "Venture capital", hedge_funds: "Hedge funds", alternatives: "Alternatives", family_office: "Family office",
  corporate_finance: "Corporate finance", treasury: "Treasury", risk: "Financial risk", credit: "Credit",
  investment_consulting: "Investment consulting", banking: "Banking", fintech: "Fintech", other_finance: "Other finance",
};
const SEASONS = { summer: "Summer", spring: "Spring", winter: "Winter", off_cycle: "Off-cycle", insight: "Insight week",
  working_student: "Working student", part_time: "Part-time", graduate: "Graduate", unknown: "—" };
const LIFECYCLE = { pending: "unconfirmed", unavailable: "source failing", unknown: "unknown", expired: "expired" };
const ELIG_SHORT = { eligible: "Eligible", potential: "Possible", sponsorship: "Sponsorship", language_gap: "Language gap",
  ineligible: "Ineligible", insufficient: "Unknown" };
const HEALTH = { failing: "Failing", blocked: "Blocked", gone: "Gone", degraded: "Degraded", unknown: "Not checked yet",
  ok: "Working", disabled: "Disabled" };
const PIPELINE = STAGES.filter((s) => s.rank >= 0); // saved → offer, the stepper
const THEME_FONTS = {
  ledger: "family=Instrument+Sans:wght@400;500;600&family=Newsreader:opsz,wght@6..72,400;6..72,500&family=JetBrains+Mono:wght@400;500",
  swiss: "family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500",
  terminal: "family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500",
};
const VIEWS = { discover: "Discover", programs: "Programs", firms: "Firms", outreach: "Outreach", pipeline: "Pipeline",
  deadlines: "Deadlines", sources: "Sources", profile: "Profile" };
const NUMBERED = ["discover", "programs", "firms", "outreach", "pipeline", "deadlines", "sources"];

const regionNames = new Intl.DisplayNames(["en"], { type: "region" });
const languageNames = new Intl.DisplayNames(["en"], { type: "language" });
const countryName = (c) => { try { return regionNames.of(c) || c; } catch { return c; } };
const languageName = (l) => { try { return languageNames.of(l) || l; } catch { return l; } };
const localToday = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
const shortDate = (s) => {
  if (!s) return "";
  const d = new Date(String(s).slice(0, 10) + "T00:00:00Z");
  return isNaN(d) ? String(s) : d.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
};
const plural = (n, one, many = one + "s") => `${n} ${n === 1 ? one : many}`;

// --- state ----------------------------------------------------------------------------------------------------------
const tracker = createTracker();
const profile = createProfile();
const crm = createCrm();
const dismissed = createDismissed();
const saved = prefs.load();
const state = {
  data: null,
  market: { employers: [], programs: [], prospects: [] },
  ops: null,
  digest: { items: [] },
  employers: new Map(),
  programs: new Map(),
  prospects: new Map(),
  roles: [],
  byId: new Map(),
  detailKind: "role",   // role | program | firm | source
  today: localToday(),
  view: "discover",
  query: "",
  openOnly: saved.openOnly ?? true,
  confirmedOnly: saved.confirmedOnly ?? false,
  showHidden: saved.showHidden ?? false,
  trackedOnly: saved.trackedOnly ?? false,
  filters: {
    countries: new Set(saved.filters?.countries || []),
    sectors: new Set(saved.filters?.sectors || []),
    seasons: new Set(saved.filters?.seasons || []),
    languages: new Set(saved.filters?.languages || []),
    eligibility: new Set(saved.filters?.eligibility || []),
    firms: new Set(saved.filters?.firms || []),
    years: new Set(saved.filters?.years || []),
  },
  deadline: saved.deadline || "any",           // any | 7 | 14 | 30 | dated | rolling
  newOnly: saved.newOnly ?? false,
  untrackedOnly: saved.untrackedOnly ?? false,
  languageMode: saved.languageMode || "all",   // all | any
  firmFilter: saved.firmFilter || "all",       // all | live | prospects | uncovered
  firmFilters: {
    sectors: new Set(saved.firmFilters?.sectors || []),
    countries: new Set(saved.firmFilters?.countries || []),
  },
  hiddenCols: new Set(saved.hiddenCols || []),  // Discover columns the user switched off
  wrap: saved.wrap ?? true,                     // long text wraps onto two lines instead of being cut
  colWidths: saved.colWidths || {},             // { view: { column: px } } set by dragging a column edge
  detailWidth: saved.detailWidth || null,       // px, set by dragging the detail pane's edge
  sourceHealth: new Set(saved.sourceHealth || []),
  sort: saved.sort || { key: "closes", dir: "asc" },
  theme: saved.theme || document.documentElement.dataset.theme || "ledger",
  rowIds: [],
  focusId: null,
  detailId: null,
};

function savePrefs() {
  prefs.save({
    theme: state.theme, openOnly: state.openOnly, trackedOnly: state.trackedOnly, sort: state.sort,
    confirmedOnly: state.confirmedOnly, showHidden: state.showHidden, firmFilter: state.firmFilter,
    sourceHealth: [...state.sourceHealth], deadline: state.deadline, newOnly: state.newOnly,
    untrackedOnly: state.untrackedOnly, languageMode: state.languageMode, hiddenCols: [...state.hiddenCols],
    wrap: state.wrap, colWidths: state.colWidths, detailWidth: state.detailWidth,
    filters: Object.fromEntries(Object.entries(state.filters).map(([k, v]) => [k, [...v]])),
    firmFilters: Object.fromEntries(Object.entries(state.firmFilters).map(([k, v]) => [k, [...v]])),
  });
}

// --- tiny DOM helper ------------------------------------------------------------------------------------------------
const $ = (sel, root = document) => root.querySelector(sel);
// replaceChildren, skipping null/false so optional sections never render as the text "null".
const fill = (el, ...children) => el.replaceChildren(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
function h(tag, attrs, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(e.dataset, v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) if (c !== null && c !== undefined && c !== false) e.append(c);
  return e;
}
const svgStar = (filled) => {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 16 16");
  s.setAttribute("aria-hidden", "true");
  s.innerHTML = `<path d="M8 1.6l1.9 4.1 4.5.5-3.4 3 1 4.4L8 11.3l-3.9 2.3 1-4.4-3.4-3 4.5-.5z" fill="${filled ? "currentColor" : "none"}" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>`;
  return s;
};

let toastTimer;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2200);
}

// --- role helpers ---------------------------------------------------------------------------------------------------
// A tracked role resolves to the live posting when present, otherwise to its saved snapshot.
function resolve(id) {
  const live = state.byId.get(id);
  if (live) return live;
  const e = tracker.get(id);
  return e ? { ...e.role, id, status: "removed" } : null;
}
const isNew = (r) => r.first_seen && daysUntil(r.first_seen, state.today) >= -6;
const placeOf = (r) => [r.city, (r.countries || []).join(" · ")].filter(Boolean).join(", ");
const langCodes = (r) => (r.languages_required || []).map((l) => l.toUpperCase()).join(" ");

function daysCell(dateStr, { closed = false } = {}) {
  if (closed) return h("span", { class: "days past" }, "closed");
  const d = daysUntil(dateStr, state.today);
  if (d === null) return h("span", { class: "days none" }, "rolling");
  const u = urgency(d);
  const text = d < 0 ? "passed" : d === 0 ? "today" : d === 1 ? "1 day" : `${d} days`;
  return h("span", { class: `days ${u}`, title: formatDate(dateStr) }, text);
}

function stageView(stageId) {
  const s = stageById(stageId);
  if (!s) return h("span", { class: "sub" }, "");
  const rank = s.rank < 0 ? 0 : s.rank;
  const marks = h("span", { class: "marks", "aria-hidden": "true" },
    PIPELINE.slice(1).map((p) => h("i", { class: p.rank <= rank && s.rank >= 1 ? "on" : null })));
  return h("span", { class: `stage ${s.id}` }, marks, s.label);
}

function starButton(role) {
  const on = !!tracker.get(role.id);
  return h("button", {
    class: "star", type: "button", "aria-pressed": String(on),
    "aria-label": on ? `Remove ${role.title} from tracking` : `Shortlist ${role.title}`,
    title: on ? "Tracked — press s to remove" : "Shortlist (s)",
    onclick: (ev) => { ev.stopPropagation(); toggleSaved(role.id); },
  }, svgStar(on));
}

// Small status words, in the same type as the "new" tag.
function lifecycleTag(r) {
  if (r.excluded_reason) return h("span", { class: "tag muted", title: "Excluded by the tracker; shown because Hidden is on" },
    r.excluded_reason === "not_internship" ? "not an internship?" : "outside your countries");
  if (r.dismissed) return h("span", { class: "tag muted" }, "dismissed");
  const t = LIFECYCLE[r.lifecycle];
  return t ? h("span", { class: `tag ${r.lifecycle === "pending" ? "soon" : "muted"}`,
    title: r.lifecycle === "pending" ? "Found by search or an aggregator; not yet seen on the employer's own site" : null }, t) : null;
}
function eligTag(r) {
  return h("span", { class: `elig ${r.eligibility}`, title: `${OUTCOMES[r.eligibility]} · fit score ${r.match}` }, ELIG_SHORT[r.eligibility]);
}

// --- actions --------------------------------------------------------------------------------------------------------
function toggleSaved(id) {
  const role = resolve(id);
  if (!role) return;
  const e = tracker.get(id);
  if (e && e.stage !== "saved" && !confirm(`Stop tracking ${role.firm_name} — ${role.title}? Notes and stage will be removed.`)) return;
  tracker.setStage(role, e ? null : "saved");
  toast(e ? "Removed from tracking" : `Shortlisted · ${role.firm_name}`);
}
function setStage(id, stage) {
  const role = resolve(id);
  if (!role) return;
  if (!tracker.setStage(role, stage)) toast("Could not save — browser storage is unavailable");
  else toast(stage ? `${stageById(stage).label} · ${role.firm_name}` : "Removed from tracking");
}
function stepStage(id, delta) {
  const e = tracker.get(id);
  const cur = e ? stageById(e.stage) : null;
  const rank = cur ? Math.max(cur.rank, 0) : -1;
  const next = PIPELINE.find((s) => s.rank === rank + delta);
  if (delta < 0 && rank <= 0) return setStage(id, null);
  if (next) setStage(id, next.id);
}

// --- coverage & meta ------------------------------------------------------------------------------------------------
function renderCoverage() {
  const d = state.data;
  const box = $("#coverage");
  box.replaceChildren();
  if (!d) return;
  const ops = state.ops;
  const updated = d.generated_at ? new Date(d.generated_at) : null;
  const when = updated && !isNaN(updated)
    ? updated.toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "—";
  const sources = ops?.sources || [];
  const working = sources.filter((s) => s.enabled && s.health === "ok").length;
  const failing = sources.filter((s) => s.enabled && ["failing", "blocked", "gone"].includes(s.health));
  box.append(
    h("div", null, "Updated ", h("strong", { class: "num" }, when)),
    h("div", null, h("strong", { class: "num" }, String(working)), ` of ${sources.filter((s) => s.enabled).length} sources working`),
  );
  if (ops?.ai?.exhausted) box.append(h("div", { class: "warn" }, `AI limit reached (${ops.ai.exhausted.replaceAll("_", " ")}) — reading resumes automatically.`));
  else if (ops && !ops.ai?.available) box.append(h("div", { class: "warn" }, `AI unavailable (${(ops.ai?.blocked_reason || "").replaceAll("_", " ")}) — job boards are still read.`));
  const notCovered = state.market.employers.filter((e) => e.access_note).map((e) => e.name);
  const digest = (state.digest.items || []);
  if (digest.length) {
    box.append(h("details", null, h("summary", null, `Today's digest (${digest.length})`),
      h("ul", null, digest.slice(0, 30).map((i) => h("li", { class: i.priority === "urgent" ? "warn" : null }, i.title)))));
  }
  const lists = [["Sources failing", failing.map((s) => `${state.employers.get(s.employer_id)?.name || s.id} · ${HEALTH[s.health]}`)],
    ["Not covered", notCovered]];
  for (const [title, names] of lists) {
    if (!names?.length) continue;
    box.append(h("details", null, h("summary", null, `${title} (${names.length})`), h("ul", null, names.map((n) => h("li", null, n)))));
  }
}

function pipelineEntries() {
  return Object.values(tracker.all()).map((e) => ({ entry: e, role: resolve(e.id) })).filter((x) => x.role);
}

function renderCounts() {
  const open = state.roles.filter((r) => r.status === "open" && !r.excluded_reason && !r.dismissed).length;
  const active = pipelineEntries().filter((x) => !stageById(x.entry.stage)?.closed).length;
  const week = deadlineEvents().filter((ev) => ev.days >= 0 && ev.days <= 6).length;
  $("#count-discover").textContent = open || "";
  $("#count-pipeline").textContent = active || "";
  $("#count-deadlines").textContent = week || "";
  const soon = [...state.programs.values()].filter((p) => ["confirmed", "estimated"].includes(programStatus(p, state.today).key)).length;
  $("#count-programs").textContent = soon || "";
  $("#count-firms").textContent = state.market.employers.length || "";
  const due = Object.values(crm.all()).filter((x) => x.next_date && x.next_date <= state.today && x.stage !== "closed").length;
  $("#count-outreach").textContent = due || "";
  const bad = (state.ops?.sources || []).filter((x) => x.enabled && ["failing", "blocked", "gone"].includes(x.health)).length;
  $("#count-sources").textContent = bad || "";
}

// --- toolbar --------------------------------------------------------------------------------------------------------
function optionCounts(getter, items = null) {
  const counts = new Map();
  for (const r of items || state.roles) {
    if (!items && state.openOnly && r.status !== "open") continue;
    if (!items && !state.showHidden && (r.excluded_reason || r.dismissed)) continue;
    for (const v of getter(r)) counts.set(v, (counts.get(v) || 0) + 1);
  }
  return counts;
}

// A checkbox menu. `sel` is the Set it edits; `after` re-renders. Marked with data-key so it can be reopened.
function checkMenu(key, label, { sel, counts, nameOf, hint, extra, after, sortBy = "name" }) {
  const values = [...new Set([...counts.keys(), ...sel])].sort((a, b) => sortBy === "count"
    ? (counts.get(b) || 0) - (counts.get(a) || 0) || nameOf(a).localeCompare(nameOf(b))
    : nameOf(a).localeCompare(nameOf(b)));
  return h("details", { class: `menu${sel.size ? " active" : ""}`, dataset: { key } },
    h("summary", { "aria-label": `${label} filter${sel.size ? `, ${sel.size} selected` : ""}` },
      label, sel.size ? h("span", { class: "badge" }, String(sel.size)) : null),
    h("div", { class: "menu-pop", role: "group", "aria-label": label },
      values.length > 12 ? h("input", { class: "menu-find", type: "search", placeholder: `Find ${label.toLowerCase()}…`,
        "aria-label": `Find ${label.toLowerCase()}`, oninput: (ev) => {
          const q = ev.target.value.toLowerCase();
          ev.target.parentElement.querySelectorAll(".opt").forEach((o) => { o.hidden = !o.textContent.toLowerCase().includes(q); });
        } }) : null,
      extra || null,
      values.length ? values.map((v) => h("label", { class: "opt" },
        h("input", {
          type: "checkbox", checked: sel.has(v),
          onchange: (ev) => { ev.target.checked ? sel.add(v) : sel.delete(v); savePrefs(); after(key); },
        }),
        h("span", null, nameOf(v)),
        h("span", { class: "num" }, String(counts.get(v) || 0)),
      )) : h("p", { class: "hint" }, "No values yet."),
      hint ? h("p", { class: "hint" }, hint) : null,
      sel.size ? h("div", { class: "menu-actions" },
        h("button", { class: "link", type: "button", onclick: () => { sel.clear(); savePrefs(); after(key); } }, "Clear")) : null,
    ));
}

// A single-choice menu.
function radioMenu(key, label, { options, value, set, defaultValue }) {
  const active = value !== defaultValue;
  return h("details", { class: `menu${active ? " active" : ""}`, dataset: { key } },
    h("summary", null, active ? options[value] : label),
    h("div", { class: "menu-pop", role: "radiogroup", "aria-label": label }, Object.entries(options).map(([k, text]) =>
      h("label", { class: "opt" }, h("input", { type: "radio", name: `menu-${key}`, checked: value === k,
        onchange: () => set(k) }), h("span", null, text), h("span")))));
}

function filterMenu(key, label, getter, nameOf, hint, extra) {
  return checkMenu(key, label, { sel: state.filters[key], counts: optionCounts(getter), nameOf, hint, extra,
    after: (k) => { renderView(); renderToolbar(k); } });
}

function toggle(label, checked, onchange, title) {
  return h("label", { class: "toggle", title }, h("input", { type: "checkbox", checked, onchange: (ev) => onchange(ev.target.checked) }), label);
}

const discoverFiltersActive = () => Object.values(state.filters).some((s) => s.size) || state.deadline !== "any"
  || state.newOnly || state.untrackedOnly;
function clearDiscoverFilters() {
  Object.values(state.filters).forEach((s) => s.clear());
  state.deadline = "any"; state.newOnly = false; state.untrackedOnly = false;
}

// Secondary on/off filters for Discover, grouped so the toolbar stays on one line.
function showMenu() {
  const items = [
    ["newOnly", "New this week", "Only roles first found in the last 7 days"],
    ["untrackedOnly", "Not tracked yet", "Hide roles already in your pipeline"],
    ["confirmedOnly", "Confirmed only", "Hide postings found by search and not yet seen on the employer's own site"],
    ["showHidden", "Show hidden", "Also show postings you dismissed and ones the tracker excluded"],
  ];
  const on = items.filter(([k]) => state[k]).length;
  return h("details", { class: `menu${on ? " active" : ""}`, dataset: { key: "show" } },
    h("summary", { "aria-label": `Show options${on ? `, ${on} on` : ""}` }, "Show", on ? h("span", { class: "badge" }, String(on)) : null),
    h("div", { class: "menu-pop", role: "group", "aria-label": "Show" }, items.map(([k, label, hint]) =>
      h("label", { class: "opt", title: hint }, h("input", { type: "checkbox", checked: state[k],
        onchange: (ev) => { state[k] = ev.target.checked; savePrefs(); renderView(); renderToolbar("show"); } }),
      h("span", null, label), h("span")))));
}

// Columns the user can switch off in Discover, and the wrap setting. Firm, Role and Left always show.
const OPTIONAL_COLS = { loc: "Location", season: "Intake", sector: "Sector", lang: "Languages", fit: "Fit",
  closes: "Closing date", status: "Status" };
function viewMenu() {
  const rerender = () => { savePrefs(); renderView(); renderToolbar("view"); };
  const widthKeys = Object.keys(state.colWidths).filter((k) => k.startsWith(state.view + ":") && Object.keys(state.colWidths[k]).length);
  return h("details", { class: "menu", dataset: { key: "view" } },
    h("summary", { title: "Columns, text wrapping and widths" }, "View"),
    h("div", { class: "menu-pop", role: "group", "aria-label": "View" },
      h("label", { class: "opt" }, h("input", { type: "checkbox", checked: state.wrap,
        onchange: (ev) => { state.wrap = ev.target.checked; rerender(); } }), h("span", null, "Wrap long text"), h("span")),
      state.view === "discover" ? [h("p", { class: "hint" }, "Columns"),
        Object.entries(OPTIONAL_COLS).map(([id, label]) => h("label", { class: "opt" },
          h("input", { type: "checkbox", checked: !state.hiddenCols.has(id),
            onchange: (ev) => { ev.target.checked ? state.hiddenCols.delete(id) : state.hiddenCols.add(id); rerender(); } }),
          h("span", null, label), h("span")))] : null,
      h("p", { class: "hint" }, "Columns that do not fit are hidden automatically (their details are in the side panel). Drag a column’s edge to resize it; double-click it to fit the content again."),
      h("div", { class: "menu-actions" },
        h("button", { class: "link", type: "button", disabled: !widthKeys.length && !(state.view === "discover" && state.hiddenCols.size) ? true : null,
          onclick: () => { widthKeys.forEach((k) => delete state.colWidths[k]); if (state.view === "discover") state.hiddenCols.clear(); rerender(); } },
        "Reset to default")),
    ));
}

function renderToolbar(keepOpen) {
  const box = $("#filters");
  box.replaceChildren();
  if (state.view === "discover") {
    const firmNames = new Map(state.roles.map((r) => [r.firm_id, r.firm_name]));
    box.append(...[
      filterMenu("countries", "Country", (r) => r.countries || [], countryName),
      filterMenu("sectors", "Sector", (r) => [r.sector], (v) => SECTORS[v] || v),
      filterMenu("firms", "Firm", (r) => [r.firm_id], (v) => firmNames.get(v) || v),
      filterMenu("seasons", "Season", (r) => [r.season], (v) => (v === "unknown" ? "Not stated" : SEASONS[v] || v)),
      filterMenu("years", "Year", (r) => [r.year ? String(r.year) : "none"], (v) => (v === "none" ? "Not stated" : v)),
      filterMenu("languages", "Language", (r) => r.languages_required || [], languageName,
        state.languageMode === "any"
          ? "Shows roles needing at least one ticked language. Roles where you lack one of them are flagged “Language gap” in Fit."
          : "Shows roles you fully qualify for: any role needing a language you have not ticked is hidden.",
        h("label", { class: "opt opt-mode" }, h("input", { type: "checkbox", checked: state.languageMode === "any",
          onchange: (ev) => { state.languageMode = ev.target.checked ? "any" : "all"; savePrefs(); renderView(); renderToolbar("languages"); } }),
        h("span", null, "Include partial matches"), h("span"))),
      filterMenu("eligibility", "Fit", (r) => [r.eligibility], (v) => OUTCOMES[v] || v,
        "Assessed in this browser against your Profile. Uncertain roles are never hidden unless you choose to."),
      radioMenu("deadline", "Deadline", { options: { any: "Any deadline", ...DEADLINES }, value: state.deadline, defaultValue: "any",
        set: (v) => { state.deadline = v; savePrefs(); renderView(); renderToolbar(); } }),
      toggle("Open only", state.openOnly, (v) => { state.openOnly = v; savePrefs(); renderView(); renderToolbar(); }),
      showMenu(),
      viewMenu(),
      discoverFiltersActive() ? h("button", {
        class: "btn ghost clear", type: "button",
        onclick: () => { clearDiscoverFilters(); savePrefs(); renderView(); renderToolbar(); },
      }, "Clear filters") : null,
    ].filter(Boolean));
  } else if (state.view === "firms") {
    const after = (k) => { renderView(); renderToolbar(k); };
    const emps = state.market.employers;
    box.append(firmMenu(),
      checkMenu("firm-sectors", "Sector", { sel: state.firmFilters.sectors, counts: optionCounts((e) => e.sectors || [], emps),
        nameOf: (v) => SECTORS[v] || v, after }),
      checkMenu("firm-countries", "Country", { sel: state.firmFilters.countries, counts: optionCounts((e) => e.countries || [], emps),
        nameOf: countryName, after }),
      viewMenu());
  } else if (state.view === "sources") {
    box.append(healthMenu(), viewMenu());
  } else if (state.view === "deadlines") {
    box.append(toggle("Tracked only", state.trackedOnly, (v) => { state.trackedOnly = v; savePrefs(); renderView(); }), viewMenu());
  } else if (state.view !== "profile") {
    box.append(viewMenu());
  }
  if (keepOpen) {
    const m = box.querySelector(`.menu[data-key="${CSS.escape(keepOpen)}"]`);
    if (m) m.open = true;
  }
}

// --- tables ---------------------------------------------------------------------------------------------------------
// Columns: [sortKey, label, class, default width, id]. The column with no width takes the remaining space; the
// others are fitted to their content after rendering (see autofit) unless the user has dragged them.
function header(cols) {
  return h("thead", null, h("tr", null, cols.map(([key, label, cls, width, id], i) => {
    const sortable = key && state.view === "discover";
    const sorted = sortable && state.sort.key === key;
    const col = id || key || (label ? label.toLowerCase() : `c${i}`);
    const fixed = cls?.includes("c-star");
    return h("th", {
      class: cls, scope: "col", style: width ? `width:${width}` : null,
      dataset: { col, ...(width ? {} : { flex: "1" }), ...(fixed ? { fixed: "1" } : {}) },
      "aria-sort": sorted ? (state.sort.dir === "asc" ? "ascending" : "descending") : null,
    }, fixed || !width ? null : h("span", { class: "col-resize", "aria-hidden": "true", title: "Drag to resize · double-click to fit",
      onpointerdown: startResize, ondblclick: (ev) => resetColumn(ev, col), onclick: (ev) => ev.stopPropagation() }),
    sortable ? h("button", {
      type: "button",
      onclick: () => {
        const first = key === "match" ? "desc" : "asc";  // best fit first
        state.sort = { key, dir: state.sort.key === key ? (state.sort.dir === "asc" ? "desc" : "asc") : first };
        savePrefs(); renderView();
      },
    }, label) : label || h("span", { class: "visually-hidden" }, "Shortlist"));
  })));
}

// --- column fitting and resizing ---------------------------------------------------------------------------------
// Each column is sized to its content (95th percentile of its cells, so one very long value does not take over),
// the flexible column (Role) gets the rest, and if that leaves it too narrow the others give way proportionally.
// Widths the user dragged are kept per view and table.
const tableKey = (table) => `${state.view}:${table.getAttribute("aria-label") || ""}`;
let measureCtx;
function longestWord(cells) {
  if (!cells.length) return 0;
  measureCtx ||= document.createElement("canvas").getContext("2d");
  const cs = getComputedStyle(cells[0]);
  measureCtx.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
  let max = 0;
  for (const td of cells) for (const w of td.textContent.split(/\s+/)) if (w.length > 3) max = Math.max(max, measureCtx.measureText(w).width);
  return Math.ceil(max);
}
const WRAPPABLE = "td.c-role, td.c-firm, td.col-loc, td.col-sector, td.c-loc";
// Content width of each cell, measured on a hidden max-content copy inside the cell, so it inherits the cell's
// fonts and does not depend on how a browser reports overflow (which differs between Chromium, Safari and Firefox).
function contentWidths(cells) {
  const probes = cells.map((cell) => {
    const probe = document.createElement("span");
    probe.className = "probe";
    probe.setAttribute("aria-hidden", "true");
    for (const n of cell.childNodes) if (!n.classList?.contains("col-resize")) probe.append(n.cloneNode(true));
    cell.append(probe);
    return probe;
  });
  const ws = probes.map((p) => Math.ceil(p.getBoundingClientRect().width));
  probes.forEach((p) => p.remove());
  return ws;
}
const hPad = (el) => { if (!el) return 0; const cs = getComputedStyle(el); return (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0); };

// Columns dropped first when a table does not fit, least useful first. Their values stay in the detail pane.
const DROP_ORDER = ["col-lang", "col-sector", "col-closes", "col-updated", "col-season", "col-what", "col-window",
  "col-status", "col-fit", "col-loc"];
function autofit(table) {
  const head = table.tHead?.rows[0];
  DROP_ORDER.forEach((c) => table.classList.remove("drop-" + c));
  if (!head || getComputedStyle(table.tHead).display === "none") return;
  const avail = Math.floor((table.parentElement || table).clientWidth);
  if (!avail) return;
  const ths = [...head.cells];
  const visible = ths.map((th) => getComputedStyle(th).display !== "none");
  let flex = ths.findIndex((th, i) => th.dataset.flex && visible[i]);
  if (flex < 0) flex = visible.lastIndexOf(true);
  const saved = state.colWidths[tableKey(table)] || {};
  const sample = [...table.tBodies[0].rows].filter((tr) => !tr.classList.contains("group-row")).slice(0, 250);
  // Text columns (firm, place, sector) may wrap, so they size to the 95th percentile and can give way down to their
  // longest word; short values (dates, days, fit, status) size to their longest value.
  const floor = ths.map(() => 0);
  const want = ths.map((th, i) => {
    if (!visible[i] || i === flex) return 0;
    if (th.dataset.fixed) return (floor[i] = parseFloat(th.style.width) || 44);
    const cells = sample.map((tr) => tr.cells[i]).filter(Boolean);
    const pad = hPad(cells[0]);
    const thW = contentWidths([th])[0] + hPad(th) + 2;
    const ws = contentWidths(cells).map((w) => w + pad + 2).sort((x, y) => x - y);
    const textual = cells.some((td) => td.matches(WRAPPABLE));
    const pick = (q) => (ws.length ? ws[Math.min(ws.length - 1, Math.floor(ws.length * q))] : 0);
    const auto = Math.min(Math.max(thW, textual ? pick(0.95) : pick(1)), 360);
    floor[i] = textual ? Math.min(auto, Math.max(thW, longestWord(cells) + pad + 2)) : auto;
    if (saved[th.dataset.col]) { floor[i] = Math.min(floor[i], saved[th.dataset.col]); return saved[th.dataset.col]; }
    return auto;
  });
  // The flexible column (Role) always keeps a usable width; the others give way, first those you did not size
  // yourself, then those you did, and as a last resort all of them proportionally.
  const minFlex = Math.round(Math.min(420, Math.max(200, avail * 0.3)));
  const total = () => want.reduce((x, y) => x + y, 0);
  const shrink = (pick) => {
    const over = total() + minFlex - avail;
    if (over <= 0) return;
    const free = want.map((w, i) => (pick(i) ? Math.max(0, w - floor[i]) : 0));
    const room = free.reduce((x, y) => x + y, 0);
    if (room) free.forEach((f, i) => { want[i] -= Math.ceil(f * Math.min(1, over / room)); });
  };
  const isSaved = (i) => !!saved[ths[i].dataset.col];
  shrink((i) => !isSaved(i) && !ths[i].dataset.fixed);
  shrink((i) => isSaved(i));
  // Still too wide: rather than cutting values, drop whole columns in DROP_ORDER until the rest fit.
  for (const cls of DROP_ORDER) {
    if (total() + minFlex <= avail) break;
    const i = ths.findIndex((th, j) => visible[j] && j !== flex && th.classList.contains(cls));
    if (i < 0) continue;
    table.classList.add("drop-" + cls);
    visible[i] = false;
    want[i] = 0;
  }
  const flexW = Math.max(minFlex, avail - total());
  ths.forEach((th, i) => { if (visible[i]) th.style.width = `${i === flex ? flexW : want[i]}px`; });
  table.classList.toggle("wrap", state.wrap);
}
function fitTables() { document.querySelectorAll("#view .grid:not(.skeleton)").forEach(autofit); }

let resizing = null;
function startResize(ev) {
  ev.preventDefault();
  ev.stopPropagation();
  const th = ev.target.closest("th");
  resizing = { th, table: th.closest("table"), x: ev.clientX, w: th.offsetWidth };
  ev.target.setPointerCapture(ev.pointerId);
  ev.target.addEventListener("pointermove", onResize);
  ev.target.addEventListener("pointerup", endResize, { once: true });
  document.body.classList.add("is-resizing");
}
function onResize(ev) {
  if (!resizing) return;
  resizing.th.style.width = `${Math.max(40, Math.round(resizing.w + ev.clientX - resizing.x))}px`;
}
function endResize(ev) {
  ev.target.removeEventListener("pointermove", onResize);
  document.body.classList.remove("is-resizing");
  if (!resizing) return;
  const key = tableKey(resizing.table);
  state.colWidths[key] = { ...(state.colWidths[key] || {}), [resizing.th.dataset.col]: resizing.th.offsetWidth };
  resizing = null;
  savePrefs();
  fitTables();
}
function resetColumn(ev, col) {
  ev.stopPropagation();
  const key = tableKey(ev.target.closest("table"));
  if (state.colWidths[key]) delete state.colWidths[key][col];
  savePrefs();
  fitTables();
}

function roleRow(r, cells) {
  const tr = h("tr", {
    dataset: { id: r.id },
    class: [r.status !== "open" ? "closed" : "", r.id === state.focusId ? "focused" : "", r.id === state.detailId ? "selected" : ""].join(" ").trim() || null,
    onclick: () => { focusRow(r.id); openDetail(r.id); },
  }, cells);
  return tr;
}

// One cell per column (not a colspan) so headings survive columns being hidden at narrow widths.
function groupRow(title, count, cols) {
  return h("tr", { class: "group-row" }, cols.map(([, , cls], i) => h("td", { class: i === 0 ? "group-cell" : cls },
    i === 0 ? h("h2", null, title, h("span", { class: "num" }, String(count))) : null)));
}

function emptyState(title, body, action) {
  return h("div", { class: "state" }, h("p", { class: "state-title" }, title), h("p", null, body), action || null);
}

// Discover ---------------------------------------------------------------------------------------------------------
function discoverRows() {
  const filters = {
    countries: [...state.filters.countries], sectors: [...state.filters.sectors],
    seasons: [...state.filters.seasons], languages: [...state.filters.languages], openOnly: state.openOnly,
    eligibility: [...state.filters.eligibility], confirmedOnly: state.confirmedOnly, showHidden: state.showHidden,
    firms: [...state.filters.firms], years: [...state.filters.years], deadline: state.deadline, newOnly: state.newOnly,
    untrackedOnly: state.untrackedOnly, tracked: (id) => !!tracker.get(id), today: state.today, languageMode: state.languageMode,
  };
  const rows = searchRoles(visibleRoles(state.roles, filters), state.query);
  return sortRoles(rows, state.sort.key, state.sort.dir);
}

function renderDiscover(view) {
  const rows = discoverRows();
  const open = rows.filter((r) => r.status === "open").length;
  const firms = new Set(rows.map((r) => r.firm_id)).size;
  const closing = rows.filter((r) => r.status === "open" && urgency(daysUntil(r.closes, state.today)) === "urgent").length;
  setMeta([[open, plural(open, "open role").replace(/^\d+ /, "")], [firms, firms === 1 ? "firm" : "firms"],
    closing ? [closing, "closing within 7 days"] : null]);
  state.rowIds = rows.map((r) => r.id);

  if (!state.roles.length) {
    view.append(emptyState("No postings yet",
      "The tracker reads every firm's job board on its own schedule. Roles appear here as soon as they are found; see Sources for what is being checked."));
    return;
  }
  if (!rows.length) {
    view.append(emptyState("Nothing matches", state.query ? `No role matches “${state.query}” with the current filters.` : "No role matches the current filters.",
      h("button", { class: "btn", type: "button", onclick: resetDiscover }, "Clear search and filters")));
    return;
  }
  // [sortKey, label, class, width, id, cell]. Optional columns can be switched off in the View menu.
  const all = [
    [null, "", "c-star", "44px", "star", (r) => h("td", { class: "c-star" }, starButton(r))],
    ["firm_name", "Firm", null, "16%", "firm", (r) => h("td", { class: "c-firm firm", title: r.firm_name }, r.firm_name)],
    ["title", "Role", null, null, "role", (r) => h("td", { class: "c-role", title: r.title }, h("span", { class: "role-title" }, r.title),
      isNew(r) && r.status === "open" ? h("span", { class: "new" }, "new") : null, lifecycleTag(r))],
    ["city", "Location", "col-loc", "13%", "loc", (r) => h("td", { class: "c-loc col-loc sub", title: placeOf(r) }, placeOf(r))],
    ["season", "Intake", "col-season", "108px", "season", (r) => h("td", { class: "c-extra col-season sub" }, `${SEASONS[r.season] || "—"}${r.year ? " " + r.year : ""}`)],
    ["sector", "Sector", "col-sector", "128px", "sector", (r) => h("td", { class: "c-extra col-sector sub", title: SECTORS[r.sector] || r.sector }, SECTORS[r.sector] || r.sector)],
    [null, "Lang.", "col-lang", "60px", "lang", (r) => h("td", { class: "c-extra col-lang sub mono", title: (r.languages_required || []).map(languageName).join(", ") }, langCodes(r))],
    ["match", "Fit", "col-fit", "96px", "fit", (r) => h("td", { class: "c-extra col-fit" }, eligTag(r))],
    ["closes", "Closes", "r col-closes", "72px", "closes", (r) => h("td", { class: "c-extra col-closes r date" }, r.closes ? shortDate(r.closes) : "")],
    [null, "Left", "r", "72px", "left", (r) => h("td", { class: "c-days r" }, daysCell(r.closes, { closed: r.status !== "open" }))],
    [null, "Status", "col-status", "136px", "status", (r) => { const e = tracker.get(r.id); return h("td", { class: "c-extra col-status" }, e ? stageView(e.stage) : ""); }],
  ];
  const shown = all.filter((c) => !state.hiddenCols.has(c[4]));
  const cols = shown.map((c) => c.slice(0, 5));
  const tbody = h("tbody");
  const frag = document.createDocumentFragment();
  for (const r of rows) frag.append(roleRow(r, shown.map((c) => c[5](r))));
  tbody.append(frag);
  view.append(h("table", { class: "grid", "aria-label": "Internship postings" }, header(cols), tbody));
}

function resetDiscover() {
  state.query = "";
  $("#search").value = "";
  Object.values(state.filters).forEach((s) => s.clear());
  savePrefs(); renderToolbar(); renderView();
}

// Pipeline ---------------------------------------------------------------------------------------------------------
function renderPipeline(view) {
  const all = pipelineEntries().filter(({ role }) => searchRoles([role], state.query).length);
  const active = all.filter((x) => !stageById(x.entry.stage)?.closed);
  const interviewing = all.filter((x) => ["interview", "final"].includes(x.entry.stage)).length;
  const offers = all.filter((x) => x.entry.stage === "offer").length;
  setMeta([[active.length, "active"], interviewing ? [interviewing, "in interviews"] : null, offers ? [offers, offers === 1 ? "offer" : "offers"] : null]);

  if (!tracker.count()) {
    state.rowIds = [];
    view.append(emptyState("Your pipeline is empty",
      "Shortlist roles from Discover with the star or the s key. Move them through stages here as your applications progress — everything stays in this browser.",
      h("a", { class: "btn primary", href: "#discover" }, "Browse roles")));
    return;
  }
  if (!all.length) {
    state.rowIds = [];
    view.append(emptyState("Nothing matches", `No tracked role matches “${state.query}”.`));
    return;
  }
  const order = STAGES.map((s) => s.id);
  // Within a stage: the soonest next step first, then the soonest closing date.
  const key = (x) => x.entry.next_date || x.role.closes || "9999";
  const groups = order.map((id) => [id, all.filter((x) => x.entry.stage === id).sort((a, b) => key(a).localeCompare(key(b)))])
    .filter(([, xs]) => xs.length);
  const cols = [
    [null, "", "c-star", "44px"], [null, "Role", null, "46%"], [null, "Next step", null, null], [null, "Closes", "r col-closes", "84px"], [null, "Updated", "r col-updated", "84px"],
  ];
  const tbody = h("tbody");
  state.rowIds = [];
  for (const [id, xs] of groups) {
    tbody.append(groupRow(stageById(id).label, xs.length, cols));
    for (const { entry, role } of xs) {
      state.rowIds.push(role.id);
      const nd = entry.next_date ? daysUntil(entry.next_date, state.today) : null;
      tbody.append(roleRow(role, [
        h("td", { class: "c-star" }, starButton(role)),
        h("td", { class: "c-firm stack", title: `${role.firm_name} — ${role.title}` },
          h("span", { class: "firm" }, role.firm_name),
          h("span", { class: "role-title" }, role.title,
            role.status === "removed" ? h("span", { class: "new", title: "This ad is no longer listed on the firm's site" }, "ad removed") : null)),
        h("td", { class: "c-loc" }, entry.next_step || entry.next_date
          ? [entry.next_date ? h("span", { class: `days ${urgency(nd) || ""}` }, shortDate(entry.next_date) + "  ") : null,
            h("span", { class: "sub" }, entry.next_step || "")]
          : h("span", { class: "sub empty" }, "—")),
        h("td", { class: "c-days r col-closes" }, stageById(entry.stage)?.rank >= 1 || stageById(entry.stage)?.closed
          ? h("span", { class: "date" }, role.closes ? shortDate(role.closes) : "")
          : daysCell(role.closes, { closed: role.status !== "open" })),
        h("td", { class: "c-extra col-updated r date" }, shortDate(entry.updated)),
      ]));
    }
  }
  view.append(h("table", { class: "grid", "aria-label": "Your applications" }, header(cols), tbody));
}

// Deadlines --------------------------------------------------------------------------------------------------------
function deadlineEvents() {
  const events = [];
  const tracked = tracker.all();
  for (const r of state.roles) {
    if (r.status !== "open" || !r.closes) continue;
    if (state.trackedOnly && !tracked[r.id]) continue;
    const st = tracked[r.id] && stageById(tracked[r.id].stage);
    if (st && (st.rank >= 1 || st.closed)) continue; // already applied: the closing date no longer matters
    events.push({ id: r.id, date: r.closes, days: daysUntil(r.closes, state.today), kind: "Closes", role: r });
  }
  for (const e of Object.values(tracked)) {
    if (!e.next_date || stageById(e.stage)?.closed) continue;
    const role = resolve(e.id);
    if (role) events.push({ id: e.id, date: e.next_date, days: daysUntil(e.next_date, state.today), kind: e.next_step || "Next step", role, mine: true });
  }
  for (const r of Object.values(crm.all())) {
    if (!r.next_date || r.stage === "closed") continue;
    events.push({ id: "crm:" + r.employer_id, date: r.next_date, days: daysUntil(r.next_date, state.today), kind: r.next_step || "Follow up",
      role: { firm_name: r.employer_name, title: "Outreach", city: null, countries: [] }, mine: true, link: "#outreach/" + r.employer_id });
  }
  if (!state.trackedOnly) {
    for (const p of state.programs.values()) {
      const st = programStatus(p, state.today);
      const w = st.window;
      if (!w || !["confirmed", "estimated"].includes(st.key)) continue;
      const date = st.key === "confirmed" ? w.opens_on : w.opens_earliest;
      const firm = state.employers.get(p.employer_id)?.name || p.employer_id;
      events.push({ id: "program:" + p.id, date, days: daysUntil(date, state.today), link: "#programs/" + p.id,
        kind: st.key === "confirmed" ? "Applications open" : "May open (estimate)", role: { firm_name: firm, title: p.name, countries: p.countries } });
    }
  }
  return events.filter((ev) => ev.days >= -7).sort((a, b) => a.date.localeCompare(b.date) || (b.mine ? 1 : 0) - (a.mine ? 1 : 0));
}

function renderDeadlines(view) {
  const events = deadlineEvents().filter((ev) => searchRoles([ev.role], state.query).length);
  const week = events.filter((ev) => ev.days >= 0 && ev.days <= 6).length;
  const mine = events.filter((ev) => ev.mine && ev.days >= 0).length;
  setMeta([[week, "this week"], mine ? [mine, mine === 1 ? "of your own next step" : "of your own next steps"] : null]);
  if (!events.length) {
    state.rowIds = [];
    view.append(emptyState("No upcoming dates",
      state.trackedOnly ? "None of your tracked roles has a closing date or a next step with a date. Add one from a role's detail panel."
        : "No open role states a closing date yet. Many firms recruit on a rolling basis — apply early."));
    return;
  }
  const cols = [[null, "", "c-star", "44px"], [null, "Date", null, "96px"], [null, "In", "r", "72px"], [null, "What", "col-what", "20%"],
    [null, "Firm", null, "16%"], [null, "Role", null, null], [null, "Status", "col-status", "136px"]];
  const tbody = h("tbody");
  state.rowIds = [];
  let bucket = null;
  const buckets = events.reduce((m, ev) => { const b = ev.days < 0 ? "Just passed" : deadlineBucket(ev.days); m[b] = (m[b] || 0) + 1; return m; }, {});
  for (const ev of events) {
    const b = ev.days < 0 ? "Just passed" : deadlineBucket(ev.days);
    if (b !== bucket) { bucket = b; tbody.append(groupRow(b, buckets[b], cols)); }
    if (ev.link) {
      tbody.append(h("tr", { onclick: () => { location.hash = ev.link; } }, [
        h("td", { class: "c-star" }, ""),
        h("td", { class: "c-extra date" }, new Date(ev.date + "T00:00:00Z").toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" })),
        h("td", { class: "c-days r" }, daysCell(ev.date)),
        h("td", { class: "c-extra col-what" + (ev.mine ? "" : " sub") }, ev.mine ? h("strong", { class: "w-kind" }, ev.kind) : ev.kind),
        h("td", { class: "c-firm firm" }, ev.role.firm_name),
        h("td", { class: "c-role", title: ev.role.title }, h("a", { class: "role-title", href: ev.link }, ev.role.title)),
        h("td", { class: "c-loc col-status sub" }, (ev.role.countries || []).join(" · ")),
      ]));
      continue;
    }
    if (!state.rowIds.includes(ev.id)) state.rowIds.push(ev.id);
    const e = tracker.get(ev.id);
    tbody.append(roleRow(ev.role, [
      h("td", { class: "c-star" }, starButton(ev.role)),
      h("td", { class: "c-extra date" }, new Date(ev.date + "T00:00:00Z").toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" })),
      h("td", { class: "c-days r" }, daysCell(ev.date)),
      h("td", { class: "c-extra col-what" + (ev.mine ? "" : " sub") }, ev.mine ? h("strong", { style: "font-weight:500" }, ev.kind) : ev.kind),
      h("td", { class: "c-firm firm" }, ev.role.firm_name),
      h("td", { class: "c-role", title: ev.role.title }, h("span", { class: "role-title" }, ev.role.title)),
      h("td", { class: "c-loc col-status" }, e ? stageView(e.stage) : h("span", { class: "sub" }, placeOf(ev.role))),
    ]));
  }
  view.append(h("table", { class: "grid", "aria-label": "Upcoming dates" }, header(cols), tbody));
}

// Programs -----------------------------------------------------------------------------------------------------------
const WINDOW_KIND = { confirmed: "Confirmed", observed: "Observed", estimated: "Estimate", third_party: "Unverified claim" };
const PROGRAM_GROUPS = [["open", "Open now"], ["confirmed", "Opening date announced"], ["estimated", "Expected — estimated from earlier years"],
  ["history", "Closed — kept for their history"]];

function windowText(w) {
  if (!w) return "—";
  if (w.kind === "estimated") return `${shortDate(w.opens_earliest)} – ${shortDate(w.opens_latest)}`;
  return [w.opens_on ? shortDate(w.opens_on) : "?", w.closes_on ? shortDate(w.closes_on) : ""].filter(Boolean).join(" → ");
}

function renderPrograms(view) {
  const q = state.query.toLowerCase();
  const all = [...state.programs.values()].map((p) => ({ p, st: programStatus(p, state.today), e: state.employers.get(p.employer_id) }))
    .filter(({ p, e }) => !q || `${p.name} ${e?.name || ""}`.toLowerCase().includes(q));
  const upcoming = all.filter((x) => ["confirmed", "estimated"].includes(x.st.key)).length;
  setMeta([[all.length, all.length === 1 ? "program" : "programs"], [all.filter((x) => x.st.key === "open").length, "open now"],
    upcoming ? [upcoming, "expected or announced"] : null]);
  state.rowIds = [];
  if (!state.programs.size) {
    view.append(emptyState("No recurring programs yet",
      "Programs appear when the tracker links a vacancy to a recurring scheme (a summer internship, an insight week) or reads one from an employer's student page. Each year's dates are kept so the next opening can be anticipated."));
    return;
  }
  if (!all.length) { view.append(emptyState("Nothing matches", `No program matches “${state.query}”.`)); return; }
  const cols = [[null, "Firm", "c-firm-col", "18%"], [null, "Program", null, null], [null, "Window", "col-window", "168px"],
    [null, "Intake", "r col-season", "72px"], [null, "Evidence", "col-status", "120px"]];
  const tbody = h("tbody");
  for (const [key, title] of PROGRAM_GROUPS) {
    const xs = all.filter((x) => x.st.key === key).sort((a, b) => (windowText(a.st.window) > windowText(b.st.window) ? 1 : -1));
    if (!xs.length) continue;
    tbody.append(groupRow(title, xs.length, cols));
    for (const { p, st, e } of xs) {
      state.rowIds.push(p.id);
      tbody.append(plainRow(p.id, [
        h("td", { class: "c-firm firm" }, e?.name || p.employer_id),
        h("td", { class: "c-role", title: p.name }, h("span", { class: "role-title" }, p.name),
          p.live ? h("span", { class: "new" }, `${p.live} open`) : null),
        h("td", { class: "c-days col-window date" }, windowText(st.window)),
        h("td", { class: "c-extra r col-season date" }, st.year ? String(st.year) : "—"),
        h("td", { class: "c-loc col-status sub" }, st.window ? WINDOW_KIND[st.window.kind] : "—"),
      ]));
    }
  }
  view.append(h("table", { class: "grid", "aria-label": "Recurring programs" }, header(cols), tbody));
}

function plainRow(id, cells) {
  return h("tr", {
    dataset: { id },
    class: [id === state.focusId ? "focused" : "", id === state.detailId ? "selected" : ""].join(" ").trim() || null,
    onclick: () => { focusRow(id); openDetail(id); },
  }, cells);
}

function detailHead(over, title, cta) {
  return h("header", { class: "detail-head" },
    h("div", { class: "detail-top" }, h("span", { class: "detail-firm" }, over),
      h("button", { class: "btn ghost detail-close", type: "button", "aria-label": "Close details", onclick: closeDetail }, "Close ", h("kbd", null, "esc"))),
    h("h2", null, title),
    cta?.length ? h("div", { class: "detail-cta" }, cta) : null);
}

const extLink = (url, label, primary = false) => safeUrl(url)
  ? h("a", { class: `btn${primary ? " primary" : ""}`, href: safeUrl(url), target: "_blank", rel: "noopener noreferrer" }, label) : null;

function renderProgramDetail(pane, id) {
  const p = state.programs.get(id);
  if (!p) { closeDetail(); return; }
  const e = state.employers.get(p.employer_id);
  const st = programStatus(p, state.today);
  const opps = (p.opportunity_ids || []).map((x) => state.byId.get(x)).filter(Boolean);
  fill(pane, 
    detailHead(e?.name || p.employer_id, p.name, [extLink(p.official_url, "Official page ↗", true),
      e ? h("a", { class: "btn", href: "#firms/" + e.id }, "Firm profile") : null].filter(Boolean)),
    h("dl", { class: "facts" }, [["Status", st.label], ["Format", SEASONS[p.type] || p.type], ["Division", p.division || "—"],
      ["Countries", (p.countries || []).map(countryName).join(", ") || "Not stated"], ["Who can apply", p.eligibility || "Not stated"],
      ["Found via", p.discovered_via === "program_page" ? "the employer's student page" : "its vacancies"]]
      .flatMap(([k, v]) => [h("dt", null, k), h("dd", null, v)])),
    h("section", { class: "detail-section", "aria-label": "Recruitment windows" },
      h("h3", { class: "label" }, "Recruitment windows"),
      (p.windows || []).length ? h("ul", { class: "reasons" }, p.windows.map((w) => h("li", null,
        h("span", { class: "mark num" }, String(w.intake_year)),
        h("span", null, h("strong", { class: "w-kind" }, WINDOW_KIND[w.kind]), " · ", windowText(w),
          h("span", { class: "sub quote" }, w.evidence),
          safeUrl(w.source_url) ? h("a", { class: "sub", href: safeUrl(w.source_url), target: "_blank", rel: "noopener noreferrer" }, "source ↗") : null))))
        : h("p", { class: "sub" }, "No dates recorded yet."),
      h("p", { class: "sub" }, "Confirmed dates are printed on the employer's page. Observed dates come from the tracker's own record of that year's vacancy. Estimates are ranges, never announcements; the firm's sources are checked more often as an estimate approaches.")),
    opps.length ? h("section", { class: "detail-section", "aria-label": "Vacancies" }, h("h3", { class: "label" }, "Vacancies"),
      h("ul", { class: "history" }, opps.map((r) => h("li", null, h("span", { class: "date" }, r.year || shortDate(r.first_seen)),
        h("a", { href: "#discover/" + r.id }, r.title), r.status === "open" ? "" : " — closed")))) : null,
  );
}

// Firms --------------------------------------------------------------------------------------------------------------
const FIRM_FILTERS = { all: "All firms", live: "Hiring now", prospects: "Prospects", uncovered: "No working source" };

function firmHealth(e) {
  const srcs = (e.source_ids || []).map((id) => sourceById(id)).filter(Boolean).filter((s) => s.enabled);
  if (e.access_note) return { key: "blocked", text: "check by hand" };
  if (!srcs.length) return { key: "none", text: "no source" };
  const ok = srcs.filter((s) => s.health === "ok").length;
  return { key: ok ? "ok" : "bad", text: `${ok}/${srcs.length} working` };
}
let sourceIndex = null;
function sourceById(id) {
  if (!sourceIndex) sourceIndex = new Map((state.ops?.sources || []).map((s) => [s.id, s]));
  return sourceIndex.get(id);
}

function firmMenu() {
  return h("details", { class: `menu${state.firmFilter !== "all" ? " active" : ""}` },
    h("summary", null, FIRM_FILTERS[state.firmFilter]),
    h("div", { class: "menu-pop", role: "radiogroup", "aria-label": "Show" }, Object.entries(FIRM_FILTERS).map(([k, label]) =>
      h("label", { class: "opt" }, h("input", { type: "radio", name: "firm-filter", checked: state.firmFilter === k,
        onchange: () => { state.firmFilter = k; savePrefs(); renderToolbar(); renderView(); } }), h("span", null, label), h("span")))));
}

function renderFirms(view) {
  const q = state.query.toLowerCase();
  let rows = state.market.employers.filter((e) => !q || `${e.name} ${(e.aliases || []).join(" ")} ${(e.countries || []).join(" ")}`.toLowerCase().includes(q));
  if (state.firmFilter === "live") rows = rows.filter((e) => e.open > 0);
  if (state.firmFilter === "prospects") rows = rows.filter((e) => state.prospects.has(e.id));
  if (state.firmFilter === "uncovered") rows = rows.filter((e) => firmHealth(e).key !== "ok");
  const { sectors: fs, countries: fc } = state.firmFilters;
  if (fs.size) rows = rows.filter((e) => (e.sectors || []).some((x) => fs.has(x)));
  if (fc.size) rows = rows.filter((e) => (e.countries || []).some((x) => fc.has(x)));
  rows.sort((a, b) => b.open - a.open || a.name.localeCompare(b.name));
  const discovered = state.market.employers.filter((e) => e.discovered_via !== "seed").length;
  setMeta([[rows.length, rows.length === 1 ? "firm" : "firms"], [state.market.employers.filter((e) => e.open > 0).length, "hiring now"],
    discovered ? [discovered, "found by discovery"] : null]);
  state.rowIds = rows.map((e) => e.id);
  if (!state.market.employers.length) { view.append(emptyState("No firms yet", "Firms appear after the first run reads the firm list.")); return; }
  if (!rows.length) { view.append(emptyState("Nothing matches", "No firm matches the search and filter.")); return; }
  const cols = [[null, "Firm", "c-firm-col", "22%"], [null, "Sectors", null, null], [null, "Countries", "col-loc", "14%"],
    [null, "Sources", "col-sector", "120px"], [null, "Open", "r", "64px"], [null, "Outreach", "col-status", "136px"]];
  const tbody = h("tbody");
  for (const e of rows) {
    const hl = firmHealth(e);
    const rel = crm.get(e.id);
    tbody.append(plainRow(e.id, [
      h("td", { class: "c-firm firm", title: e.name }, e.name, e.discovered_via !== "seed" && daysUntil(e.created_at?.slice(0, 10), state.today) >= -13
        ? h("span", { class: "new" }, "new") : null),
      h("td", { class: "c-role sub", title: e.sectors.map((x) => SECTORS[x] || x).join(", ") }, e.sectors.map((x) => SECTORS[x] || x).join(", ")),
      h("td", { class: "c-loc col-loc sub" }, (e.countries || []).join(" · ")),
      h("td", { class: `c-extra col-sector health-${hl.key}` }, hl.text),
      h("td", { class: "c-days r num" }, e.open ? String(e.open) : h("span", { class: "sub" }, "—")),
      h("td", { class: "c-extra col-status sub" }, rel ? RELATIONSHIP_STAGES.find((x) => x.id === rel.stage)?.label
        : state.prospects.has(e.id) ? `prospect · ${state.prospects.get(e.id).score}` : ""),
    ]));
  }
  view.append(h("table", { class: "grid", "aria-label": "Firms" }, header(cols), tbody));
}

function renderFirmDetail(pane, id) {
  const e = state.employers.get(id);
  if (!e) { closeDetail(); return; }
  const prospect = state.prospects.get(id);
  const rel = crm.get(id);
  const roles = state.roles.filter((r) => r.firm_id === id && !r.excluded_reason);
  const progs = [...state.programs.values()].filter((p) => p.employer_id === id);
  const srcs = (e.source_ids || []).map(sourceById).filter(Boolean);
  fill(pane, 
    detailHead((e.countries || []).map(countryName).join(" · ") || "Firm", e.name, [extLink(e.website, "Website ↗", true),
      rel ? null : h("button", { class: "btn", type: "button", onclick: () => { crm.start(e); toast(`${e.name} added to Outreach`); renderDetail(); } }, "Track in Outreach")].filter(Boolean)),
    rel ? relationshipSection(e, rel, prospect) : null,
    h("dl", { class: "facts" }, [["Sectors", e.sectors.map((x) => SECTORS[x] || x).join(", ") || "—"],
      ["Head office", e.hq_country ? countryName(e.hq_country) : "—"], ["Size", e.size_hint || "Not known"],
      ["Also known as", (e.aliases || []).join(", ") || "—"],
      ["Tracked since", `${formatDate(e.created_at?.slice(0, 10))} · ${e.discovered_via}`],
      ...(e.discovery_evidence && e.discovered_via !== "seed" ? [["Why it was added", e.discovery_evidence]] : []),
      ...(e.access_note ? [["Access", e.access_note]] : [])].flatMap(([k, v]) => [h("dt", null, k), h("dd", null, v)])),
    h("section", { class: "detail-section", "aria-label": "Openings" }, h("h3", { class: "label" }, `Openings · ${roles.filter((r) => r.status === "open").length} open`),
      roles.length ? h("ul", { class: "history" }, roles.slice(0, 20).map((r) => h("li", null, h("span", { class: "date" }, shortDate(r.first_seen)),
        h("a", { href: "#discover/" + r.id }, r.title), r.status === "open" ? "" : " — closed"))) :
        h("p", { class: "sub" }, "No internship recorded at this firm. That is not proof it never takes interns; see Prospect below.")),
    progs.length ? h("section", { class: "detail-section" }, h("h3", { class: "label" }, "Recurring programs"),
      h("ul", { class: "history" }, progs.map((p) => h("li", null, h("a", { href: "#programs/" + p.id }, p.name), ` — ${programStatus(p, state.today).label}`)))) : null,
    h("section", { class: "detail-section", "aria-label": "Recruitment sources" }, h("h3", { class: "label" }, "Recruitment sources"),
      srcs.length ? h("ul", { class: "history" }, srcs.map((s) => h("li", null, h("span", { class: `date health-${s.health === "ok" ? "ok" : "bad"}` }, HEALTH[s.health]),
        h("a", { href: "#sources/" + encodeURIComponent(s.id) }, sourceLabel(s.id)), ` · ${s.category.replaceAll("_", " ")}`)))
        : h("p", { class: "sub" }, e.access_note ? e.access_note : "No public recruitment source found yet. Discovery keeps looking.")),
    prospect ? prospectSection(prospect) : null,
  );
}

function prospectSection(p) {
  return h("section", { class: "detail-section", "aria-label": "Prospect" },
    h("h3", { class: "label" }, `Prospect · score ${p.score}`),
    h("p", { class: "sub" }, "Not an advertised vacancy: a firm that may be worth approaching directly."),
    h("ul", { class: "reasons" }, (p.reasons || []).map((r) => h("li", null, h("span", { class: "mark num" }, `+${r.points}`), r.detail))),
    p.summary ? h("p", null, p.summary, h("span", { class: "sub quote" }, `AI summary of the firm's own pages, ${shortDate(p.researched_at)}`)) : null,
    p.application_channel ? h("p", null, h("span", { class: "label" }, "Stated channel "), p.application_channel) : null,
    (p.intel || []).length ? h("ul", { class: "history" }, p.intel.map((n) => h("li", null, h("span", { class: "date" }, n.kind),
      n.text, " ", safeUrl(n.source_url) ? h("a", { class: "sub", href: safeUrl(n.source_url), target: "_blank", rel: "noopener noreferrer", title: `“${n.evidence}”` }, "source ↗") : null)))
      : h("p", { class: "sub" }, p.researched_at ? "Research found nothing it could support with the firm's own pages."
        : "Not researched yet. Add the firm id to firms/research.yaml to have it researched on the next run."),
  );
}

// Outreach: relationship workflow, all in this browser ----------------------------------------------------------------
function renderOutreach(view) {
  const q = state.query.toLowerCase();
  const rels = Object.values(crm.all()).filter((r) => !q || r.employer_name.toLowerCase().includes(q));
  const suggested = state.market.prospects.filter((p) => !crm.get(p.employer_id) && p.status !== "dismissed")
    .map((p) => ({ p, e: state.employers.get(p.employer_id) })).filter(({ e }) => e && (!q || e.name.toLowerCase().includes(q))).slice(0, 25);
  const due = rels.filter((r) => r.next_date && r.next_date <= state.today && r.stage !== "closed").length;
  setMeta([[rels.filter((r) => r.stage !== "closed").length, "active"], due ? [due, "follow-ups due"] : null, [suggested.length, "suggested"]]);
  state.rowIds = [];
  if (!rels.length && !suggested.length) {
    view.append(emptyState("No outreach yet", "Prospects appear when the tracker finds firms worth approaching without an advertised internship. You can also start from any firm's profile."));
    return;
  }
  const cols = [[null, "Firm", "c-firm-col", "22%"], [null, "Status / why", null, null], [null, "Next step", "col-what", "24%"],
    [null, "Contacts", "r col-sector", "80px"], [null, "Updated", "r col-updated", "84px"]];
  const tbody = h("tbody");
  for (const stage of RELATIONSHIP_STAGES) {
    const xs = rels.filter((r) => r.stage === stage.id).sort((a, b) => (a.next_date || "9999").localeCompare(b.next_date || "9999"));
    if (!xs.length) continue;
    tbody.append(groupRow(stage.label, xs.length, cols));
    for (const r of xs) {
      state.rowIds.push(r.employer_id);
      const nd = r.next_date ? daysUntil(r.next_date, state.today) : null;
      tbody.append(plainRow(r.employer_id, [
        h("td", { class: "c-firm firm" }, r.employer_name),
        h("td", { class: "c-role sub" }, r.notes ? r.notes.split("\n")[0] : (r.interactions[0]?.summary || "")),
        h("td", { class: "c-loc col-what" }, r.next_date || r.next_step ? [r.next_date ? h("span", { class: `days ${urgency(nd) || ""}` }, shortDate(r.next_date) + "  ") : null,
          h("span", { class: "sub" }, r.next_step || "")] : h("span", { class: "sub empty" }, "—")),
        h("td", { class: "c-extra r col-sector num" }, String(r.contacts.length)),
        h("td", { class: "c-extra r col-updated date" }, shortDate(r.updated)),
      ]));
    }
  }
  if (suggested.length) {
    tbody.append(groupRow("Suggested by the tracker", suggested.length, cols));
    for (const { p, e } of suggested) {
      state.rowIds.push(e.id);
      tbody.append(plainRow(e.id, [
        h("td", { class: "c-firm firm" }, e.name),
        h("td", { class: "c-role sub", title: p.reasons.map((x) => x.detail).join("; ") }, p.reasons.map((x) => x.detail).slice(0, 2).join("; ")),
        h("td", { class: "c-loc col-what sub" }, p.researched_at ? "researched" : "—"),
        h("td", { class: "c-extra r col-sector num" }, String(p.score)),
        h("td", { class: "c-extra r col-updated" }, ""),
      ]));
    }
  }
  view.append(h("table", { class: "grid", "aria-label": "Outreach" }, header(cols), tbody));
}

function relationshipSection(e, rel, prospect) {
  const section = h("section", { class: "detail-section tracking", "aria-label": "Your outreach" });
  const timers = {};
  const save = (field) => (ev) => { clearTimeout(timers[field]); const v = ev.target.value; timers[field] = setTimeout(() => crm.update(e.id, { [field]: v }), 250); };
  const draftBox = h("div", { class: "draft" });
  const showDraft = (kind, existing) => {
    const d = existing || compose(kind, { profile: profile.get(), employer: e, intel: prospect?.intel || [], contact: rel.contacts[0] || null,
      channel: prospect?.application_channel });
    const text = Object.assign(h("textarea", { "aria-label": "Draft text" }), { value: d.text });
    const subject = h("input", { type: "text", value: d.subject || "", "aria-label": "Subject" });
    const current = () => ({ ...d, subject: subject.value, text: text.value });
    draftBox.replaceChildren(
      h("label", { class: "field" }, h("span", { class: "label" }, `${DRAFT_KINDS[d.kind] || "Draft"} — subject`), subject),
      h("label", { class: "field" }, h("span", { class: "label" }, "Text — edit before sending"), text),
      h("div", { class: "stage-actions" },
        h("button", { class: "btn", type: "button", onclick: () => { crm.saveDraft(e.id, current()); toast("Draft saved"); } }, "Save draft"),
        h("button", { class: "btn ghost", type: "button", onclick: async () => {
          try { await navigator.clipboard.writeText(`${subject.value}\n\n${text.value}`); toast("Copied"); } catch { toast("Copy failed — select the text instead"); } } }, "Copy"),
        h("a", { class: "btn ghost", href: mailto(current()), title: "Opens your own mail app with this draft; nothing is sent from here" }, "Open in mail app"),
        h("button", { class: "btn ghost", type: "button", onclick: () => {
          if (!confirm(`Record that you sent this ${DRAFT_KINDS[d.kind]?.toLowerCase() || "message"} to ${e.name}?`)) return;
          crm.saveDraft(e.id, { ...current(), status: "sent" });
          crm.log(e.id, { kind: "message", summary: `Sent: ${subject.value || DRAFT_KINDS[d.kind]}` });
          if (["research", "to_contact"].includes(rel.stage)) crm.update(e.id, { stage: d.kind === "speculative" ? "applied" : "contacted" });
          toast("Recorded as sent");
        } }, "Mark as sent")));
  };
  section.append(
    h("h3", { class: "label" }, "Your outreach"),
    h("div", { class: "stage-actions" }, RELATIONSHIP_STAGES.map((st) => h("button", {
      class: `btn${rel.stage === st.id ? "" : " ghost"}`, type: "button", "aria-pressed": String(rel.stage === st.id),
      onclick: () => crm.update(e.id, { stage: st.id }) }, st.label))),
    h("div", { class: "field-row" },
      h("label", { class: "field" }, h("span", { class: "label" }, "Next step"),
        h("input", { type: "text", value: rel.next_step || "", placeholder: "e.g. follow up with HR", oninput: save("next_step") })),
      h("label", { class: "field" }, h("span", { class: "label" }, "Date"),
        h("input", { type: "date", value: rel.next_date || "", onchange: (ev) => crm.update(e.id, { next_date: ev.target.value }) }))),
    h("label", { class: "field" }, h("span", { class: "label" }, "Notes"),
      Object.assign(h("textarea", { placeholder: "Context, who introduced you, what you learned…", oninput: save("notes") }), { value: rel.notes || "" })),
    contactsBlock(e, rel),
    h("div", { class: "field" }, h("span", { class: "label" }, "Draft a message"),
      h("div", { class: "stage-actions" }, Object.entries(DRAFT_KINDS).map(([k, label]) => h("button", { class: "btn ghost", type: "button", onclick: () => showDraft(k) }, label))),
      rel.drafts.length ? h("ul", { class: "history" }, rel.drafts.slice(0, 6).map((d) => h("li", null, h("span", { class: "date" }, shortDate(d.updated)),
        h("button", { class: "link", type: "button", onclick: () => showDraft(d.kind, d) }, `${DRAFT_KINDS[d.kind] || d.kind}${d.status === "sent" ? " · sent" : ""}`)))) : null,
      draftBox,
      h("p", { class: "sub" }, "Drafts are filled from your profile and the firm's sourced research. Nothing is sent from Fintrack.")),
    interactionsBlock(e, rel),
    h("div", { class: "stage-actions" }, h("button", { class: "btn ghost", type: "button", onclick: () => {
      if (confirm(`Stop tracking outreach to ${e.name}? Contacts, notes and drafts for this firm are deleted from this browser.`)) { crm.remove(e.id); renderDetail(); }
    } }, "Stop tracking")),
  );
  return section;
}

function contactsBlock(e, rel) {
  const name = h("input", { type: "text", placeholder: "Name", "aria-label": "Contact name" });
  const role = h("input", { type: "text", placeholder: "Public role, e.g. HR, Associate", "aria-label": "Contact role" });
  const source = h("input", { type: "url", placeholder: "Where you found them (URL)", "aria-label": "Contact source" });
  const email = h("input", { type: "email", placeholder: "Work email, if published", "aria-label": "Contact email" });
  return h("div", { class: "field" }, h("span", { class: "label" }, "Contacts"),
    rel.contacts.length ? h("ul", { class: "history" }, rel.contacts.map((c) => h("li", null, h("strong", { class: "w-kind" }, c.name), ` · ${c.role || ""} `,
      safeUrl(c.source) ? h("a", { class: "sub", href: safeUrl(c.source), target: "_blank", rel: "noopener noreferrer" }, "source ↗") : null,
      h("button", { class: "link", type: "button", "aria-label": `Remove ${c.name}`, onclick: () => { if (confirm(`Remove ${c.name}?`)) crm.removeContact(e.id, c.id); } }, "remove"))))
      : h("p", { class: "sub" }, "Only add people whose professional contact details are published, and only what you need."),
    h("div", { class: "field-row" }, name, role), h("div", { class: "field-row" }, source, email),
    h("div", { class: "stage-actions" }, h("button", { class: "btn", type: "button", onclick: () => {
      if (!name.value.trim()) { toast("Add a name first"); return; }
      crm.addContact(e.id, { name: name.value.trim(), role: role.value.trim(), source: source.value.trim(), email: email.value.trim() });
    } }, "Add contact")));
}

function interactionsBlock(e, rel) {
  const kind = h("select", { "aria-label": "Interaction type" }, ["message", "call", "meeting", "note"].map((k) => h("option", { value: k }, k)));
  const text = h("input", { type: "text", placeholder: "What happened", "aria-label": "Interaction summary" });
  return h("div", { class: "field" }, h("span", { class: "label" }, "Interactions"),
    h("div", { class: "field-row" }, kind, text),
    h("div", { class: "stage-actions" }, h("button", { class: "btn", type: "button", onclick: () => {
      if (!text.value.trim()) return;
      crm.log(e.id, { kind: kind.value, summary: text.value.trim() });
    } }, "Log")),
    rel.interactions.length ? h("ul", { class: "history" }, rel.interactions.slice(0, 12).map((i) => h("li", null, h("span", { class: "date" }, shortDate(i.at)), `${i.kind}: ${i.summary}`))) : null);
}

// Sources & coverage ----------------------------------------------------------------------------------------------
const HEALTH_ORDER = ["failing", "blocked", "gone", "degraded", "unknown", "ok", "disabled"];

function healthMenu() {
  const sel = state.sourceHealth;
  const counts = new Map();
  for (const s of state.ops?.sources || []) counts.set(s.health, (counts.get(s.health) || 0) + 1);
  return h("details", { class: `menu${sel.size ? " active" : ""}` },
    h("summary", null, "Health", sel.size ? h("span", { class: "badge" }, String(sel.size)) : null),
    h("div", { class: "menu-pop", role: "group", "aria-label": "Health" }, HEALTH_ORDER.filter((k) => counts.has(k)).map((k) =>
      h("label", { class: "opt" }, h("input", { type: "checkbox", checked: sel.has(k),
        onchange: (ev) => { ev.target.checked ? sel.add(k) : sel.delete(k); savePrefs(); renderView(); } }),
        h("span", null, HEALTH[k]), h("span", { class: "num" }, String(counts.get(k)))))));
}

const pct = (x) => (x === null || x === undefined ? null : `${Math.round(x * 1000) / 10}%`);
function renderSources(view) {
  const ops = state.ops;
  if (!ops) { view.append(emptyState("No operations data yet", "The first run writes it.")); state.rowIds = []; return; }
  const q = state.query.toLowerCase();
  const rows = ops.sources.filter((s) => s.kind !== "search" && s.kind !== "import")
    .filter((s) => !state.sourceHealth.size || state.sourceHealth.has(s.health))
    .filter((s) => !q || `${s.id} ${s.url} ${state.employers.get(s.employer_id)?.name || ""}`.toLowerCase().includes(q));
  const enabled = ops.sources.filter((s) => s.enabled);
  setMeta([[enabled.filter((s) => s.health === "ok").length, `of ${enabled.length} sources working`],
    [ops.coverage_gaps.length, ops.coverage_gaps.length === 1 ? "open coverage gap" : "open coverage gaps"]]);
  const ai = ops.ai;
  const cov = ops.coverage?.metrics;
  const covNote = (k) => cov?.notes?.[k];
  const jobsPending = Object.values(ops.jobs).reduce((n, x) => n + (x.pending || 0), 0);
  const jobsDead = Object.values(ops.jobs).reduce((n, x) => n + (x.dead || 0), 0);
  const summary = [
    ["AI", ai.available ? `${ai.model} · ${ai.requests_today} of ${ai.daily_requests} requests today` : `Unavailable (${(ai.blocked_reason || "").replaceAll("_", " ")})`],
    ["AI spend", `$${ai.spent_usd.toFixed(2)} this month at list prices${ai.cap_usd ? ` · cap $${ai.cap_usd}` : " · no cap set (fine on the free tier)"}`],
    ["Web search", ai.search_provider === "none" ? "Off — set SEARCH_PROVIDER to enable (see docs)" :
      `${ai.search_provider} · ${ai.searches_today} of ${ai.daily_searches} today · ${ops.search.tried} of ${ops.search.queries} queries tried`],
    ["Work queue", `${jobsPending} waiting${jobsDead ? ` · ${jobsDead} given up` : ""}`],
    ["Discovery", Object.entries(ops.candidates.by_status).map(([k, v]) => `${v} ${k}`).join(" · ") || "No candidates yet"],
    ["Directories", Object.entries(ops.directories).map(([k, v]) => `${k.toUpperCase()} read ${shortDate(v)}`).join(" · ") || "Not read yet"],
    ["Coverage", cov ? `recall ${pct(cov.discovery_recall) ?? "—"} · precision ${pct(cov.discovery_precision) ?? "—"} · source coverage ${pct(cov.source_coverage) ?? "—"} (${cov.benchmark_employers} benchmark firms)`
      : "No benchmark audit yet — add verified postings to benchmark/benchmark.yaml"],
    ...(cov && (covNote("discovery_recall") || covNote("discovery_precision")) ? [["", [covNote("discovery_recall"), covNote("discovery_precision")].filter(Boolean).join("; ")]] : []),
    ["Reliability", cov?.source_reliability_30d != null ? `${pct(cov.source_reliability_30d)} of checks succeeded in 30 days` : "—"],
  ];
  view.append(h("dl", { class: "facts ops-summary" }, summary.flatMap(([k, v]) => [h("dt", null, k), h("dd", null, v)])));
  state.rowIds = [];
  const cols = [[null, "Firm", "c-firm-col", "18%"], [null, "Source", null, null], [null, "Last result", "col-what", "18%"],
    [null, "Checks 7d", "r col-sector", "88px"], [null, "Postings", "r col-lang", "76px"], [null, "Next", "r col-updated", "84px"]];
  const tbody = h("tbody");
  for (const hk of HEALTH_ORDER) {
    const xs = rows.filter((s) => s.health === hk);
    if (!xs.length) continue;
    tbody.append(groupRow(HEALTH[hk], xs.length, cols));
    for (const s of xs) {
      state.rowIds.push(s.id);
      const c7 = s.checks_7d || {};
      const ok = ["ok", "empty", "not_modified", "unchanged", "deferred"].reduce((n, k) => n + (c7[k] || 0), 0);
      const all = Object.values(c7).reduce((n, v) => n + v, 0);
      tbody.append(plainRow(s.id, [
        h("td", { class: "c-firm firm" }, state.employers.get(s.employer_id)?.name || "—"),
        h("td", { class: "c-role", title: s.url }, h("span", { class: "role-title" }, sourceLabel(s.id)),
          s.category !== "careers" ? h("span", { class: "tag muted" }, s.category.replaceAll("_", " ")) : null,
          s.discovery_method !== "seed" ? h("span", { class: "tag muted" }, `via ${s.discovery_method.replaceAll("_", " ")}`) : null),
        h("td", { class: "c-loc col-what sub", title: s.last_error || "" }, s.last_outcome ? `${s.last_outcome.replaceAll("_", " ")}${s.health !== "ok" && s.last_success_at ? " · last ok " + shortDate(s.last_success_at) : ""}` : "not checked yet"),
        h("td", { class: "c-extra r col-sector num" }, all ? `${ok}/${all}` : "—"),
        h("td", { class: "c-extra r col-lang num" }, String(s.postings_total)),
        h("td", { class: "c-days r col-updated date" }, s.enabled && s.next_check_at ? shortDate(s.next_check_at) : "—"),
      ]));
    }
  }
  view.append(h("table", { class: "grid", "aria-label": "Recruitment sources" }, header(cols), tbody));
  if (ops.coverage_gaps.length) {
    const gcols = [[null, "Firm", "c-firm-col", "18%"], [null, "Gap", null, null], [null, "Found by", "col-what", "18%"], [null, "When", "r col-updated", "84px"]];
    view.append(h("table", { class: "grid", "aria-label": "Coverage gaps" }, header(gcols), h("tbody", null,
      groupRow("Coverage gaps", ops.coverage_gaps.length, gcols),
      ops.coverage_gaps.map((g) => h("tr", null, h("td", { class: "c-firm firm" }, state.employers.get(g.employer_id)?.name || "—"),
        h("td", { class: "c-role", title: g.detail }, h("span", { class: "role-title" }, `${g.cause.replaceAll("_", " ")} — ${g.detail}`)),
        h("td", { class: "c-loc col-what sub" }, g.detected_by), h("td", { class: "c-days r col-updated date" }, shortDate(g.detected_at)))))));
  }
  const cands = ops.candidates.recent;
  if (cands.length) {
    const ccols = [[null, "Name", "c-firm-col", "22%"], [null, "Outcome", null, null], [null, "From", "col-what", "16%"], [null, "When", "r col-updated", "84px"]];
    view.append(h("table", { class: "grid", "aria-label": "Discovery" }, header(ccols), h("tbody", null,
      groupRow("Discovery — latest candidates", cands.length, ccols),
      cands.map((c) => h("tr", null, h("td", { class: "c-firm firm" }, c.name),
        h("td", { class: "c-role sub", title: c.note || "" }, `${c.status}${c.note ? " · " + c.note : ""}`),
        h("td", { class: "c-loc col-what sub" }, `${c.origin}${c.country ? " · " + c.country : ""}`),
        h("td", { class: "c-days r col-updated date" }, shortDate(c.updated_at)))))));
  }
}

function renderSourceDetail(pane, id) {
  const s = sourceById(id);
  if (!s) { closeDetail(); return; }
  const e = state.employers.get(s.employer_id);
  fill(pane, 
    detailHead(e?.name || "Unassigned source", sourceLabel(s.id), [extLink(s.url, "Open source ↗", true),
      e ? h("a", { class: "btn", href: "#firms/" + e.id }, "Firm profile") : null].filter(Boolean)),
    h("dl", { class: "facts" }, [["Health", HEALTH[s.health]], ["Kind", `${s.kind} · ${s.category.replaceAll("_", " ")}`],
      ["Belongs to", `${e?.name || "—"} (${s.association})`], ["Found by", `${s.discovery_method.replaceAll("_", " ")}${s.discovery_evidence ? " — " + s.discovery_evidence : ""}`],
      ["Last success", s.last_success_at ? formatDate(s.last_success_at) : "never"], ["Last attempt", s.last_attempt_at ? formatDate(s.last_attempt_at) : "never"],
      ["Last result", (s.last_outcome || "—").replaceAll("_", " ")], ["Error", s.last_error || "—"],
      ["Failures in a row", String(s.consecutive_failures)], ["Checks", `${s.checks_total} total, ${s.checks_failed} failed`],
      ["Internships seen", String(s.postings_total)], ["Next check", s.enabled && s.next_check_at ? formatDate(s.next_check_at) : "not scheduled"],
      ["Last 7 days", Object.entries(s.checks_7d || {}).map(([k, v]) => `${v} ${k.replaceAll("_", " ")}`).join(", ") || "—"]]
      .flatMap(([k, v]) => [h("dt", null, k), h("dd", null, v)])),
    h("p", { class: "detail-note" }, "A failed check never closes a posting. Postings close only when a complete, successful check no longer lists them."),
  );
}

// Profile: private, in this browser ------------------------------------------------------------------------------
const REGION_OPTIONS = ["CH", "EU", "EEA", "EFTA", "GB", "PE", "US", ...REGIONS.EU];
const LEVELS = ["basic", "working", "fluent", "native"];
const LANG_OPTIONS = ["en", "de", "fr", "it", "es", "pt", "nl", "sv", "da", "pl", "zh"];
const TYPE_OPTIONS = ["summer", "off_cycle", "insight", "working_student", "part_time", "graduate", "spring", "winter"];

function redrawProfile() {
  if (state.view !== "profile") return;
  const view = $("#view");
  const controls = () => [...view.querySelectorAll("input, select, textarea, button")];
  const i = controls().indexOf(document.activeElement);
  renderView();
  if (i >= 0) controls()[i]?.focus({ preventScroll: true });
}

function renderProfile(view) {
  const p = profile.get();
  $("#view-meta").textContent = "Stored only in this browser. Use Export in the sidebar to back it up.";
  state.rowIds = [];
  // Save, then redraw so new or removed rows appear and every control reads the saved profile (not this render's
  // copy). Deferred one tick so focus has moved (e.g. Tab after a text field), then restored to the same control.
  const set = (patch) => { profile.set({ ...profile.get(), ...patch }); setTimeout(redrawProfile, 0); };
  const field = (label, input) => h("label", { class: "field" }, h("span", { class: "label" }, label), input);
  const text = (key, ph = "") => h("input", { type: "text", value: p[key] || "", placeholder: ph, onchange: (ev) => set({ [key]: ev.target.value.trim() }) });
  const select = (value, options, onchange, label) => h("select", { "aria-label": label, onchange: (ev) => onchange(ev.target.value || null) },
    options.map(([v, t]) => h("option", { value: v ?? "", selected: (value ?? "") === (v ?? "") }, t)));
  const checks = (selected, options, nameOf, onchange) => h("div", { class: "checks" }, options.map((v) => h("label", { class: "toggle" },
    h("input", { type: "checkbox", checked: selected.includes(v), onchange: (ev) => onchange(ev.target.checked ? [...selected, v] : selected.filter((x) => x !== v)) }), nameOf(v))));
  const rights = p.work_rights || [];
  const updRight = (i, patch) => set({ work_rights: rights.map((r, j) => (j === i ? { ...r, ...patch } : r)) });
  const sugg = suggestions(p);
  view.append(h("div", { class: "page-form" },
    h("section", { class: "detail-section" }, h("h2", { class: "section-title" }, "About you"),
      h("div", { class: "field-row" }, field("Name (used in drafts)", text("name")), field("University", text("university"))),
      h("div", { class: "field-row" },
        field("Degree level", select(p.degree_level, [[null, "Not set"], ["bachelor", "Bachelor"], ["master", "Master"], ["phd", "PhD"]], (v) => set({ degree_level: v }), "Degree level")),
        field("Field of study", text("degree_field", "e.g. Finance"))),
      h("div", { class: "field-row" },
        field("Graduation year", h("input", { type: "number", min: "2020", max: "2040", value: p.graduation_year || "", onchange: (ev) => set({ graduation_year: Number(ev.target.value) || null }) })),
        field("Enrolled as a student", select(p.enrolled === null || p.enrolled === undefined ? null : String(p.enrolled), [[null, "Not set"], ["true", "Yes"], ["false", "No"]],
          (v) => set({ enrolled: v === null ? null : v === "true" }), "Enrolled"))),
      field("Longest internship you can take (months)", h("input", { type: "number", min: "1", max: "24", value: p.max_months || "", onchange: (ev) => set({ max_months: Number(ev.target.value) || null }) }))),
    h("section", { class: "detail-section" }, h("h2", { class: "section-title" }, "Work authorisation"),
      h("p", { class: "sub" }, "Nationality, residence and permits are recorded separately, and a right only counts once you tick Confirmed. Fintrack never infers a right for you; check official sources."),
      field("Citizenships", checks(p.citizenships || [], ["CH", "PE", ...REGIONS.EU.slice(0, 0)], countryName, (v) => set({ citizenships: v }))),
      rights.length ? h("ul", { class: "rights" }, rights.map((r, i) => h("li", { class: "field-row rights-row" },
        select(r.region, REGION_OPTIONS.map((c) => [c, ["EU", "EEA", "EFTA"].includes(c) ? c : `${countryName(c)} (${c})`]), (v) => updRight(i, { region: v }), "Region"),
        select(r.basis, [["citizenship", "Citizenship"], ["permit", "Residence/work permit"], ["other", "Other"]], (v) => updRight(i, { basis: v }), "Basis"),
        h("input", { type: "date", value: r.expires || "", "aria-label": "Expires", title: "Expiry (permits)", onchange: (ev) => updRight(i, { expires: ev.target.value || null }) }),
        h("label", { class: "toggle" }, h("input", { type: "checkbox", checked: !!r.confirmed, onchange: (ev) => updRight(i, { confirmed: ev.target.checked }) }), "Confirmed"),
        h("button", { class: "link", type: "button", onclick: () => set({ work_rights: rights.filter((_, j) => j !== i) }) }, "remove")))) : null,
      h("div", { class: "stage-actions" }, h("button", { class: "btn", type: "button", onclick: () => set({ work_rights: [...rights, { region: "CH", basis: "permit", confirmed: false, expires: null }] }) }, "Add a work right")),
      sugg.length ? h("div", { class: "field" }, h("span", { class: "label" }, "To check"), h("ul", { class: "reasons" }, sugg.map((x) => h("li", null,
        h("span", { class: "mark" }, "?"), h("span", null, x.note, " ", h("button", { class: "link", type: "button", onclick: () => set({ work_rights: [...rights, { region: x.region, basis: x.basis, confirmed: false, expires: null }] }) }, "Add, unconfirmed")))))) : null),
    h("section", { class: "detail-section" }, h("h2", { class: "section-title" }, "Languages"),
      (p.languages || []).length ? h("ul", { class: "rights" }, p.languages.map((l, i) => h("li", { class: "field-row rights-row" },
        select(l.code, LANG_OPTIONS.map((c) => [c, languageName(c)]), (v) => set({ languages: p.languages.map((x, j) => (j === i ? { ...x, code: v } : x)) }), "Language"),
        select(l.level, LEVELS.map((x) => [x, x]), (v) => set({ languages: p.languages.map((x, j) => (j === i ? { ...x, level: v } : x)) }), "Level"),
        h("button", { class: "link", type: "button", onclick: () => set({ languages: p.languages.filter((_, j) => j !== i) }) }, "remove")))) : null,
      h("div", { class: "stage-actions" }, h("button", { class: "btn", type: "button", onclick: () => set({ languages: [...(p.languages || []), { code: "en", level: "fluent" }] }) }, "Add a language"))),
    h("section", { class: "detail-section" }, h("h2", { class: "section-title" }, "What you are looking for"),
      h("p", { class: "sub" }, "Used only to order the list (Fit). Nothing is hidden because of these."),
      field("Sectors", checks(p.prefs.sectors, Object.keys(SECTORS), (x) => SECTORS[x], (v) => set({ prefs: { ...p.prefs, sectors: v } }))),
      field("Countries", checks(p.prefs.countries, ["CH", "PE", "GB", "DE", "FR", "IT", "ES", "NL", "LU", "IE", "AT", "BE"], countryName, (v) => set({ prefs: { ...p.prefs, countries: v } }))),
      field("Formats", checks(p.prefs.types, TYPE_OPTIONS, (x) => SEASONS[x], (v) => set({ prefs: { ...p.prefs, types: v } })))),
  ));
}

// --- view switching -------------------------------------------------------------------------------------------------
function setMeta(parts) {
  const meta = $("#view-meta");
  meta.replaceChildren();
  parts.filter(Boolean).forEach(([n, label], i) => {
    if (i) meta.append(" · ");
    meta.append(h("span", { class: "num" }, String(n)), " ", label);
  });
}

function renderView() {
  const view = $("#view");
  const scroll = view.scrollTop;
  view.replaceChildren();
  view.setAttribute("aria-busy", "false");
  $("#view-title").textContent = VIEWS[state.view];
  ({ discover: renderDiscover, pipeline: renderPipeline, deadlines: renderDeadlines, programs: renderPrograms,
    firms: renderFirms, outreach: renderOutreach, sources: renderSources, profile: renderProfile })[state.view](view);
  $("#toolbar").querySelector(".search").hidden = state.view === "profile";
  fitTables();
  view.scrollTop = scroll;
  if (state.focusId && !state.rowIds.includes(state.focusId)) state.focusId = null;
  renderCounts();
}

function setView(name, { push = true } = {}) {
  if (!VIEWS[name]) name = "discover";
  const changed = name !== state.view;
  state.view = name;
  document.querySelectorAll(".nav a").forEach((a) => {
    if (a.dataset.view === name) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  document.title = `${VIEWS[name]} — fintrack`;
  if (changed) $("#view").scrollTop = 0;
  if (changed && state.detailId && (DETAIL_FOR_VIEW[name] || "role") !== state.detailKind) {
    state.detailId = null;
    $("#detail").hidden = true;
    $(".app").classList.remove("has-detail");
  }
  renderToolbar();
  renderView();
  if (push) syncHash();
}

function syncHash() {
  const hash = `#${state.view}${state.detailId ? "/" + encodeURIComponent(state.detailId) : ""}`;
  if (location.hash !== hash) history.replaceState(null, "", hash);
}

// --- focus & keyboard -----------------------------------------------------------------------------------------------
function focusRow(id, { scroll = false } = {}) {
  state.focusId = id;
  document.querySelectorAll(".grid tbody tr.focused").forEach((tr) => tr.classList.remove("focused"));
  const tr = document.querySelector(`.grid tbody tr[data-id="${CSS.escape(id)}"]`);
  if (tr) {
    tr.classList.add("focused");
    if (scroll) tr.scrollIntoView({ block: "nearest" });
  }
}

function moveFocus(delta) {
  if (!state.rowIds.length) return;
  const i = state.rowIds.indexOf(state.focusId);
  const next = i < 0 ? (delta > 0 ? 0 : state.rowIds.length - 1) : Math.min(state.rowIds.length - 1, Math.max(0, i + delta));
  focusRow(state.rowIds[next], { scroll: true });
  if (state.detailId) openDetail(state.rowIds[next], { focus: false });
}

function onKey(ev) {
  const tag = ev.target.tagName;
  const typing = tag === "INPUT" || tag === "TEXTAREA" || ev.target.isContentEditable;
  if (ev.key === "Escape") {
    if ($("#shortcuts").open) return;
    if (typing && ev.target.id === "search" && ev.target.value) { ev.target.value = ""; onSearch(""); return; }
    if (typing) { ev.target.blur(); return; }
    if (document.querySelector(".menu[open]")) { document.querySelectorAll(".menu[open]").forEach((m) => { m.open = false; }); return; }
    if (state.detailId) { closeDetail(); return; }
    return;
  }
  if (typing || ev.metaKey || ev.ctrlKey || ev.altKey) return;
  const id = state.focusId || state.detailId;
  const roleView = !DETAIL_FOR_VIEW[state.view];
  const handlers = {
    "/": () => $("#search").focus(),
    ...Object.fromEntries(NUMBERED.map((v, i) => [String(i + 1), () => setView(v)])),
    j: () => moveFocus(1), ArrowDown: () => moveFocus(1),
    k: () => moveFocus(-1), ArrowUp: () => moveFocus(-1),
    Enter: () => id && openDetail(id),
    s: () => id && roleView && toggleSaved(id),
    a: () => id && roleView && setStage(id, "applied"),
    "]": () => id && roleView && stepStage(id, 1),
    "[": () => id && roleView && stepStage(id, -1),
    x: () => id && roleView && dismissed.toggle(id),
    o: () => { const r = id && roleView && resolve(id); const u = r && safeUrl(r.url); if (u) window.open(u, "_blank", "noopener"); },
    "?": () => $("#shortcuts").showModal(),
  };
  const fn = handlers[ev.key];
  if (!fn) return;
  if (ev.key === "Enter" && ev.target.closest("button, a, summary, label")) return;
  if ((ev.key === "ArrowDown" || ev.key === "ArrowUp") && ev.target.closest(".menu, .detail")) return;
  ev.preventDefault();
  fn();
}

let searchTimer;
function onSearch(value) {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.query = value.trim(); renderView(); }, 60);
}

// --- detail pane ----------------------------------------------------------------------------------------------------
const DETAIL_FOR_VIEW = { programs: "program", firms: "firm", outreach: "firm", sources: "source" };

function openDetail(id, { focus = true } = {}) {
  const kind = DETAIL_FOR_VIEW[state.view] || "role";
  if (kind === "role" && !resolve(id)) return;
  state.detailKind = kind;
  state.detailId = id;
  document.querySelectorAll(".grid tbody tr.selected").forEach((tr) => tr.classList.remove("selected"));
  document.querySelector(`.grid tbody tr[data-id="${CSS.escape(id)}"]`)?.classList.add("selected");
  $(".app").classList.add("has-detail");
  const pane = $("#detail");
  pane.hidden = false;
  renderDetail();
  // The table just got narrower: refit it and keep the chosen row in sight.
  requestAnimationFrame(() => document.querySelector(`.grid tbody tr[data-id="${CSS.escape(id)}"]`)?.scrollIntoView({ block: "nearest" }));
  if (focus && window.matchMedia("(max-width: 900px)").matches) pane.querySelector(".detail-close")?.focus();
  syncHash();
}

function closeDetail() {
  const id = state.detailId;
  state.detailId = null;
  $("#detail").hidden = true;
  $(".app").classList.remove("has-detail");
  document.querySelectorAll(".grid tbody tr.selected").forEach((tr) => tr.classList.remove("selected"));
  if (id) focusRow(id, { scroll: true });
  syncHash();
}

function renderDetail() {
  const pane = $("#detail");
  const id = state.detailId;
  if (!id) return;
  if (state.detailKind === "program") return renderProgramDetail(pane, id);
  if (state.detailKind === "firm") return renderFirmDetail(pane, id);
  if (state.detailKind === "source") return renderSourceDetail(pane, id);
  const role = resolve(id);
  if (!role) { closeDetail(); return; }
  pane.dataset.id = id;
  const url = safeUrl(role.url);
  const closed = role.status !== "open";
  const d = daysUntil(role.closes, state.today);
  const facts = [
    ["Location", [role.city, (role.countries || []).map(countryName).join(", ")].filter(Boolean).join(" — ") || "Not stated"],
    ["Intake", `${role.season && role.season !== "unknown" ? SEASONS[role.season] : "Not stated"}${role.year ? " " + role.year : ""}`],
    ["Sector", SECTORS[role.sector] || role.sector || "—"],
    ["Languages", (role.languages_required || []).map(languageName).join(", ") || "Not stated"],
    ["Closes", role.closes ? h("span", { class: "closes-line" }, formatDate(role.closes), daysCell(role.closes, { closed })) : "Rolling or not stated"],
    ["Published", role.published_at ? formatDate(role.published_at) + " (stated by the source)" : "Not stated"],
    ["First seen", role.first_seen ? formatDate(role.first_seen) + " (by the tracker)" : "—"],
    ["Last confirmed", role.last_checked ? formatDate(role.last_checked) : "—"],
  ];
  const program = role.program_id && state.programs.get(role.program_id);
  if (program) facts.push(["Program", h("a", { href: "#programs/" + program.id }, program.name)]);
  fill(pane, 
    h("header", { class: "detail-head" },
      h("div", { class: "detail-top" },
        h("span", { class: "detail-firm" }, role.firm_name),
        h("button", { class: "btn ghost detail-close", type: "button", "aria-label": "Close details", onclick: closeDetail }, "Close ", h("kbd", null, "esc"))),
      h("h2", null, role.title),
      h("div", { class: "detail-cta" },
        url ? h("a", { class: "btn primary", href: url, target: "_blank", rel: "noopener noreferrer" }, closed ? "View original ad ↗" : "Open the ad to apply ↗") : null,
        starButtonLarge(role),
        role.status !== "removed" ? h("button", { class: "btn ghost", type: "button", "aria-pressed": String(!!role.dismissed),
          onclick: () => { dismissed.toggle(role.id); toast(role.dismissed ? "Back in the list" : "Dismissed — turn on Hidden to see it again"); } },
        role.dismissed ? "Undo dismiss" : "Not interested") : null),
      role.lifecycle === "pending" ? h("p", { class: "sub detail-line" }, "Found by search; not yet confirmed on the employer's own site. The tracker checks the posting page and opens it once confirmed.") : null,
      role.lifecycle === "unavailable" ? h("p", { class: "sub detail-line" }, "The sources listing this role are failing, so it has not been confirmed for over a week. It has not been closed.") : null,
      closed ? h("p", { class: "sub", style: "margin:10px 0 0" },
        role.status === "removed" ? "This ad is no longer listed on the firm's site." : `Closed${role.closed_on ? " on " + formatDate(role.closed_on) : ""}.`) : null,
      !closed && urgency(d) === "urgent" ? h("p", { class: "days urgent", style: "margin:10px 0 0" }, d === 0 ? "Closes today." : `Closes in ${plural(d, "day")}.`) : null,
    ),
    h("dl", { class: "facts" }, facts.flatMap(([k, v]) => [h("dt", null, k), h("dd", null, v)])),
    role.assessment ? eligibilitySection(role) : null,
    trackingSection(role),
    role.sources ? evidenceSection(role) : null,
    h("p", { class: "detail-note" }, "Each fact shows where it came from. AI-read facts can be wrong; check the original ad before applying."),
  );
}

const RESULT = { pass: "✓", fail: "✕", gap: "!", unknown: "?", sponsorship: "~", info: "·" };
function eligibilitySection(role) {
  const a = role.assessment;
  const fit = role.matchFactors || [];
  return h("section", { class: "detail-section", "aria-label": "Eligibility" },
    h("h3", { class: "label" }, "Eligibility"),
    h("p", { class: "detail-verdict" }, h("span", { class: `elig ${a.outcome}` }, a.label)),
    h("ul", { class: "reasons" }, a.lines.map((l) => h("li", { class: `r-${l.result}` },
      h("span", { class: "mark", "aria-hidden": "true" }, RESULT[l.result] || "·"), l.reason))),
    a.outcome === "insufficient" || a.lines.some((l) => l.reason.includes("Profile"))
      ? h("p", { class: "sub" }, h("a", { href: "#profile" }, "Complete your profile"), " to assess more of this.") : null,
    h("h3", { class: "label" }, `Why it ranks here · ${role.match}`),
    fit.length ? h("ul", { class: "reasons" }, fit.map((f) => h("li", null,
      h("span", { class: "mark num" }, f.points > 0 ? `+${f.points}` : String(f.points)), f.why)))
      : h("p", { class: "sub" }, "No preference matches yet. Set sectors, countries and formats in Profile."),
  );
}

const METHOD = { structured: "job board API", jsonld: "page structured data", rule: "parsed from the text", ai: "AI reading",
  manual: "set by hand" };
function evidenceSection(role) {
  const facts = (role.facts || []).filter((f) => f.field !== "title");
  const byField = new Map();
  for (const f of facts) if (!byField.has(f.field) || rankMethod(f.method) > rankMethod(byField.get(f.field).method)) byField.set(f.field, f);
  return h("section", { class: "detail-section", "aria-label": "Evidence" },
    h("h3", { class: "label" }, "Where it was found"),
    h("ul", { class: "history" }, role.sources.map((x) => h("li", null,
      h("span", { class: "date" }, shortDate(x.last_seen_at)),
      safeUrl(x.url) ? h("a", { href: safeUrl(x.url), target: "_blank", rel: "noopener noreferrer" }, sourceLabel(x.source_id)) : sourceLabel(x.source_id),
      x.present ? "" : " — no longer listed"))),
    byField.size ? h("details", { class: "evidence" }, h("summary", { class: "label" }, "How each field was read"),
      h("dl", { class: "facts compact" }, [...byField.values()].flatMap((f) => [h("dt", null, f.field.replaceAll("_", " ")),
        h("dd", null, `${Array.isArray(f.value) ? f.value.join(", ") : f.value} — ${METHOD[f.method] || f.method}`,
          f.evidence ? h("span", { class: "sub quote" }, `“${f.evidence}”`) : null)]))) : null,
    role.events?.length ? h("details", { class: "evidence" }, h("summary", { class: "label" }, "History"),
      h("ul", { class: "history" }, role.events.map((e) => h("li", null, h("span", { class: "date" }, shortDate(e.at)),
        e.event === "changed" ? `${e.field} changed: ${e.old} → ${e.new}` : e.event === "status" || e.event === "reopened"
          ? `${e.event === "reopened" ? "reopened" : (e.from || "") + " → " + e.to}${e.note ? " · " + e.note : ""}` : `${e.event}${e.note ? " · " + e.note : ""}`)))) : null,
  );
}
const rankMethod = (m) => ({ manual: 5, structured: 4, jsonld: 3, rule: 2, ai: 1 })[m] || 0;
function sourceLabel(id) {
  const [kind, rest] = String(id).split(":");
  const names = { greenhouse: "Greenhouse", lever: "Lever", ashby: "Ashby", workday: "Workday", smartrecruiters: "SmartRecruiters",
    recruitee: "Recruitee", personio: "Personio", page: "Careers page", search: "Web search", import: "Your import" };
  return `${names[kind] || kind}${kind === "page" || kind === "search" || kind === "import" ? "" : " · " + rest}`;
}

function starButtonLarge(role) {
  const on = !!tracker.get(role.id);
  return h("button", { class: "btn", type: "button", "aria-pressed": String(on), onclick: () => toggleSaved(role.id) },
    svgStar(on), on ? "Tracked" : "Shortlist", h("kbd", null, "s"));
}

function trackingSection(role) {
  const e = tracker.get(role.id);
  const cur = e ? stageById(e.stage) : null;
  const section = h("section", { class: "detail-section tracking", "aria-label": "Your application" });
  section.append(h("h3", { class: "label" }, "Your application"));
  section.append(h("div", { class: "stepper", role: "group", "aria-label": "Stage" }, PIPELINE.map((s) => h("button", {
    type: "button",
    class: cur && cur.id === s.id ? "current" : cur && cur.rank > s.rank ? "done" : null,
    "aria-pressed": String(!!cur && cur.id === s.id),
    onclick: () => setStage(role.id, s.id),
  }, s.label))));
  const end = STAGES.filter((s) => s.rank < 0);
  section.append(h("div", { class: "stage-actions" },
    end.map((s) => h("button", {
      class: `btn${cur?.id === s.id ? "" : " ghost"}`, type: "button", "aria-pressed": String(cur?.id === s.id),
      onclick: () => setStage(role.id, s.id),
    }, s.label)),
    e ? h("button", { class: "btn ghost", type: "button", onclick: () => toggleSaved(role.id) }, "Stop tracking") : null,
  ));
  if (!e) {
    section.append(h("p", { class: "sub", style: "margin:12px 0 0" }, "Pick a stage to start tracking. Notes and dates stay in this browser."));
    return section;
  }
  const timers = {};
  const save = (field) => (ev) => {
    clearTimeout(timers[field]);
    const value = ev.target.value;
    timers[field] = setTimeout(() => tracker.update(role.id, { [field]: value }), 250);
  };
  section.append(
    h("div", { class: "field-row" },
      h("label", { class: "field" }, h("span", { class: "label" }, "Next step"),
        h("input", { type: "text", value: e.next_step || "", placeholder: "e.g. HireVue, call with HR", oninput: save("next_step") })),
      h("label", { class: "field" }, h("span", { class: "label" }, "Date"),
        h("input", { type: "date", value: e.next_date || "", onchange: (ev) => tracker.update(role.id, { next_date: ev.target.value }) }))),
    h("label", { class: "field" }, h("span", { class: "label" }, "Notes"),
      Object.assign(h("textarea", { placeholder: "Contacts, referral, what they asked…", oninput: save("notes") }), { value: e.notes || "" })),
  );
  if (e.history?.length) {
    section.append(h("div", { class: "field" }, h("span", { class: "label" }, "History"),
      h("ul", { class: "history" }, [...e.history].reverse().slice(0, 8).map((x) =>
        h("li", null, h("span", { class: "date" }, shortDate(x.at)), stageById(x.stage)?.label || x.stage)))));
  }
  return section;
}

// --- theme ----------------------------------------------------------------------------------------------------------
function applyTheme(name) {
  if (!THEME_FONTS[name]) name = "ledger";
  state.theme = name;
  document.documentElement.dataset.theme = name;
  $("#theme-fonts").href = `https://fonts.googleapis.com/css2?${THEME_FONTS[name]}&display=swap`;
  document.querySelectorAll('input[name="theme"]').forEach((i) => { i.checked = i.value === name; });
  savePrefs();
}

// --- export / import ------------------------------------------------------------------------------------------------
function exportTracker() {
  const blob = new Blob([exportAll({ tracker, profile, crm, dismissed })], { type: "application/json" });
  const a = h("a", { href: URL.createObjectURL(blob), download: `fintrack-applications-${state.today}.json` });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  toast(`Exported ${plural(tracker.count(), "tracked role")}, your profile and outreach`);
}
async function importTracker(file) {
  try {
    importAll(await file.text(), { tracker, profile, crm, dismissed });
    toast(`Imported · ${plural(tracker.count(), "tracked role")}`);
  } catch (err) {
    toast(`Import failed: ${err.message}`);
  }
}

// --- eligibility & ranking ------------------------------------------------------------------------------------------
// Recomputed whenever the profile, tracking or dismissals change. Everything here stays in the browser.
function derive() {
  const p = profile.get();
  for (const r of state.roles) {
    const a = assess(r, p, { today: state.today, languageName });
    r.assessment = a;
    r.eligibility = a.outcome;
    r.dismissed = dismissed.has(r.id);
    const rk = rank(r, { prefs: p.prefs, eligibility: a.outcome, today: state.today, tracked: !!tracker.get(r.id), daysUntil });
    r.match = rk.score;
    r.matchFactors = rk.factors;
  }
}

// --- boot -----------------------------------------------------------------------------------------------------------
function skeleton() {
  const view = $("#view");
  const rows = Array.from({ length: 14 }, (_, i) => h("tr", null, [48, 22, 46, 18, 12].map((w) =>
    h("td", null, h("span", { style: `width:${w + ((i * 7) % 20)}%` })))));
  view.replaceChildren(h("table", { class: "grid skeleton", "aria-hidden": "true" }, h("tbody", null, rows)));
}

async function getJson(name, required) {
  const resp = await fetch(`data/${name}`, { cache: "no-cache" });
  if (!resp.ok) {
    if (!required) return null;
    throw new Error(resp.status === 404 ? "not-yet" : "HTTP " + resp.status);
  }
  return resp.json();
}

async function load() {
  skeleton();
  try {
    const [data, market, ops, digest] = await Promise.all([getJson("opportunities.json", true),
      getJson("market.json", false), getJson("ops.json", false), getJson("digest.json", false)]);
    if (!data || !Array.isArray(data.opportunities)) throw new Error("The data file has an unexpected shape.");
    state.data = data;
    state.market = market || state.market;
    state.ops = ops;
    state.digest = digest || state.digest;
    state.employers = new Map(state.market.employers.map((e) => [e.id, e]));
    state.prospects = new Map(state.market.prospects.map((p) => [p.employer_id, p]));
    state.roles = data.opportunities.map((o) => toRole(o, state.employers));
    state.byId = new Map(state.roles.map((r) => [r.id, r]));
    const live = new Map();
    for (const r of state.roles) if (r.program_id && r.status === "open") live.set(r.program_id, (live.get(r.program_id) || 0) + 1);
    state.programs = new Map(state.market.programs.map((p) => [p.id, { ...p, live: live.get(p.id) || 0 }]));
    derive();
  } catch (err) {
    const view = $("#view");
    view.setAttribute("aria-busy", "false");
    view.replaceChildren(err.message === "not-yet"
      ? emptyState("No data published yet", "The tracker has not finished its first run. Check back after the next daily update.")
      : emptyState("Could not load postings", `${err.message || "Network error"}. Your tracked applications are safe in this browser.`,
        h("button", { class: "btn", type: "button", onclick: load }, "Try again")));
    $("#view-meta").textContent = "";
    return;
  }
  renderCoverage();
  const [view, id] = location.hash.slice(1).split("/");
  setView(view || "discover", { push: false });
  if (id) { focusRow(id, { scroll: true }); openDetail(decodeURIComponent(id), { focus: false }); }
}

// The detail pane's left edge can be dragged; the width is remembered.
function applyDetailWidth() {
  if (state.detailWidth) document.documentElement.style.setProperty("--detail-w", `${state.detailWidth}px`);
  else document.documentElement.style.removeProperty("--detail-w");
}
function initDetailResize() {
  const pane = $("#detail");
  const grip = h("div", { class: "detail-resize", role: "separator", "aria-orientation": "vertical", "aria-label": "Resize details",
    title: "Drag to resize · double-click to reset", tabindex: "0" });
  pane.before(grip);
  const clamp = (w) => Math.round(Math.min(Math.max(w, 300), window.innerWidth * 0.6));
  grip.addEventListener("pointerdown", (ev) => {
    ev.preventDefault();
    grip.setPointerCapture(ev.pointerId);
    document.body.classList.add("is-resizing");
    const move = (e) => { state.detailWidth = clamp(window.innerWidth - e.clientX); applyDetailWidth(); };
    grip.addEventListener("pointermove", move);
    grip.addEventListener("pointerup", () => { grip.removeEventListener("pointermove", move); document.body.classList.remove("is-resizing"); savePrefs(); fitTables(); }, { once: true });
  });
  grip.addEventListener("dblclick", () => { state.detailWidth = null; applyDetailWidth(); savePrefs(); fitTables(); });
  grip.addEventListener("keydown", (ev) => {
    const step = ev.key === "ArrowLeft" ? 24 : ev.key === "ArrowRight" ? -24 : 0;
    if (!step) return;
    ev.preventDefault();
    state.detailWidth = clamp((state.detailWidth || pane.offsetWidth) + step); applyDetailWidth(); savePrefs(); fitTables();
  });
}

function init() {
  applyTheme(state.theme);
  applyDetailWidth();
  initDetailResize();
  let fitTimer;
  new ResizeObserver(() => { clearTimeout(fitTimer); fitTimer = setTimeout(fitTables, 80); }).observe($("#view"));
  document.querySelectorAll('input[name="theme"]').forEach((i) => i.addEventListener("change", () => applyTheme(i.value)));
  document.querySelectorAll(".nav a").forEach((a) => a.addEventListener("click", (ev) => { ev.preventDefault(); setView(a.dataset.view); }));
  $("#search").addEventListener("input", (ev) => onSearch(ev.target.value));
  $("#open-shortcuts").addEventListener("click", () => $("#shortcuts").showModal());
  $("#export").addEventListener("click", exportTracker);
  $("#import").addEventListener("change", (ev) => { if (ev.target.files[0]) importTracker(ev.target.files[0]); ev.target.value = ""; });
  document.addEventListener("keydown", onKey);
  // Close any open filter menu when clicking elsewhere.
  document.addEventListener("click", (ev) => {
    document.querySelectorAll(".menu[open]").forEach((m) => { if (!m.contains(ev.target)) m.open = false; });
  });
  window.addEventListener("hashchange", () => {
    const [view, id] = location.hash.slice(1).split("/");
    if (view && view !== state.view) setView(view, { push: false });
    if (id && decodeURIComponent(id) !== state.detailId) openDetail(decodeURIComponent(id), { focus: false });
  });
  const rederive = () => { if (!state.data) return; derive(); renderView(); renderDetail(); };
  profile.onChange(() => { if (state.view !== "profile") rederive(); else derive(); });
  dismissed.onChange(rederive);
  crm.onChange(() => { if (!state.data) return; renderView(); renderCounts(); });
  tracker.onChange((kind) => {
    if (!state.data) return;
    derive();
    renderView();
    if (state.focusId) focusRow(state.focusId);
    if (kind !== "fields") renderDetail(); // never rebuild the fields someone is typing in
  });
  // Keep "days left" honest if the tab stays open past midnight.
  setInterval(() => { const t = localToday(); if (t !== state.today) { state.today = t; renderView(); } }, 60_000);
  load();
}

init();
