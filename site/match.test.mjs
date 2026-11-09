import { test } from "node:test";
import assert from "node:assert/strict";
import { evaluate, compareMatches, FUNCTIONS, emptyPrefs, MATCH_VERSION } from "./match.js";

const role = (fn, extra = {}) => ({ function: fn, functions: [fn], confidence: "high", method: "title", evidence: fn, ...extra });
const opp = (o = {}) => ({
  id: "x", title: "Intern", firm_id: "bank", firm_name: "Big Bank", countries: ["CH"], city: "Zurich",
  internship_type: "summer", intake_year: 2027, status: "open", lifecycle: "open", languages_required: [],
  role: role("m_and_a"), ...o,
});
const target = (prefs = {}) => ({ id: "t1", prefsVersion: 1, prefs: { ...emptyPrefs(), ...prefs } });
const ib = target({ roles: { m_and_a: "core", investment_banking: "core", ecm: "ok" }, countries: ["CH"], types: ["summer"] });
const employers = new Map([["bank", { id: "bank", sectors: ["investment_banking"] }], ["pe", { id: "pe", sectors: ["private_equity"] }]]);
const ev = (o, t = ib, ctx = {}) => evaluate(o, t, { employers, eligibility: "eligible", ...ctx });

test("a strong finance function match in a preferred place is a top match with reasons", () => {
  const m = ev(opp());
  assert.equal(m.category, "top");
  assert.ok(m.reasons.some((r) => r.code === "role_core" && r.tone === "+"));
  assert.ok(m.reasons.some((r) => r.code === "geo_country"));
  assert.equal(m.version, MATCH_VERSION);
});

test("an engineering role at a bank does not outrank an investment role there", () => {
  const swe = ev(opp({ id: "swe", role: role("technology", { functions: ["technology", "investment_banking"] }) }));
  const mna = ev(opp({ id: "mna" }));
  assert.ok(swe.score < mna.score);
  assert.equal(swe.category, "low");
  assert.ok(swe.reasons.some((r) => r.code === "role_outside"));
});

test("HR at a preferred employer stays a weak match", () => {
  const t = target({ ...ib.prefs, employers: { prefer: ["bank"], exclude: [] } });
  const m = ev(opp({ role: role("hr") }), t);
  assert.ok(["low", "adjacent"].includes(m.category));
  assert.ok(m.reasons.some((r) => r.code === "employer_preferred"));
});

test("a strong role in a less-preferred location is good, below the same role in a preferred one", () => {
  const away = ev(opp({ id: "a", countries: ["DE"], city: "Frankfurt" }));
  const home = ev(opp({ id: "h" }));
  assert.equal(away.category, "good");
  assert.ok(away.score < home.score);
  assert.ok(away.reasons.some((r) => r.code === "geo_outside" && r.tone === "-"));
});

test("an unknown role is never a top match; a firm working in the target makes it worth reviewing", () => {
  const atBank = ev(opp({ role: role("unknown", { functions: [], confidence: "none", method: "none" }) }));
  assert.equal(atBank.category, "review");
  assert.ok(atBank.uncertain.includes("role_unknown"));
  assert.ok(atBank.reasons.some((r) => r.code === "role_unknown_firm_hint" && /firm/.test(r.text)));
  const elsewhere = ev(opp({ firm_id: "pe", role: role("unknown", { functions: [], confidence: "none", method: "none" }) }));
  assert.equal(elsewhere.category, "low");
  const missing = ev(opp({ firm_id: "pe", role: null }));
  assert.ok(missing.uncertain.includes("role_unknown"));
});

test("unknown is neutral, not zero: an unclassified role scores above a known unrelated one", () => {
  const unknown = ev(opp({ firm_id: "pe", role: role("unknown", { functions: [], confidence: "none" }) }));
  const hr = ev(opp({ firm_id: "pe", role: role("hr") }));
  assert.ok(unknown.score > hr.score);
});

test("secondary finance functions count, but not under a non-finance primary", () => {
  const sec = ev(opp({ role: role("corporate_finance", { functions: ["corporate_finance", "m_and_a"] }) }));
  assert.ok(["good", "top"].includes(sec.category));
  const tech = ev(opp({ role: role("technology", { functions: ["technology", "m_and_a"] }) }));
  assert.equal(tech.category, "low");
});

test("hard constraints exclude before ranking; missing data never fails them", () => {
  const t = target({ ...ib.prefs, roles: { ...ib.prefs.roles, hr: "exclude" }, countriesHard: true });
  assert.equal(ev(opp({ role: role("hr") }), t).category, "excluded");
  const de = ev(opp({ countries: ["DE"] }), t);
  assert.equal(de.category, "excluded");
  assert.ok(de.hard.some((x) => x.code === "hard_country"));
  const unknownPlace = ev(opp({ countries: [], city: null }), t);
  assert.notEqual(unknownPlace.category, "excluded");
  assert.ok(unknownPlace.uncertain.includes("location_unknown"));
  assert.equal(unknownPlace.category, "review");
  const years = target({ ...ib.prefs, years: [2027], yearsHard: true });
  assert.equal(ev(opp({ intake_year: 2028 }), years).category, "excluded");
  assert.notEqual(ev(opp({ intake_year: null }), years).category, "excluded");
});

test("an excluded employer wins over a perfect role (conflicting attributes)", () => {
  const t = target({ ...ib.prefs, employers: { prefer: [], exclude: ["bank"] } });
  assert.equal(ev(opp(), t).category, "excluded");
});

test("uncertain eligibility is reported separately and does not change relevance", () => {
  const sure = ev(opp(), ib, { eligibility: "eligible" });
  const unsure = ev(opp(), ib, { eligibility: "insufficient" });
  assert.equal(sure.score, unsure.score);
  assert.equal(unsure.category, "top");
  assert.equal(unsure.eligibility, "insufficient");
  assert.ok(unsure.uncertain.includes("eligibility_unknown"));
});

test("missing intake dates are neutral", () => {
  const t = target({ ...ib.prefs, years: [2027] });
  const stated = ev(opp(), t), missing = ev(opp({ intake_year: null, internship_type: "unknown" }), t);
  const wrong = ev(opp({ intake_year: 2029, internship_type: "insight" }), t);
  assert.ok(missing.score < stated.score && missing.score > wrong.score);
});

test("a low-confidence classification is pulled toward neutral and flagged", () => {
  const low = ev(opp({ role: role("m_and_a", { confidence: "low", method: "description" }) }));
  assert.ok(low.uncertain.includes("role_low_confidence"));
  assert.notEqual(low.category, "top");
});

test("a user correction replaces the classification without touching the source record", () => {
  const o = opp({ role: role("hr") });
  const m = ev(o, ib, { override: "m_and_a" });
  assert.equal(m.role.function, "m_and_a");
  assert.equal(m.role.corrected, true);
  assert.equal(o.role.function, "hr");
});

test("any-finance targets accept every finance function but not technology", () => {
  const t = target({ anyFinance: true });
  assert.ok(["top", "good"].includes(ev(opp({ role: role("treasury") }), t).category));
  assert.equal(ev(opp({ role: role("technology") }), t).category, "low");
});

test("bounded behaviour nudges order but never crosses a category", () => {
  const plain = ev(opp());
  const liked = ev(opp(), ib, { behaviour: { m_and_a: 1 } });
  const disliked = ev(opp(), ib, { behaviour: { m_and_a: -1 } });
  assert.ok(liked.score > plain.score && disliked.score < plain.score);
  assert.equal(disliked.category, plain.category);
});

test("ranking is deterministic and follows profile changes", () => {
  const os = [opp({ id: "b", role: role("ecm") }), opp({ id: "a" }), opp({ id: "c", role: role("hr") })];
  const order = (t) => os.map((o) => ev(o, t)).sort(compareMatches).map((m) => m.id);
  assert.deepEqual(order(ib), ["a", "b", "c"]);
  assert.deepEqual(order(ib), order(ib));
  const ecmFirst = target({ ...ib.prefs, roles: { ecm: "core", m_and_a: "low" } });
  assert.deepEqual(order(ecmFirst), ["b", "a", "c"]);
});

test("the taxonomy marks business functions as non-finance", () => {
  assert.equal(FUNCTIONS.technology.finance, false);
  assert.equal(FUNCTIONS.private_equity.finance, true);
});
