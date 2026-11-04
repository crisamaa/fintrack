import { visibleRoles, sortRoles, safeUrl, formatDate } from "./filters.js";

const SECTORS = { banking: "Banking", private_equity: "Private equity", finance: "Finance", fintech: "Fintech" };
const SEASONS = { summer: "Summer", spring: "Spring", winter: "Winter", off_cycle: "Off-cycle", unknown: "Unknown" };
const COLUMNS = [
  ["firm_name", "Firm"], ["title", "Role"], ["city", "City"], ["countries", "Country"], ["sector", "Sector"],
  ["season", "Season"], ["languages_required", "Languages"], ["closes", "Closes"], ["last_checked", "Last checked"],
];
const regionNames = new Intl.DisplayNames(["en"], { type: "region" });
const countryName = (code) => { try { return regionNames.of(code) || code; } catch { return code; } };
const label = (map, key) => map[key] || (key ? String(key) : "Unknown");

const $ = (sel, root = document) => root.querySelector(sel);
const state = { roles: [], sortKey: "closes", sortDir: "asc", selected: { countries: new Set(), sectors: new Set(), seasons: new Set(), languages: new Set() } };

function el(tag, text, attrs) {
  const e = document.createElement(tag);
  if (text !== undefined && text !== null) e.textContent = text;
  if (attrs) for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}

function renderStatus(data) {
  const parts = [];
  if (data.generated_at) {
    const d = new Date(data.generated_at);
    parts.push("Last updated " + (isNaN(d) ? data.generated_at : d.toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" })));
  }
  parts.push(`${data.firms_checked ?? 0} firms checked`);
  const failed = data.firms_failed || [];
  parts.push(`${failed.length} failed`);
  if (data.firms_unread > 0) parts.push(`${data.firms_unread} not yet read by AI`);
  $("#status").textContent = parts.join(" · ");
  if (data.budget_exhausted) {
    const note = el("span", "The monthly AI budget ran out, so some pages and new roles were not read. They will be picked up next month.", { class: "budget-note" });
    $("#status").append(" ", note);
  }
  const notCovered = data.firms_not_covered || [];
  const cov = $("#coverage");
  if (failed.length || notCovered.length) {
    const bits = [];
    if (failed.length) bits.push(`Not checked this run (${failed.length} ${failed.length === 1 ? "firm" : "firms"})`);
    if (notCovered.length) bits.push(`Not covered (${notCovered.length} ${notCovered.length === 1 ? "firm" : "firms"})`);
    $("#coverage-summary").textContent = bits.join(" · ");
    $("#coverage-list").textContent = failed.join(", ");
    $("#coverage-list").hidden = !failed.length;
    $("#coverage-blocked").hidden = !notCovered.length;
    $("#coverage-blocked-names").textContent = notCovered.join(", ");
    cov.hidden = false;
    cov.open = failed.length + notCovered.length <= 5;
  }
}

function buildFilters() {
  const uniq = (fn) => [...new Set(state.roles.flatMap(fn))];
  const sets = {
    countries: uniq((r) => r.countries || []).sort((a, b) => countryName(a).localeCompare(countryName(b))).map((v) => [v, countryName(v)]),
    sectors: uniq((r) => [r.sector]).sort().map((v) => [v, label(SECTORS, v)]),
    seasons: uniq((r) => [r.season]).sort().map((v) => [v, label(SEASONS, v)]),
    languages: uniq((r) => r.languages_required || []).sort().map((v) => [v, v]),
  };
  for (const [name, options] of Object.entries(sets)) {
    const box = $(`details[data-filter="${name}"] .options`);
    box.replaceChildren();
    for (const [value, text] of options) {
      const input = el("input", null, { type: "checkbox", value });
      input.addEventListener("change", () => {
        input.checked ? state.selected[name].add(value) : state.selected[name].delete(value);
        render();
      });
      const lab = el("label");
      lab.append(input, " ", text);
      box.append(lab);
    }
    if (!options.length) box.append(el("span", "No options in the data.", { class: "hint" }));
  }
}

function renderHead() {
  const tr = $("#roles thead tr");
  tr.replaceChildren();
  for (const [key, text] of COLUMNS) {
    const th = el("th", null, { scope: "col" });
    const active = state.sortKey === key;
    th.setAttribute("aria-sort", active ? (state.sortDir === "asc" ? "ascending" : "descending") : "none");
    const btn = el("button", text + (active ? (state.sortDir === "asc" ? " ▲" : " ▼") : ""), { type: "button" });
    btn.addEventListener("click", () => {
      if (state.sortKey === key) state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
      else { state.sortKey = key; state.sortDir = "asc"; }
      render();
    });
    th.append(btn);
    tr.append(th);
  }
}

function renderRow(r) {
  const tr = el("tr");
  if (r.status !== "open") tr.className = "closed";
  tr.append(el("td", r.firm_name));
  const titleCell = el("td");
  const href = safeUrl(r.url);
  if (href) {
    const a = el("a", r.title, { href, rel: "noopener noreferrer", target: "_blank" });
    titleCell.append(a);
  } else {
    titleCell.textContent = r.title;
  }
  if (r.status !== "open") titleCell.append(" ", el("span", "Closed", { class: "tag" }));
  tr.append(titleCell);
  tr.append(el("td", r.city || ""));
  tr.append(el("td", (r.countries || []).map(countryName).join(", ")));
  tr.append(el("td", label(SECTORS, r.sector)));
  tr.append(el("td", label(SEASONS, r.season) + (r.year ? " " + r.year : "")));
  const langs = r.languages_required || [];
  tr.append(el("td", langs.length ? langs.join(", ") : "Not stated", langs.length ? undefined : { class: "muted" }));
  const closes = formatDate(r.closes);
  const c = el("td", closes || "Rolling/unknown", closes ? undefined : { class: "muted" });
  if (r.closes) c.setAttribute("data-sort", r.closes);
  tr.append(c);
  tr.append(el("td", formatDate(r.last_checked) || ""));
  return tr;
}

function render() {
  const filters = {
    countries: [...state.selected.countries], sectors: [...state.selected.sectors],
    seasons: [...state.selected.seasons], languages: [...state.selected.languages],
    openOnly: $("#open-only").checked,
  };
  for (const name of Object.keys(state.selected)) {
    const n = state.selected[name].size;
    $(`details[data-filter="${name}"] .count`).textContent = n ? `(${n})` : "";
  }
  const rows = sortRoles(visibleRoles(state.roles, filters), state.sortKey, state.sortDir);
  renderHead();
  const body = $("#roles tbody");
  body.replaceChildren(...rows.map(renderRow));
  $("#result-count").textContent = `${rows.length} of ${state.roles.length} roles`;
  $("#message").textContent = rows.length ? "" : "No roles match these filters.";
  $("#table-wrap").hidden = rows.length === 0;
}

function reset() {
  for (const s of Object.values(state.selected)) s.clear();
  document.querySelectorAll(".options input").forEach((i) => { i.checked = false; });
  $("#open-only").checked = true;
  render();
}

async function main() {
  let data;
  try {
    const resp = await fetch("data/roles.json", { cache: "no-cache" });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    data = await resp.json();
    if (!data || !Array.isArray(data.roles)) throw new Error("bad shape");
  } catch {
    $("#status").textContent = "The data could not be loaded. Please try again later.";
    $("#filters").hidden = true;
    $("#table-wrap").hidden = true;
    return;
  }
  state.roles = data.roles;
  renderStatus(data);
  buildFilters();
  $("#open-only").addEventListener("change", render);
  $("#reset").addEventListener("click", reset);
  render();
}
main();
