// Career targets: several independent search strategies, each with its own preferences, decisions, review history
// and suggestions. Personal, so it lives in this browser's localStorage only (key fintrack.targets.v1), next to the
// applications in store.js. Applications stay global there: one real application per posting, whichever target
// you found it under.
//
// Document shape (versioned as a whole):
//   profiles     { id: { id, name, description, status: active|archived, created, updated, prefsVersion, prefs,
//                        revisions: [{ id, at, reason, suggestion, before, after }] } }
//   activeId     the target Discover opens with
//   decisions    { profileId: { oppId: { state: saved|dismissed|later, reason, at, fp, event } } }
//   events       [{ id, at, profile, opp, action, reason, fp, reverses, reversed }] — capped, newest last
//   suggestions  { profileId: [{ id, key, ... , status: pending|accepted|rejected|deferred|reverted }] }
//   visits       { profileId: { previous, current } }
//   overrides    { oppId: function }   your role corrections; the tracker's classification is never rewritten
//   draft        onboarding progress, cleared when the target is activated

import { FUNCTIONS, LEVELS, emptyPrefs } from "./match.js";

export const TARGETS_KEY = "fintrack.targets.v1";
export const DECISIONS = ["saved", "dismissed", "later"];
export const TYPES = ["summer", "off_cycle", "spring", "winter", "insight", "working_student", "part_time", "graduate"];
export const DISMISS_REASONS = {
  wrong_field: "Wrong finance field", uninteresting_role: "Uninteresting role", location: "Unsuitable location",
  timing: "Unsuitable timing", employer: "Uninteresting employer", too_technical: "Too technical",
  too_operational: "Too operational", eligibility: "Eligibility issue", already: "Already considered", other: "Other",
};
const MAX_EVENTS = 3000;
const MAX_REVISIONS = 50;

const empty = () => ({ version: 1, profiles: {}, activeId: null, decisions: {}, events: [], suggestions: {}, visits: {},
  overrides: {}, draft: null });

const uniq = (xs) => [...new Set(xs)];

// Normalise and check a target's preferences. Only a role interest is required.
export function validatePrefs(input) {
  const errors = [];
  const p = { ...emptyPrefs(), ...(input || {}) };
  const roles = {};
  for (const [fn, lvl] of Object.entries(p.roles || {})) {
    if (!FUNCTIONS[fn]) errors.push(`Unknown function ${fn}`);
    else if (!LEVELS[lvl]) errors.push(`Unknown level ${lvl} for ${fn}`);
    else roles[fn] = lvl;
  }
  const wanted = Object.values(roles).some((l) => l === "core" || l === "ok");
  if (!wanted && !p.anyFinance) errors.push("Choose at least one function, or Any finance function");
  const codes = (xs, label) => {
    const out = uniq((xs || []).map((c) => String(c).trim().toUpperCase()));
    if (out.some((c) => !/^[A-Z]{2}$/.test(c))) errors.push(`${label} must be two-letter country codes`);
    return out;
  };
  const countries = codes(p.countries, "Countries");
  const lowCountries = codes(p.lowCountries, "Deprioritised countries").filter((c) => !countries.includes(c));
  const types = uniq(p.types || []).filter((t) => TYPES.includes(t));
  const lowTypes = uniq(p.lowTypes || []).filter((t) => TYPES.includes(t) && !types.includes(t));
  const years = uniq((p.years || []).map(Number));
  if (years.some((y) => !Number.isInteger(y) || y < 2020 || y > 2040)) errors.push("Intake years must be between 2020 and 2040");
  if (p.countriesHard && !countries.length) errors.push("Requiring countries needs at least one country");
  if (p.typesHard && !types.length) errors.push("Requiring formats needs at least one format");
  if (p.yearsHard && !years.length) errors.push("Requiring intake years needs at least one year");
  const employers = { prefer: uniq(p.employers?.prefer || []), exclude: uniq(p.employers?.exclude || []) };
  employers.prefer = employers.prefer.filter((e) => !employers.exclude.includes(e));
  const prefs = { roles, anyFinance: !!p.anyFinance, countries, lowCountries,
    cities: uniq((p.cities || []).map((c) => String(c).trim()).filter(Boolean)), countriesHard: !!p.countriesHard && !!countries.length,
    types, lowTypes, typesHard: !!p.typesHard && !!types.length, years: years.filter((y) => y >= 2020 && y <= 2040).sort(),
    yearsHard: !!p.yearsHard && !!years.length, employers,
    employerTypes: uniq(p.employerTypes || []).filter((s) => FUNCTIONS[s]),
    languages: uniq((p.languages || []).map((l) => String(l).toLowerCase())).filter((l) => /^[a-z]{2}$/.test(l)) };
  return { ok: !errors.length, errors, prefs };
}

// The old Profile "What you are looking for" (sectors, countries, formats) as a starting point for onboarding. The
// note is shown so the user sees where the prefill came from; nothing is created without their review.
export function prefsFromLegacy(profile) {
  const lp = profile?.prefs || {};
  const sectors = (lp.sectors || []).filter((s) => FUNCTIONS[s]);
  if (!sectors.length && !(lp.countries || []).length && !(lp.types || []).length) return null;
  return {
    prefs: { ...emptyPrefs(), roles: Object.fromEntries(sectors.map((s) => [s, "core"])), anyFinance: !sectors.length,
      countries: [...(lp.countries || [])], types: (lp.types || []).filter((t) => TYPES.includes(t)) },
    note: "Prefilled from the sectors, countries and formats in your Profile. Sectors were firm and role labels; check that each is a function you want.",
  };
}

export function createTargets({ storage = globalThis.localStorage, now = () => new Date().toISOString(),
  uid = () => Math.random().toString(36).slice(2, 10) } = {}) {
  let doc = load();
  const listeners = new Set();

  function load() {
    try {
      const raw = JSON.parse(storage?.getItem(TARGETS_KEY));
      if (raw && typeof raw === "object" && raw.version === 1) return { ...empty(), ...raw };
    } catch { /* unreadable: start empty, the old value is overwritten only on the next change */ }
    return empty();
  }
  function persist(kind = "change") {
    let ok = true;
    try { storage?.setItem(TARGETS_KEY, JSON.stringify(doc)); } catch { ok = false; }
    listeners.forEach((fn) => fn(kind));
    return ok;
  }
  const must = (id) => { const p = doc.profiles[id]; if (!p) throw new Error(`No target ${id}`); return p; };
  const checkName = (name) => {
    const n = String(name || "").trim();
    if (!n) throw new Error("A target needs a name");
    return n.slice(0, 80);
  };
  const checked = (prefs) => {
    const v = validatePrefs(prefs);
    if (!v.ok) throw new Error(v.errors.join("; "));
    return v.prefs;
  };
  const firstActive = () => Object.values(doc.profiles).filter((p) => p.status === "active").sort((a, b) => a.created.localeCompare(b.created))[0]?.id || null;
  function event(profile, opp, action, extra = {}) {
    const e = { id: uid(), at: now(), profile, opp, action, ...extra };
    doc.events.push(e);
    if (doc.events.length > MAX_EVENTS) doc.events = doc.events.slice(-MAX_EVENTS);
    return e;
  }

  const api = {
    onChange: (fn) => listeners.add(fn),
    doc: () => doc,
    list: ({ archived = false } = {}) => Object.values(doc.profiles).filter((p) => (p.status === "archived") === archived)
      .sort((a, b) => a.created.localeCompare(b.created)),
    get: (id) => doc.profiles[id] || null,
    activeId: () => (doc.profiles[doc.activeId]?.status === "active" ? doc.activeId : firstActive()),
    active: () => doc.profiles[api.activeId()] || null,

    create({ name, description = "", prefs, fromDraft = false }) {
      const p = { id: uid(), name: checkName(name), description: String(description || "").slice(0, 280), status: "active",
        created: now(), prefsVersion: 1, prefs: checked(prefs), revisions: [] };
      p.updated = p.created;
      doc.profiles[p.id] = p;
      doc.activeId = p.id;
      if (fromDraft) doc.draft = null;
      persist();
      return p.id;
    },
    rename(id, name, description) {
      const p = must(id);
      p.name = checkName(name);
      if (description !== undefined) p.description = String(description || "").slice(0, 280);
      p.updated = now();
      return persist();
    },
    // Every preference change is a revision, whether typed by you or an approved suggestion, so it can be undone.
    setPrefs(id, prefs, { reason = "edited", suggestion = null } = {}) {
      const p = must(id);
      const next = checked(prefs);
      if (JSON.stringify(next) === JSON.stringify(p.prefs)) return null;
      const rev = { id: uid(), at: now(), reason, suggestion, before: p.prefs, after: next };
      p.revisions = [rev, ...(p.revisions || [])].slice(0, MAX_REVISIONS);
      p.prefs = next;
      p.prefsVersion += 1;
      p.updated = rev.at;
      persist();
      return rev;
    },
    revisions: (id) => must(id).revisions || [],
    revert(id, revId) {
      const p = must(id);
      const rev = (p.revisions || []).find((r) => r.id === revId);
      if (!rev) throw new Error("No such revision");
      return api.setPrefs(id, rev.before, { reason: "reverted", suggestion: rev.suggestion });
    },
    duplicate(id) {
      const p = must(id);
      const copy = { ...structuredClone(p), id: uid(), name: `${p.name} (copy)`.slice(0, 80), status: "active", created: now(),
        prefsVersion: 1, revisions: [] };
      copy.updated = copy.created;
      doc.profiles[copy.id] = copy;
      doc.activeId = copy.id;
      persist();
      return copy.id;
    },
    activate(id) { if (must(id).status !== "active") throw new Error("Restore the target first"); doc.activeId = id; return persist("switch"); },
    archive(id) {
      must(id).status = "archived";
      if (doc.activeId === id) doc.activeId = firstActive();
      return persist();
    },
    restore(id) { must(id).status = "active"; return persist(); },
    remove(id) {
      must(id);
      delete doc.profiles[id];
      delete doc.decisions[id];
      delete doc.suggestions[id];
      delete doc.visits[id];
      doc.events = doc.events.filter((e) => e.profile !== id);
      if (doc.activeId === id) doc.activeId = firstActive();
      return persist();
    },

    draft: () => doc.draft,
    saveDraft(d) { doc.draft = d ? structuredClone(d) : null; return persist("draft"); },

    decision: (profile, opp) => doc.decisions[profile]?.[opp] || null,
    decisions: (profile) => doc.decisions[profile] || {},
    events: (profile) => doc.events.filter((e) => e.profile === profile),
    // state: saved | dismissed | later | null (clear). `fp` is the posting's material fingerprint at this moment, used
    // to tell later whether a dismissed posting changed in a way that matters.
    decide(profile, opp, state, { reason = null, fp = null, action = null } = {}) {
      must(profile);
      if (state !== null && !DECISIONS.includes(state)) throw new Error(`Unknown decision state ${state}`);
      const prev = api.decision(profile, opp);
      const e = event(profile, opp, action || (state === null ? `un${prev?.state || "decide"}` : state), { reason, fp,
        from: prev?.state || null });
      doc.decisions[profile] ||= {};
      if (state === null) delete doc.decisions[profile][opp];
      else doc.decisions[profile][opp] = { state, reason, at: e.at, fp, event: e.id, previous: prev || null };
      persist("decision");
      return e;
    },
    // An optional reason, given after a dismissal without blocking it.
    setReason(profile, opp, reason) {
      const d = api.decision(profile, opp);
      if (!d) return false;
      d.reason = reason;
      const e = doc.events.find((x) => x.id === d.event);
      if (e) e.reason = reason;
      return persist("decision");
    },
    // Reverse one decision event: the decision returns to what it was before, and both events stay in the history.
    undo(profile, eventId) {
      const e = doc.events.find((x) => x.id === eventId && x.profile === profile);
      if (!e || e.reversed) return null;
      e.reversed = true;
      const cur = api.decision(profile, e.opp);
      doc.decisions[profile] ||= {};
      const restored = cur?.event === e.id ? cur.previous : cur;
      if (restored) doc.decisions[profile][e.opp] = restored; else delete doc.decisions[profile][e.opp];
      const u = event(profile, e.opp, "undo", { reverses: e.id });
      persist("decision");
      return u;
    },
    // Events that are not decisions on one posting (applied, restored, suggestion outcomes) for the history.
    log(profile, opp, action, extra = {}) { const e = event(profile, opp, action, extra); persist("event"); return e; },

    suggestions: (profile) => doc.suggestions[profile] || [],
    setSuggestions(profile, list) { doc.suggestions[profile] = list; return persist("suggestions"); },

    visit(profile) {
      const p = must(profile);
      const v = doc.visits[profile] || { current: p.created };
      doc.visits[profile] = { previous: v.current, current: now() };
      persist("visit");
      return doc.visits[profile];
    },
    lastVisit: (profile) => doc.visits[profile] || null,

    override: (opp) => doc.overrides[opp] || null,
    overrides: () => doc.overrides,
    setOverride(opp, fn) {
      if (fn !== null && !FUNCTIONS[fn]) throw new Error(`Unknown function ${fn}`);
      if (fn === null) delete doc.overrides[opp]; else doc.overrides[opp] = fn;
      return persist("override");
    },

    exportData: () => structuredClone(doc),
    importData(incoming) {
      if (!incoming || incoming.version !== 1) return false;
      doc.profiles = { ...doc.profiles, ...(incoming.profiles || {}) };
      for (const [pid, ds] of Object.entries(incoming.decisions || {})) doc.decisions[pid] = { ...(doc.decisions[pid] || {}), ...ds };
      const seen = new Set(doc.events.map((e) => e.id));
      doc.events = [...doc.events, ...(incoming.events || []).filter((e) => !seen.has(e.id))]
        .sort((a, b) => a.at.localeCompare(b.at)).slice(-MAX_EVENTS);
      doc.suggestions = { ...doc.suggestions, ...(incoming.suggestions || {}) };
      doc.visits = { ...doc.visits, ...(incoming.visits || {}) };
      doc.overrides = { ...doc.overrides, ...(incoming.overrides || {}) };
      if (!doc.profiles[doc.activeId]) doc.activeId = incoming.activeId || firstActive();
      return persist();
    },
  };
  return api;
}
