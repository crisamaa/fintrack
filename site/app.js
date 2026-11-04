import {
  visibleRoles, sortRoles, safeUrl, formatDate, searchRoles, daysUntil, urgency, deadlineBucket, STAGES, stageById,
} from "./filters.js";
import { createTracker, prefs } from "./store.js";

// --- reference data -----------------------------------------------------------------------------------------------
const SECTORS = { banking: "Banking", private_equity: "Private equity", finance: "Asset & wealth mgmt", fintech: "Fintech" };
const SEASONS = { summer: "Summer", spring: "Spring", winter: "Winter", off_cycle: "Off-cycle", unknown: "—" };
const PIPELINE = STAGES.filter((s) => s.rank >= 0); // saved → offer, the stepper
const THEME_FONTS = {
  ledger: "family=Instrument+Sans:wght@400;500;600&family=Newsreader:opsz,wght@6..72,400;6..72,500&family=JetBrains+Mono:wght@400;500",
  swiss: "family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500",
  terminal: "family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500",
};
const VIEWS = { discover: "Discover", pipeline: "Pipeline", deadlines: "Deadlines" };

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
const saved = prefs.load();
const state = {
  data: null,
  roles: [],
  byId: new Map(),
  today: localToday(),
  view: "discover",
  query: "",
  openOnly: saved.openOnly ?? true,
  trackedOnly: saved.trackedOnly ?? false,
  filters: {
    countries: new Set(saved.filters?.countries || []),
    sectors: new Set(saved.filters?.sectors || []),
    seasons: new Set(saved.filters?.seasons || []),
    languages: new Set(saved.filters?.languages || []),
  },
  sort: saved.sort || { key: "closes", dir: "asc" },
  theme: saved.theme || document.documentElement.dataset.theme || "ledger",
  rowIds: [],
  focusId: null,
  detailId: null,
};

function savePrefs() {
  prefs.save({
    theme: state.theme, openOnly: state.openOnly, trackedOnly: state.trackedOnly, sort: state.sort,
    filters: Object.fromEntries(Object.entries(state.filters).map(([k, v]) => [k, [...v]])),
  });
}

// --- tiny DOM helper ------------------------------------------------------------------------------------------------
const $ = (sel, root = document) => root.querySelector(sel);
function h(tag, attrs, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(e.dataset, v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) e.append(c);
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
  const updated = d.generated_at ? new Date(d.generated_at) : null;
  const when = updated && !isNaN(updated)
    ? updated.toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "—";
  box.append(
    h("div", null, "Updated ", h("strong", { class: "num" }, when)),
    h("div", null, h("strong", { class: "num" }, String(d.firms_checked ?? 0)), " firms read"),
  );
  if (d.firms_unread > 0) box.append(h("div", null, `${d.firms_unread} queued for reading`));
  if (d.budget_exhausted) box.append(h("div", { class: "warn" }, "Monthly AI budget reached — some postings wait for next month."));
  const lists = [["Not reached this run", d.firms_failed], ["Not covered", d.firms_not_covered]];
  for (const [title, names] of lists) {
    if (!names?.length) continue;
    box.append(h("details", null, h("summary", null, `${title} (${names.length})`), h("ul", null, names.map((n) => h("li", null, n)))));
  }
}

function pipelineEntries() {
  return Object.values(tracker.all()).map((e) => ({ entry: e, role: resolve(e.id) })).filter((x) => x.role);
}

function renderCounts() {
  const open = state.roles.filter((r) => r.status === "open").length;
  const active = pipelineEntries().filter((x) => !stageById(x.entry.stage)?.closed).length;
  const week = deadlineEvents().filter((ev) => ev.days >= 0 && ev.days <= 6).length;
  $("#count-discover").textContent = open || "";
  $("#count-pipeline").textContent = active || "";
  $("#count-deadlines").textContent = week || "";
}

// --- toolbar --------------------------------------------------------------------------------------------------------
function optionCounts(key, getter) {
  const counts = new Map();
  for (const r of state.roles) {
    if (state.openOnly && r.status !== "open") continue;
    for (const v of getter(r)) counts.set(v, (counts.get(v) || 0) + 1);
  }
  return counts;
}

function filterMenu(key, label, getter, nameOf, hint) {
  const sel = state.filters[key];
  const counts = optionCounts(key, getter);
  const values = [...new Set([...counts.keys(), ...sel])].sort((a, b) => nameOf(a).localeCompare(nameOf(b)));
  const menu = h("details", { class: `menu${sel.size ? " active" : ""}` },
    h("summary", { "aria-label": `${label} filter${sel.size ? `, ${sel.size} selected` : ""}` },
      label, sel.size ? h("span", { class: "badge" }, String(sel.size)) : null),
    h("div", { class: "menu-pop", role: "group", "aria-label": label },
      values.length ? values.map((v) => h("label", { class: "opt" },
        h("input", {
          type: "checkbox", checked: sel.has(v),
          onchange: (ev) => { ev.target.checked ? sel.add(v) : sel.delete(v); savePrefs(); renderView(); renderToolbar(key); },
        }),
        h("span", null, nameOf(v)),
        h("span", { class: "num" }, String(counts.get(v) || 0)),
      )) : h("p", { class: "hint" }, "No values yet."),
      hint ? h("p", { class: "hint" }, hint) : null,
      sel.size ? h("div", { class: "menu-actions" },
        h("button", { class: "link", type: "button", onclick: () => { sel.clear(); savePrefs(); renderView(); renderToolbar(key); } }, "Clear")) : null,
    ));
  return menu;
}

function renderToolbar(keepOpen) {
  const box = $("#filters");
  box.replaceChildren();
  if (state.view === "discover") {
    const active = Object.values(state.filters).some((s) => s.size);
    box.append(...[
      filterMenu("countries", "Country", (r) => r.countries || [], countryName),
      filterMenu("sectors", "Sector", (r) => [r.sector], (v) => SECTORS[v] || v),
      filterMenu("seasons", "Season", (r) => [r.season], (v) => (v === "unknown" ? "Not stated" : SEASONS[v] || v)),
      filterMenu("languages", "Language", (r) => r.languages_required || [], languageName,
        "Shows roles you qualify for: any role needing a language you have not ticked is hidden."),
      h("label", { class: "toggle" }, h("input", {
        type: "checkbox", checked: state.openOnly,
        onchange: (ev) => { state.openOnly = ev.target.checked; savePrefs(); renderView(); renderToolbar(); },
      }), "Open only"),
      active ? h("button", {
        class: "btn ghost clear", type: "button",
        onclick: () => { Object.values(state.filters).forEach((s) => s.clear()); savePrefs(); renderView(); renderToolbar(); },
      }, "Clear filters") : null,
    ].filter(Boolean));
  } else if (state.view === "deadlines") {
    box.append(h("label", { class: "toggle" }, h("input", {
      type: "checkbox", checked: state.trackedOnly,
      onchange: (ev) => { state.trackedOnly = ev.target.checked; savePrefs(); renderView(); },
    }), "Tracked only"));
  }
  if (keepOpen) {
    const menus = box.querySelectorAll(".menu");
    const idx = ["countries", "sectors", "seasons", "languages"].indexOf(keepOpen);
    if (menus[idx]) menus[idx].open = true;
  }
}

// --- tables ---------------------------------------------------------------------------------------------------------
function header(cols) {
  return h("thead", null, h("tr", null, cols.map(([key, label, cls, width]) => {
    const sortable = key && state.view === "discover";
    const sorted = sortable && state.sort.key === key;
    return h("th", {
      class: cls, scope: "col", style: width ? `width:${width}` : null,
      "aria-sort": sorted ? (state.sort.dir === "asc" ? "ascending" : "descending") : null,
    }, sortable ? h("button", {
      type: "button",
      onclick: () => {
        state.sort = { key, dir: state.sort.key === key && state.sort.dir === "asc" ? "desc" : "asc" };
        savePrefs(); renderView();
      },
    }, label) : label || h("span", { class: "visually-hidden" }, "Shortlist"));
  })));
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
      "The first daily run reads every firm's job board over a few days. Roles appear here as soon as they are found."));
    return;
  }
  if (!rows.length) {
    view.append(emptyState("Nothing matches", state.query ? `No role matches “${state.query}” with the current filters.` : "No role matches the current filters.",
      h("button", { class: "btn", type: "button", onclick: resetDiscover }, "Clear search and filters")));
    return;
  }
  const cols = [
    [null, "", "c-star", "44px"],
    ["firm_name", "Firm", null, "16%"],
    ["title", "Role", null, null],
    ["city", "Location", "col-loc", "13%"],
    ["season", "Intake", "col-season", "108px"],
    ["sector", "Sector", "col-sector", "128px"],
    [null, "Lang.", "col-lang", "60px"],
    ["closes", "Closes", "r col-closes", "72px"],
    [null, "Left", "r", "72px"],
    [null, "Status", "col-status", "136px"],
  ];
  const tbody = h("tbody");
  const frag = document.createDocumentFragment();
  for (const r of rows) {
    const e = tracker.get(r.id);
    frag.append(roleRow(r, [
      h("td", { class: "c-star" }, starButton(r)),
      h("td", { class: "c-firm firm", title: r.firm_name }, r.firm_name),
      h("td", { class: "c-role", title: r.title }, h("span", { class: "role-title" }, r.title), isNew(r) && r.status === "open" ? h("span", { class: "new" }, "new") : null),
      h("td", { class: "c-loc col-loc sub", title: placeOf(r) }, placeOf(r)),
      h("td", { class: "c-extra col-season sub" }, `${SEASONS[r.season] || "—"}${r.year ? " " + r.year : ""}`),
      h("td", { class: "c-extra col-sector sub" }, SECTORS[r.sector] || r.sector),
      h("td", { class: "c-extra col-lang sub mono", title: (r.languages_required || []).map(languageName).join(", ") }, langCodes(r)),
      h("td", { class: "c-extra col-closes r date" }, r.closes ? shortDate(r.closes) : ""),
      h("td", { class: "c-days r" }, daysCell(r.closes, { closed: r.status !== "open" })),
      h("td", { class: "c-extra col-status" }, e ? stageView(e.stage) : ""),
    ]));
  }
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
  ({ discover: renderDiscover, pipeline: renderPipeline, deadlines: renderDeadlines })[state.view](view);
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
  renderToolbar();
  renderView();
  if (push) syncHash();
}

function syncHash() {
  const hash = `#${state.view}${state.detailId ? "/" + state.detailId : ""}`;
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
  const handlers = {
    "/": () => $("#search").focus(),
    "1": () => setView("discover"), "2": () => setView("pipeline"), "3": () => setView("deadlines"),
    j: () => moveFocus(1), ArrowDown: () => moveFocus(1),
    k: () => moveFocus(-1), ArrowUp: () => moveFocus(-1),
    Enter: () => id && openDetail(id),
    s: () => id && toggleSaved(id),
    a: () => id && setStage(id, "applied"),
    "]": () => id && stepStage(id, 1),
    "[": () => id && stepStage(id, -1),
    o: () => { const r = id && resolve(id); const u = r && safeUrl(r.url); if (u) window.open(u, "_blank", "noopener"); },
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
function openDetail(id, { focus = true } = {}) {
  const role = resolve(id);
  if (!role) return;
  state.detailId = id;
  document.querySelectorAll(".grid tbody tr.selected").forEach((tr) => tr.classList.remove("selected"));
  document.querySelector(`.grid tbody tr[data-id="${CSS.escape(id)}"]`)?.classList.add("selected");
  $(".app").classList.add("has-detail");
  const pane = $("#detail");
  pane.hidden = false;
  renderDetail();
  if (focus && window.matchMedia("(max-width: 1180px)").matches) pane.querySelector(".detail-close")?.focus();
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
    ["First seen", role.first_seen ? formatDate(role.first_seen) : "—"],
    ["Last checked", role.last_checked ? formatDate(role.last_checked) : "—"],
  ];
  pane.replaceChildren(
    h("header", { class: "detail-head" },
      h("div", { class: "detail-top" },
        h("span", { class: "detail-firm" }, role.firm_name),
        h("button", { class: "btn ghost detail-close", type: "button", "aria-label": "Close details", onclick: closeDetail }, "Close ", h("kbd", null, "esc"))),
      h("h2", null, role.title),
      h("div", { class: "detail-cta" },
        url ? h("a", { class: "btn primary", href: url, target: "_blank", rel: "noopener noreferrer" }, closed ? "View original ad ↗" : "Open the ad to apply ↗") : null,
        starButtonLarge(role)),
      closed ? h("p", { class: "sub", style: "margin:10px 0 0" },
        role.status === "removed" ? "This ad is no longer listed on the firm's site." : `Closed${role.closed_on ? " on " + formatDate(role.closed_on) : ""}.`) : null,
      !closed && urgency(d) === "urgent" ? h("p", { class: "days urgent", style: "margin:10px 0 0" }, d === 0 ? "Closes today." : `Closes in ${plural(d, "day")}.`) : null,
    ),
    h("dl", { class: "facts" }, facts.flatMap(([k, v]) => [h("dt", null, k), h("dd", null, v)])),
    trackingSection(role),
    h("p", { class: "detail-note" }, "Details are read from the firm's ad by an AI model and can be wrong. Check the original ad before applying."),
  );
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
  const blob = new Blob([tracker.exportJson()], { type: "application/json" });
  const a = h("a", { href: URL.createObjectURL(blob), download: `fintrack-applications-${state.today}.json` });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  toast(`Exported ${plural(tracker.count(), "tracked role")}`);
}
async function importTracker(file) {
  try {
    tracker.importJson(await file.text());
    toast(`Imported · ${plural(tracker.count(), "tracked role")}`);
  } catch (err) {
    toast(`Import failed: ${err.message}`);
  }
}

// --- boot -----------------------------------------------------------------------------------------------------------
function skeleton() {
  const view = $("#view");
  const rows = Array.from({ length: 14 }, (_, i) => h("tr", null, [48, 22, 46, 18, 12].map((w) =>
    h("td", null, h("span", { style: `width:${w + ((i * 7) % 20)}%` })))));
  view.replaceChildren(h("table", { class: "grid skeleton", "aria-hidden": "true" }, h("tbody", null, rows)));
}

async function load() {
  skeleton();
  try {
    const resp = await fetch("data/roles.json", { cache: "no-cache" });
    if (!resp.ok) throw new Error(resp.status === 404 ? "not-yet" : "HTTP " + resp.status);
    const data = await resp.json();
    if (!data || !Array.isArray(data.roles)) throw new Error("The data file has an unexpected shape.");
    state.data = data;
    state.roles = data.roles;
    state.byId = new Map(data.roles.map((r) => [r.id, r]));
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
  if (id && resolve(id)) { focusRow(id, { scroll: true }); openDetail(id, { focus: false }); }
}

function init() {
  applyTheme(state.theme);
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
    const [view] = location.hash.slice(1).split("/");
    if (view && view !== state.view) setView(view, { push: false });
  });
  tracker.onChange((kind) => {
    if (!state.data) return;
    renderView();
    if (state.focusId) focusRow(state.focusId);
    if (kind !== "fields") renderDetail(); // never rebuild the fields someone is typing in
  });
  // Keep "days left" honest if the tab stays open past midnight.
  setInterval(() => { const t = localToday(); if (t !== state.today) { state.today = t; renderView(); } }, 60_000);
  load();
}

init();
