import { test } from "node:test";
import assert from "node:assert/strict";
import { detect, applySuggestion, behaviour, resolve } from "./learn.js";
import { createTargets } from "./targets.js";
import { emptyPrefs, evaluate } from "./match.js";

const DAY = 86_400_000;
const T0 = Date.parse("2026-11-09T12:00:00Z");
const iso = (ms) => new Date(ms).toISOString();
const memory = () => { const m = new Map(); return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => m.set(k, v) }; };
let tick = 0;
const store = () => createTargets({ storage: memory(), now: () => iso(T0 + tick++ * 1000) });

const opps = new Map([
  ["r1", { id: "r1", firm_id: "a", firm_name: "A", countries: ["DE"], internship_type: "summer", role: { function: "risk" } }],
  ["r2", { id: "r2", firm_id: "b", firm_name: "B", countries: ["DE"], internship_type: "summer", role: { function: "risk" } }],
  ["r3", { id: "r3", firm_id: "c", firm_name: "C", countries: ["DE"], internship_type: "summer", role: { function: "risk" } }],
  ["r4", { id: "r4", firm_id: "d", firm_name: "D", countries: ["DE"], internship_type: "summer", role: { function: "risk" } }],
  ["r5", { id: "r5", firm_id: "e", firm_name: "E", countries: ["DE"], internship_type: "summer", role: { function: "risk" } }],
  ["s1", { id: "s1", firm_id: "a", firm_name: "A", countries: ["CH"], role: { function: "risk" } }],
  ["s2", { id: "s2", firm_id: "a", firm_name: "A", countries: ["CH"], role: { function: "risk" } }],
  ["s3", { id: "s3", firm_id: "a", firm_name: "A", countries: ["CH"], role: { function: "risk" } }],
]);
const fnOf = (id) => opps.get(id)?.role?.function || "unknown";

function setup() {
  const t = store();
  const ib = t.create({ name: "IB — Switzerland", prefs: { roles: { m_and_a: "core" }, countries: ["CH"] } });
  const other = t.create({ name: "General finance", prefs: { anyFinance: true } });
  return { t, ib, other };
}
const run = (t, id, at = T0) => detect({ target: t.get(id), events: t.events(id), decisions: t.decisions(id), opps, fnOf,
  existing: t.suggestions(id), now: at });

test("consistent explicit dismissals across distinct firms produce one explained suggestion; prefs unchanged", () => {
  const { t, ib } = setup();
  for (const id of ["r1", "r2", "r3"]) t.decide(ib, id, "dismissed", { reason: "wrong_field" });
  const out = run(t, ib);
  const s = out.find((x) => x.status === "pending");
  assert.equal(s.key, "role:risk:low");
  assert.match(s.text, /Financial risk/);
  assert.match(s.text, /IB — Switzerland/);
  assert.equal(s.evidence.length, 3);
  assert.equal(s.employers, 3);
  assert.equal(t.get(ib).prefs.roles.risk, undefined, "detection never changes preferences");
});

test("insufficient or dependent evidence produces nothing", () => {
  const { t, ib } = setup();
  t.decide(ib, "r1", "dismissed", { reason: "wrong_field" });
  assert.equal(run(t, ib).length, 0, "one dismissal is not a pattern");
  for (const id of ["s1", "s2", "s3"]) t.decide(ib, id, "dismissed", { reason: "wrong_field" });
  assert.equal(run(t, ib).filter((s) => s.key === "role:risk:low").length, 0, "four postings, all at one firm");
  const { t: t2, ib: ib2 } = setup();
  for (const id of ["r1", "r2", "r3"]) t2.decide(ib2, id, "dismissed");
  assert.equal(run(t2, ib2).length, 0, "dismissals without a reason say nothing about the field");
});

test("undone dismissals are not evidence", () => {
  const { t, ib } = setup();
  t.decide(ib, "r1", "dismissed", { reason: "wrong_field" });
  t.decide(ib, "r2", "dismissed", { reason: "wrong_field" });
  const e = t.decide(ib, "r3", "dismissed", { reason: "wrong_field" });
  t.undo(ib, e.id);
  assert.equal(run(t, ib).length, 0);
});

test("feedback is scoped to its target", () => {
  const { t, ib, other } = setup();
  for (const id of ["r1", "r2", "r3"]) t.decide(ib, id, "dismissed", { reason: "wrong_field" });
  assert.equal(run(t, other).length, 0);
});

test("a rejected suggestion stays away until clearly more evidence and time", () => {
  const { t, ib } = setup();
  for (const id of ["r1", "r2", "r3"]) t.decide(ib, id, "dismissed", { reason: "wrong_field" });
  let list = run(t, ib);
  list = resolve(list, list[0].id, "rejected", T0);
  t.setSuggestions(ib, list);
  assert.equal(run(t, ib, T0 + DAY).filter((s) => s.status === "pending").length, 0);
  for (const id of ["r4", "r5"]) t.decide(ib, id, "dismissed", { reason: "wrong_field" });
  assert.equal(run(t, ib, T0 + 2 * DAY).filter((s) => s.status === "pending").length, 0, "new evidence, but too soon");
  assert.equal(run(t, ib, T0 + 15 * DAY).filter((s) => s.status === "pending").length, 1);
});

test("not now hides a suggestion for three days", () => {
  const { t, ib } = setup();
  for (const id of ["r1", "r2", "r3"]) t.decide(ib, id, "dismissed", { reason: "wrong_field" });
  let list = run(t, ib);
  list = resolve(list, list[0].id, "deferred", T0);
  t.setSuggestions(ib, list);
  assert.equal(run(t, ib, T0 + DAY).filter((s) => s.status === "pending").length, 0);
  assert.equal(run(t, ib, T0 + 4 * DAY).filter((s) => s.status === "pending").length, 1);
});

test("approving applies a normal revision that changes the ranking and can be undone", () => {
  const { t, ib } = setup();
  t.setPrefs(ib, { ...t.get(ib).prefs, roles: { m_and_a: "core", risk: "ok" } });
  for (const id of ["r1", "r2", "r3"]) t.decide(ib, id, "dismissed", { reason: "wrong_field" });
  const s = run(t, ib).find((x) => x.status === "pending");
  const risky = { id: "z", firm_id: "q", countries: ["CH"], role: { function: "risk", functions: ["risk"], confidence: "high" } };
  const before = evaluate(risky, t.get(ib), { eligibility: "eligible" }).score;
  const rev = t.setPrefs(ib, applySuggestion(t.get(ib).prefs, s), { reason: "suggestion", suggestion: s.id });
  assert.equal(t.get(ib).prefs.roles.risk, "low");
  assert.ok(evaluate(risky, t.get(ib), { eligibility: "eligible" }).score < before);
  t.revert(ib, rev.id);
  assert.equal(t.get(ib).prefs.roles.risk, "ok");
});

test("location and timing reasons suggest deprioritising the place or format", () => {
  const { t, ib } = setup();
  for (const id of ["r1", "r2", "r3"]) t.decide(ib, id, "dismissed", { reason: "location" });
  const s = run(t, ib).find((x) => x.key === "location:DE:low");
  assert.ok(s);
  assert.deepEqual(applySuggestion(t.get(ib).prefs, s).lowCountries, ["DE"]);
});

test("repeated saves of an unselected function suggest accepting it", () => {
  const { t, ib } = setup();
  for (const id of ["r1", "r2", "r3"]) t.decide(ib, id, "saved");
  const s = run(t, ib).find((x) => x.key === "role:risk:ok");
  assert.ok(s);
  assert.equal(applySuggestion(emptyPrefs(), s).roles.risk, "ok");
});

test("behaviour is a bounded, weak nudge per function", () => {
  const { t, ib } = setup();
  for (const id of ["r1", "r2", "r3", "r4"]) t.decide(ib, id, "saved");
  const b = behaviour(t.events(ib), t.decisions(ib), fnOf);
  assert.ok(b.risk > 0 && b.risk < 1);
  const { t: t2, ib: ib2 } = setup();
  t2.decide(ib2, "r1", "saved");
  assert.equal(behaviour(t2.events(ib2), t2.decisions(ib2), fnOf).risk, undefined, "one action is not a signal");
});

test("applications count as positive signals too", () => {
  const { t, ib } = setup();
  for (const id of ["r1", "r2", "r3"]) t.log(ib, id, "applied");
  assert.ok(run(t, ib).find((x) => x.key === "role:risk:ok"));
});
