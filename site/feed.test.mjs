import { test } from "node:test";
import assert from "node:assert/strict";
import { buildFeed, fingerprint, materialChanges } from "./feed.js";
import { emptyPrefs, evaluate } from "./match.js";

const TODAY = "2026-11-09";
const role = (fn, extra = {}) => ({ function: fn, functions: fn === "unknown" ? [] : [fn], confidence: fn === "unknown" ? "none" : "high", method: "title", ...extra });
const opp = (id, o = {}) => ({
  id, title: id, firm_id: "bank", firm_name: "Bank", countries: ["CH"], city: "Zurich", internship_type: "summer",
  intake_year: 2027, status: "open", lifecycle: "open", languages_required: [], first_seen_at: "2026-09-01T08:00:00Z",
  closes_on: null, role: role("m_and_a"), ...o,
});
const target = (id, prefs) => ({ id, created: "2026-11-09T09:00:00Z", prefsVersion: 1, prefs: { ...emptyPrefs(), ...prefs } });
const IB = target("ib", { roles: { m_and_a: "core", investment_banking: "core", corporate_finance: "ok" }, countries: ["CH"] });
const AM = target("am", { roles: { asset_management: "core" }, countries: ["CH", "DE"] });
const employers = new Map([["bank", { id: "bank", sectors: ["investment_banking"] }], ["am", { id: "am", sectors: ["asset_management"] }]]);

function feed(opps, t = IB, o = {}) {
  return buildFeed({ opps, target: t, today: TODAY, visit: o.visit || { previous: t.created, current: t.created },
    decisions: o.decisions || {}, legacyDismissed: o.legacyDismissed || new Set(), trackerEntry: o.trackerEntry || (() => null),
    programs: o.programs || [], ctxFor: (x) => ({ employers, eligibility: o.elig?.[x.id] || "eligible" }) });
}
const ids = (xs) => xs.map((x) => x.opp.id);

const BASE = [
  opp("mna"), opp("cf", { role: role("corporate_finance") }), opp("tech", { role: role("technology") }),
  opp("pm", { role: role("asset_management"), firm_id: "am" }), opp("unk", { role: role("unknown") }),
  opp("ecm", { role: role("ecm") }), opp("de", { countries: ["DE"], city: "Frankfurt" }),
];

test("top matches hold strong and good matches only, best first, without ineligible or applied ones", () => {
  const f = feed([...BASE, opp("inel"), opp("applied")], IB, { elig: { inel: "ineligible" },
    trackerEntry: (id) => (id === "applied" ? { stage: "applied" } : null) });
  assert.deepEqual(ids(f.sections.top), ["mna", "cf", "de"]);
  assert.ok(!ids(f.sections.top).includes("inel") && !ids(f.sections.top).includes("applied"));
  assert.equal(f.counts.top, 3);
});

test("saved postings stay in their sections, marked; applied ones leave the queue but stay listed", () => {
  const f = feed(BASE, IB, { decisions: { mna: { state: "saved", at: "x" } }, trackerEntry: (id) => (id === "cf" ? { stage: "interview" } : null) });
  const mna = f.sections.top.find((x) => x.opp.id === "mna");
  assert.equal(mna.state, "saved");
  assert.ok(!ids(f.sections.top).includes("cf"));
  assert.deepEqual(ids(f.sections.saved).sort(), ["cf", "mna"]);
  assert.equal(f.sections.saved.find((x) => x.opp.id === "cf").applied, "interview");
});

test("newly found means first found by the tracker since the last visit or in the last 7 days — not last verified", () => {
  const recent = opp("recent", { first_seen_at: "2026-11-06T10:00:00Z", last_verified_at: "2026-11-09T05:00:00Z" });
  const reverified = opp("old", { first_seen_at: "2026-08-01T10:00:00Z", last_verified_at: "2026-11-09T05:00:00Z" });
  const offTarget = opp("newtech", { first_seen_at: "2026-11-08T10:00:00Z", role: role("technology") });
  const f = feed([recent, reverified, offTarget], IB);
  assert.deepEqual(ids(f.sections.new), ["recent"]);
  const longAway = feed([opp("r2", { first_seen_at: "2026-10-20T10:00:00Z" })], IB,
    { visit: { previous: "2026-10-15T09:00:00Z", current: TODAY } });
  assert.deepEqual(ids(longAway.sections.new), ["r2"]);
  assert.equal(longAway.sections.new[0].newSinceVisit, true);
});

test("closing soon needs a stated date within 21 days; rolling and missing dates are not closing", () => {
  const f = feed([opp("soon", { closes_on: "2026-11-12" }), opp("sooner", { closes_on: "2026-11-10" }),
    opp("far", { closes_on: "2027-01-30" }), opp("past", { closes_on: "2026-11-01" }), opp("rolling", { closes_on: null }),
    opp("techsoon", { closes_on: "2026-11-11", role: role("technology") })]);
  assert.deepEqual(ids(f.sections.closing), ["sooner", "soon"]);
  assert.equal(f.sections.closing[0].days, 1);
});

test("programs about to open are listed apart, estimates marked as estimates", () => {
  const programs = [{ id: "p1", employer_id: "bank", name: "M&A Summer Analyst Programme", type: "summer", countries: ["CH"],
    role: role("m_and_a"), windows: [{ intake_year: 2027, kind: "estimated", opens_earliest: "2026-11-20", opens_latest: "2026-12-05" }] },
  { id: "p2", employer_id: "bank", name: "Technology Programme", type: "summer", countries: ["CH"], role: role("technology"),
    windows: [{ intake_year: 2027, kind: "confirmed", opens_on: "2026-11-15" }] }];
  const f = feed([], IB, { programs });
  assert.deepEqual(f.opening.map((x) => x.program.id), ["p1"]);
  assert.equal(f.opening[0].estimate, true);
});

test("needs review holds promising but uncertain postings, not every incomplete record", () => {
  const f = feed([...BASE, opp("unk2", { firm_id: "am", role: role("unknown") })]);
  assert.deepEqual(ids(f.sections.review), ["unk"]);
});

test("adjacent opportunities are kept apart from the primary feed", () => {
  const f = feed(BASE, IB);
  assert.deepEqual(ids(f.sections.adjacent), ["ecm"]);
  assert.ok(!ids(f.sections.top).includes("ecm"));
  assert.ok(!ids(f.sections.adjacent).includes("tech"), "unrelated functions are not adjacent");
});

test("no section lists a posting twice", () => {
  const f = feed([...BASE, ...BASE]);
  for (const xs of Object.values(f.sections)) assert.equal(new Set(ids(xs)).size, xs.length);
});

test("switching target changes the ranking from the same data", () => {
  assert.notDeepEqual(ids(feed(BASE, IB).sections.top), ids(feed(BASE, AM).sections.top));
  assert.deepEqual(ids(feed(BASE, AM).sections.top), ["pm"]);
});

test("dismissed postings leave every queue section and are listed under dismissed; old global dismissals count", () => {
  const f = feed(BASE, IB, { decisions: { mna: { state: "dismissed", at: "2026-11-09T10:00:00Z", fp: null } }, legacyDismissed: new Set(["cf"]) });
  for (const k of ["top", "new", "closing", "review", "adjacent"]) {
    assert.ok(!ids(f.sections[k]).includes("mna") && !ids(f.sections[k]).includes("cf"), k);
  }
  assert.deepEqual(ids(f.sections.dismissed).sort(), ["cf", "mna"]);
  assert.equal(f.sections.dismissed.find((x) => x.opp.id === "cf").legacy, true);
});

// --- resurfacing -----------------------------------------------------------------------------------------------

const fpOf = (o, t = IB) => fingerprint(o, evaluate(o, t, { employers, eligibility: "eligible" }), t);
const dismissedWith = (o, t = IB) => ({ [o.id]: { state: "dismissed", at: "2026-11-01T10:00:00Z", fp: fpOf(o, t) } });

test("an unchanged dismissed posting stays hidden, and so does one that was only rechecked or reworded", () => {
  const o = opp("x", { role: role("technology") });
  const d = dismissedWith(o);
  assert.equal(feed([o], IB, { decisions: d }).sections.review.length, 0);
  const trivial = { ...o, last_verified_at: "2026-11-09T05:00:00Z", description: "Reworded text", published_at: "2026-11-08",
    sources: [{ source_id: "another" }] };
  assert.equal(feed([trivial], IB, { decisions: d }).sections.review.length, 0);
});

test("a material change resurfaces a dismissal in needs review, explained, with the dismissal kept", () => {
  const o = opp("x", { role: role("technology") });
  const d = dismissedWith(o);
  const changed = { ...o, role: role("m_and_a") };
  const f = feed([changed], IB, { decisions: d });
  const item = f.sections.review.find((x) => x.opp.id === "x");
  assert.ok(item, "resurfaced into review");
  assert.equal(item.state, "dismissed");
  assert.match(item.resurfaced[0].text, /role/i);
  assert.ok(!ids(f.sections.top).includes("x"));
  assert.deepEqual(ids(f.sections.dismissed), ["x"]);
});

test("location changes resurface only when the new place is preferred", () => {
  const o = opp("x", { countries: ["FR"], city: "Paris" });
  const d = dismissedWith(o);
  assert.equal(feed([{ ...o, countries: ["CH"], city: "Geneva" }], IB, { decisions: d }).sections.review.length, 1);
  assert.equal(feed([{ ...o, countries: ["IT"], city: "Milan" }], IB, { decisions: d }).sections.review.length, 0);
});

test("a change resurfaces only where it is relevant to that target", () => {
  const o = opp("x", { role: role("technology") });
  const changed = { ...o, role: role("m_and_a") };
  const am = feed([changed], AM, { decisions: dismissedWith(o, AM) });
  assert.equal(am.sections.review.length, 0, "m&a is not relevant to the asset-management target");
});

test("materialChanges names reopenings, intake and eligibility improvements, and ignores the rest", () => {
  const a = { function: "m_and_a", countries: "CH", city: "Zurich", type: "summer", year: 2027, reopened: 0, eligibility: "insufficient", category: "top", prefsVersion: 1 };
  assert.deepEqual(materialChanges(a, { ...a }, IB.prefs), []);
  const codes = (b) => materialChanges(a, b, IB.prefs).map((c) => c.code);
  assert.deepEqual(codes({ ...a, reopened: 1 }), ["reopened"]);
  assert.deepEqual(codes({ ...a, year: 2028 }), [], "the target sets no intake years");
  assert.deepEqual(materialChanges(a, { ...a, year: 2028 }, { ...IB.prefs, years: [2027] }).map((c) => c.code), ["timing"]);
  assert.deepEqual(codes({ ...a, eligibility: "eligible" }), ["eligibility"]);
  assert.deepEqual(codes({ ...a, eligibility: "ineligible" }), []);
  assert.deepEqual(codes({ ...a, category: "top", prefsVersion: 2 }), []);
  assert.deepEqual(codes({ ...a, category: "low" }), []);
  const low = { ...a, category: "low" };
  assert.deepEqual(materialChanges(low, { ...low, category: "top", prefsVersion: 2 }, IB.prefs).map((c) => c.code), ["preferences"]);
});

test("restoring an old global dismissal in one target keeps it dismissed in the others", () => {
  const legacyDismissed = new Set(["mna"]);
  const restored = feed(BASE, IB, { legacyDismissed, decisions: { mna: { state: "restored", at: "x" } } });
  assert.ok(ids(restored.sections.top).includes("mna"));
  assert.equal(restored.sections.top.find((x) => x.opp.id === "mna").state, null);
  assert.ok(!ids(feed(BASE, IB, { legacyDismissed }).sections.top).includes("mna"));
});

test("enrichment filling unknown fields is not a material change", () => {
  const unknownFp = { function: "unknown", countries: "CH", city: "Zurich", type: "unknown", year: null, reopened: 0, eligibility: "eligible", category: "review", prefsVersion: 1 };
  const codes = (b, prefs = IB.prefs) => materialChanges(unknownFp, { ...unknownFp, ...b }, prefs).map((c) => c.code);
  assert.deepEqual(codes({ function: "technology", category: "low" }), []);
  assert.deepEqual(codes({ function: "m_and_a", category: "top" }), ["role"], "unknown -> a chosen function matters");
  assert.deepEqual(codes({ type: "summer", year: 2027 }), [], "the target sets no formats or years");
  const withTypes = { ...IB.prefs, types: ["summer"] };
  assert.deepEqual(codes({ type: "summer" }, withTypes), ["timing"]);
  assert.deepEqual(codes({ type: "insight" }, withTypes), []);
});

test("only dismissals from before the first target are legacy", async () => {
  const { legacyIds } = await import("./feed.js");
  const all = { a: "2026-11-01T10:00:00Z", b: "2026-11-10T10:00:00Z" };
  assert.deepEqual([...legacyIds(all, "2026-11-09T09:00:00Z")], ["a"]);
  assert.deepEqual([...legacyIds(all, null)].sort(), ["a", "b"]);
});
