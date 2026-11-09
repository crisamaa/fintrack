// Learning from your decisions, without ever changing a preference on its own. Pure; runs under node --test.
//
// detect() looks at one target's history and proposes changes only when the evidence is explicit and independent:
//   - a dismissal reason that names the kind of mismatch (wrong field, location, timing, employer). A dismissal
//     without a reason is not evidence about the field.
//   - at least 3 postings across at least 2 different firms (many vacancies at one firm are one observation);
//     employers need 2 postings at that firm.
//   - only decisions still in force: undone or reversed dismissals do not count.
// A suggestion is applied only when you approve it, as a normal preference revision that can be undone. "Not now"
// hides it for 3 days; rejecting it keeps it away until there are 2 more pieces of evidence and 14 days have passed.
//
// behaviour() is the only thing used without approval: a bounded nudge in [-1, 1] per function from repeated saves
// and dismissals, worth 5% of the score, which the matching engine never lets change a category.

import { FUNCTIONS, functionLabel, levelOf } from "./match.js";

const DAY = 86_400_000;
const ROLE_REASONS = new Set(["wrong_field", "uninteresting_role", "too_technical", "too_operational"]);
const DEFER_DAYS = 3;
const REJECT_DAYS = 14;
const REJECT_MORE = 2;
const TYPE_NAMES = { summer: "summer internships", off_cycle: "off-cycle internships", spring: "spring internships",
  winter: "winter internships", insight: "insight weeks", working_student: "working-student roles",
  part_time: "part-time internships", graduate: "graduate programmes" };

// The last decision event in force for each posting of this target.
function standing(events, decisions) {
  const out = new Map();
  for (const e of events) {
    if (e.reversed || e.action === "undo") continue;
    const d = decisions[e.opp];
    if (!d || d.event !== e.id) continue;
    out.set(e.opp, { ...e, reason: d.reason ?? e.reason, state: d.state });
  }
  // Applications are not decisions in this store (they live in the pipeline) but are a strong positive signal.
  for (const e of events) {
    if (e.action === "applied" && !e.reversed && !out.has(e.opp)) out.set(e.opp, { ...e, state: "applied" });
  }
  return [...out.values()];
}

function group(rows, keyOf) {
  const g = new Map();
  for (const r of rows) {
    const k = keyOf(r);
    if (k === null || k === undefined) continue;
    if (!g.has(k)) g.set(k, []);
    g.get(k).push(r);
  }
  return g;
}

// Candidate suggestions from the evidence, before cooldowns.
function candidates(target, rows, opps, fnOf) {
  const name = target.name;
  const prefs = target.prefs;
  const out = [];
  const firms = (xs) => new Set(xs.map((r) => opps.get(r.opp)?.firm_id).filter(Boolean)).size;
  const dismissed = rows.filter((r) => r.state === "dismissed");
  const enough = (xs) => xs.length >= 3 && firms(xs) >= 2;

  for (const [fn, xs] of group(dismissed.filter((r) => ROLE_REASONS.has(r.reason)), (r) => fnOf(r.opp))) {
    if (!FUNCTIONS[fn] || !enough(xs) || ["low", "exclude"].includes(prefs.roles?.[fn])) continue;
    const label = functionLabel(fn);
    out.push({ key: `role:${fn}:low`, kind: "role", value: fn, change: "low", evidence: xs.map((r) => r.opp), employers: firms(xs),
      text: `You dismissed ${xs.length} ${label} internships at ${firms(xs)} firms as outside your interests. Deprioritise ${label} in “${name}”?`,
      effect: `${label} moves to Deprioritised in this target. Other targets are not affected.` });
  }
  for (const [cc, xs] of group(dismissed.filter((r) => r.reason === "location"), (r) => (opps.get(r.opp)?.countries || [])[0])) {
    if (!enough(xs) || (prefs.lowCountries || []).includes(cc)) continue;
    out.push({ key: `location:${cc}:low`, kind: "location", value: cc, change: "low", evidence: xs.map((r) => r.opp), employers: firms(xs),
      text: `You dismissed ${xs.length} internships in ${cc} at ${firms(xs)} firms for their location. Deprioritise ${cc} in “${name}”?`,
      effect: `${cc} is ranked lower in this target${(prefs.countries || []).includes(cc) ? " and removed from its preferred countries" : ""}.` });
  }
  for (const [type, xs] of group(dismissed.filter((r) => r.reason === "timing"), (r) => {
    const t = opps.get(r.opp)?.internship_type; return t && t !== "unknown" ? t : null; })) {
    if (!enough(xs) || (prefs.lowTypes || []).includes(type)) continue;
    const label = TYPE_NAMES[type] || type;
    out.push({ key: `timing:${type}:low`, kind: "timing", value: type, change: "low", evidence: xs.map((r) => r.opp), employers: firms(xs),
      text: `You dismissed ${xs.length} ${label} for their timing. Rank ${label} lower in “${name}”?`,
      effect: `${label[0].toUpperCase() + label.slice(1)} are ranked lower in this target.` });
  }
  for (const [firm, xs] of group(dismissed.filter((r) => r.reason === "employer"), (r) => opps.get(r.opp)?.firm_id)) {
    if (xs.length < 2 || (prefs.employers?.exclude || []).includes(firm)) continue;
    const fname = opps.get(xs[0].opp)?.firm_name || firm;
    out.push({ key: `employer:${firm}:exclude`, kind: "employer", value: firm, change: "exclude", evidence: xs.map((r) => r.opp), employers: 1,
      text: `You dismissed ${xs.length} postings at ${fname} as an uninteresting employer. Exclude ${fname} from “${name}”?`,
      effect: `${fname} no longer appears in this target's feed. It stays in All opportunities.` });
  }
  const positive = rows.filter((r) => r.state === "saved" || r.action === "applied");
  for (const [fn, xs] of group(positive, (r) => fnOf(r.opp))) {
    if (!FUNCTIONS[fn] || !enough(xs) || levelOf(prefs, fn)) continue;
    const label = functionLabel(fn);
    out.push({ key: `role:${fn}:ok`, kind: "role", value: fn, change: "ok", evidence: xs.map((r) => r.opp), employers: firms(xs),
      text: `You saved ${xs.length} ${label} internships at ${firms(xs)} firms. Add ${label} as an acceptable function in “${name}”?`,
      effect: `${label} counts as Acceptable in this target.` });
  }
  return out;
}

// Merge fresh candidates with the stored list: keep statuses, apply cooldowns, drop pending ones whose condition no
// longer holds (for example because you changed the preference yourself).
export function detect({ target, events, decisions, opps, fnOf, existing = [], now = Date.now() }) {
  const rows = standing(events, decisions);
  const fresh = new Map(candidates(target, rows, opps, fnOf).map((c) => [c.key, c]));
  const out = [];
  const seen = new Set();
  for (const s of existing) {
    seen.add(s.key);
    const c = fresh.get(s.key);
    if (s.status === "pending") { if (c) out.push({ ...s, ...c, id: s.id, status: "pending" }); continue; }
    if (s.status === "deferred" && c && now - Date.parse(s.resolved) >= DEFER_DAYS * DAY) { out.push({ ...s, ...c, status: "pending" }); continue; }
    if ((s.status === "rejected" || s.status === "reverted") && c && c.evidence.length >= (s.evidence?.length || 0) + REJECT_MORE
      && now - Date.parse(s.resolved) >= REJECT_DAYS * DAY) {
      out.push({ ...s, ...c, status: "pending", previously: s.status });
      continue;
    }
    out.push(s);
  }
  for (const [key, c] of fresh) {
    if (seen.has(key)) continue;
    out.push({ ...c, id: `${key}:${new Date(now).toISOString()}`, status: "pending", created: new Date(now).toISOString() });
  }
  return out;
}

export function resolve(list, id, status, now = Date.now(), extra = {}) {
  return list.map((s) => (s.id === id ? { ...s, status, resolved: new Date(now).toISOString(), ...extra } : s));
}

// The preference change a suggestion proposes, as plain data; the caller saves it through targets.setPrefs.
export function applySuggestion(prefs, s) {
  const p = structuredClone(prefs);
  if (s.kind === "role") p.roles = { ...(p.roles || {}), [s.value]: s.change };
  if (s.kind === "location") {
    p.countries = (p.countries || []).filter((c) => c !== s.value);
    p.lowCountries = [...new Set([...(p.lowCountries || []), s.value])];
    if (!p.countries.length) p.countriesHard = false;
  }
  if (s.kind === "timing") {
    p.types = (p.types || []).filter((t) => t !== s.value);
    p.lowTypes = [...new Set([...(p.lowTypes || []), s.value])];
    if (!p.types.length) p.typesHard = false;
  }
  if (s.kind === "employer") {
    p.employers = { prefer: (p.employers?.prefer || []).filter((e) => e !== s.value),
      exclude: [...new Set([...(p.employers?.exclude || []), s.value])] };
  }
  return p;
}

// Per function, (saves and applications − dismissals) / (all + 2), only with 2+ decisions: always strictly inside
// (-1, 1) and close to 0 until there is a clear habit.
export function behaviour(events, decisions, fnOf) {
  const tally = new Map();
  for (const r of standing(events, decisions)) {
    const fn = fnOf(r.opp);
    if (!FUNCTIONS[fn]) continue;
    const t = tally.get(fn) || { pos: 0, neg: 0 };
    if (r.state === "saved" || r.action === "applied") t.pos += 1;
    else if (r.state === "dismissed") t.neg += 1;
    tally.set(fn, t);
  }
  const out = {};
  for (const [fn, { pos, neg }] of tally) if (pos + neg >= 2) out[fn] = (pos - neg) / (pos + neg + 2);
  return out;
}
