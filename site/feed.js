// The "For you" feed of one career target: one evaluation of every posting (match.js), then section views over
// it. Sections are queries, not separate rankings, so a posting reads the same everywhere and a decision taken in
// one section is reflected in all of them. Pure; runs under node --test.
//
//   top       top and good matches, not applied, not ineligible
//   new       relevant postings first found by the tracker since your previous visit to this target, or in the last
//             7 days if that is longer. First discovery only: re-verification never makes a posting new.
//   closing   relevant postings whose stated closing date is 0–21 days away. Rolling or missing dates never count.
//   review    promising but uncertain postings, and dismissed ones that changed materially since you dismissed them
//   adjacent  related functions or weaker fits, kept out of the primary sections
//   saved     saved in this target, or tracked in the pipeline (applications are global)
//   dismissed this target's dismissals, and the old global "not interested" list
// plus `opening`: recurring programs whose next window opens within 45 days (estimates marked).
//
// A posting can be in several sections (a top match closing soon) but never twice in one.

import { evaluate, compareMatches, CATEGORIES } from "./match.js";
import { daysUntil, programStatus, stageById } from "./filters.js";

export const SECTIONS = {
  top: "Top matches", new: "Newly found", closing: "Closing soon", review: "Needs review", adjacent: "Adjacent",
  saved: "Saved & applied", dismissed: "Dismissed",
};
const RELEVANT = new Set(["top", "good", "adjacent", "review"]);
const CATEGORY_RANK = { excluded: 0, low: 1, adjacent: 2, review: 2, good: 3, top: 4 };
const ELIG_RANK = { ineligible: 0, language_gap: 1, insufficient: 2, potential: 2, sponsorship: 3, eligible: 4 };
const CLOSING_DAYS = 21;
const NEW_DAYS = 7;
const OPENING_DAYS = 45;

// The attributes whose change can make a dismissed posting worth another look, as they were when dismissed.
export function fingerprint(opp, match, target) {
  return { function: match.role.function, countries: [...(opp.countries || [])].sort().join(","), city: opp.city || "",
    type: opp.internship_type || "unknown", year: opp.intake_year || null, reopened: opp.reopened || 0,
    eligibility: match.eligibility, category: match.category, prefsVersion: target?.prefsVersion || 0 };
}

// What changed between two fingerprints that matters to this target. Rechecks, rewording, new sources and dates
// moving are deliberately not here.
export function materialChanges(before, after, prefs = {}) {
  if (!before) return [];
  const out = [];
  if (before.function !== after.function) {
    out.push({ code: "role", text: `Role classification changed (${before.function.replaceAll("_", " ")} → ${after.function.replaceAll("_", " ")})` });
  }
  if (before.countries !== after.countries || before.city !== after.city) {
    const cs = after.countries ? after.countries.split(",") : [];
    const preferred = cs.some((c) => (prefs.countries || []).includes(c))
      || (prefs.cities || []).some((c) => after.city && after.city.toLowerCase().includes(c.toLowerCase()));
    if (preferred) out.push({ code: "location", text: `Location changed to ${after.city || after.countries}, a place you prefer` });
  }
  if (before.type !== after.type || before.year !== after.year) {
    out.push({ code: "timing", text: `Intake changed (${before.type}${before.year ? " " + before.year : ""} → ${after.type}${after.year ? " " + after.year : ""})` });
  }
  if (after.reopened > before.reopened) out.push({ code: "reopened", text: "Applications reopened" });
  if ((ELIG_RANK[after.eligibility] ?? 2) >= 3 && (ELIG_RANK[before.eligibility] ?? 2) < 3) {
    out.push({ code: "eligibility", text: "Eligibility now looks favourable" });
  }
  if (after.prefsVersion !== before.prefsVersion && CATEGORY_RANK[after.category] >= 3 && CATEGORY_RANK[before.category] <= 2) {
    out.push({ code: "preferences", text: "A stronger match after your preference changes" });
  }
  return out;
}

const processedStage = (entry) => { const s = entry && stageById(entry.stage); return !!s && (s.rank >= 1 || !!s.closed); };

// Order inside Top matches: relevance first, then whether you can apply, then urgency. Stated, not learned.
function topKey(item) {
  const m = item.match;
  const elig = { eligible: 3, sponsorship: 2, language_gap: -3 }[m.eligibility] || 0;
  const urgent = item.days !== null && item.days >= 0 && item.days <= 14 ? 2 : 0;
  return m.score + elig + urgent;
}
const byTop = (a, b) => topKey(b) - topKey(a) || compareMatches(a.match, b.match);
const byMatch = (a, b) => compareMatches(a.match, b.match);
const laterLast = (cmp) => (a, b) => (a.state === "later") - (b.state === "later") || cmp(a, b);

// opps: role objects; target: the active target; ctxFor(opp) -> evaluate() context (employers, eligibility,
// override, behaviour); decisions: this target's decisions; legacyDismissed: Set of ids from the old global list;
// trackerEntry(id) -> pipeline entry or null; visit: { previous, current }; programs: market programs.
export function buildFeed({ opps, target, ctxFor = () => ({}), decisions = {}, legacyDismissed = new Set(),
  trackerEntry = () => null, visit = null, today, programs = [] }) {
  const items = new Map();
  for (const opp of opps) {
    if (items.has(opp.id)) continue;
    const match = evaluate(opp, target, ctxFor(opp));
    const d = decisions[opp.id] || null;
    const legacy = !d && legacyDismissed.has(opp.id);
    const entry = trackerEntry(opp.id);
    const state = d?.state || (legacy ? "dismissed" : null);
    const days = opp.closes_on ? daysUntil(opp.closes_on, today) : null;
    const item = { opp, match, decision: d, state, legacy, entry, applied: processedStage(entry) ? entry.stage : null,
      days, resurfaced: null, newSinceVisit: false };
    if (state === "dismissed" && d?.fp && RELEVANT.has(match.category)) {
      const changes = materialChanges(d.fp, fingerprint(opp, match, target), target.prefs);
      if (changes.length) item.resurfaced = changes;
    }
    items.set(opp.id, item);
  }

  const live = [...items.values()].filter((x) => x.opp.status === "open" && !x.opp.excluded_reason && x.match.category !== "excluded");
  const queue = live.filter((x) => !x.applied && x.state !== "dismissed");
  const sinceMs = Math.min(Date.parse(`${today}T00:00:00Z`) - NEW_DAYS * 86_400_000,
    visit?.previous ? Date.parse(visit.previous) : Infinity);
  const prevMs = visit?.previous ? Date.parse(visit.previous) : null;
  for (const x of queue) {
    const seen = Date.parse(x.opp.first_seen_at || "");
    x.firstSeenMs = isNaN(seen) ? null : seen;
    x.newSinceVisit = prevMs !== null && x.firstSeenMs !== null && x.firstSeenMs > prevMs;
  }

  const sections = {
    top: queue.filter((x) => ["top", "good"].includes(x.match.category) && x.match.eligibility !== "ineligible").sort(laterLast(byTop)),
    new: queue.filter((x) => RELEVANT.has(x.match.category) && x.firstSeenMs !== null && x.firstSeenMs >= sinceMs)
      .sort((a, b) => b.firstSeenMs - a.firstSeenMs || byMatch(a, b)),
    closing: queue.filter((x) => RELEVANT.has(x.match.category) && x.days !== null && x.days >= 0 && x.days <= CLOSING_DAYS)
      .sort((a, b) => a.days - b.days || byMatch(a, b)),
    review: [...live.filter((x) => x.resurfaced && !x.applied),
      ...queue.filter((x) => x.match.category === "review").sort(laterLast(byMatch))],
    adjacent: queue.filter((x) => x.match.category === "adjacent").sort(laterLast(byMatch)),
    saved: [...items.values()].filter((x) => x.state === "saved" || x.entry)
      .sort((a, b) => (!!a.applied - !!b.applied) || byMatch(a, b)),
    dismissed: [...items.values()].filter((x) => x.state === "dismissed")
      .sort((a, b) => String(b.decision?.at || "").localeCompare(String(a.decision?.at || "")) || byMatch(a, b)),
  };

  const opening = [];
  for (const p of programs) {
    const st = programStatus(p, today);
    if (!["confirmed", "estimated"].includes(st.key) || !st.window) continue;
    const date = st.key === "confirmed" ? st.window.opens_on : st.window.opens_earliest;
    const days = daysUntil(date, today);
    if (days === null || days > OPENING_DAYS || (st.key === "confirmed" && days < 0)) continue;
    const pseudo = { id: `program:${p.id}`, title: p.name, firm_id: p.employer_id, firm_name: p.employer_name || p.employer_id,
      countries: p.countries || [], internship_type: p.type, intake_year: st.window.intake_year, status: "open", lifecycle: "open",
      role: p.role };
    const match = evaluate(pseudo, target, { ...ctxFor(pseudo), eligibility: "insufficient" });
    if (!["top", "good", "review"].includes(match.category)) continue;
    opening.push({ program: p, match, date, days, estimate: st.key === "estimated", window: st.window });
  }
  opening.sort((a, b) => a.date.localeCompare(b.date) || byMatch(a, b));

  const counts = Object.fromEntries(Object.entries(sections).map(([k, xs]) => [k, xs.length]));
  return { sections, opening, counts, items, categories: CATEGORIES };
}
