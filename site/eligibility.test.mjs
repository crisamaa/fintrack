import { test } from "node:test";
import assert from "node:assert/strict";
import { assess, workRights, suggestions } from "./eligibility.js";
import { rank } from "./relevance.js";
import { compose, mailto } from "./outreach.js";

const TODAY = "2026-11-05";
const swissProfile = {
  work_rights: [{ region: "CH", basis: "citizenship", confirmed: true }, { region: "PE", basis: "citizenship", confirmed: true }],
  degree_level: "bachelor", graduation_year: 2028, enrolled: true, languages: [{ code: "en", level: "fluent" }, { code: "de", level: "working" }],
};
const opp = (o) => ({ countries: ["CH"], requirements: [], languages: [], ...o });
const req = (kind, value, evidence = "stated in the ad") => ({ kind, value, evidence });

test("confirmed right to work and nothing excluding: eligible", () => {
  assert.equal(assess(opp({}), swissProfile, { today: TODAY }).outcome, "eligible");
});

test("unconfirmed rights never count", () => {
  const p = { ...swissProfile, work_rights: [{ region: "CH", basis: "citizenship", confirmed: false }] };
  assert.equal(assess(opp({}), p, { today: TODAY }).lines[0].result, "unknown");
});

test("expired permit does not count", () => {
  const p = { work_rights: [{ region: "GB", basis: "permit", confirmed: true, expires: "2026-01-01" }] };
  assert.equal(workRights(p, TODAY).has("GB"), false);
});

test("no right and silence about sponsorship: potentially eligible, never excluded", () => {
  const a = assess(opp({ countries: ["GB"] }), swissProfile, { today: TODAY });
  assert.equal(a.outcome, "potential");
  assert.match(a.lines[0].reason, /says nothing about sponsorship/);
});

test("employer refuses sponsorship: ineligible with the employer's words", () => {
  const a = assess(opp({ countries: ["GB"], requirements: [req("sponsorship", { available: false }, "We are unable to offer visa sponsorship")] }),
    swissProfile, { today: TODAY });
  assert.equal(a.outcome, "ineligible");
  assert.match(a.lines[0].reason, /unable to offer visa sponsorship/);
});

test("employer offers sponsorship: sponsorship route", () => {
  const a = assess(opp({ countries: ["US"], requirements: [req("sponsorship", { available: true })] }), swissProfile, { today: TODAY });
  assert.equal(a.outcome, "sponsorship");
});

test("EU region right covers member countries; Swiss citizenship alone does not imply EU rights", () => {
  const eu = { ...swissProfile, work_rights: [{ region: "EU", basis: "permit", confirmed: true }] };
  assert.equal(assess(opp({ countries: ["DE"] }), eu, { today: TODAY }).lines[0].result, "pass");
  assert.equal(assess(opp({ countries: ["DE"] }), swissProfile, { today: TODAY }).lines[0].result, "unknown");
  assert.ok(suggestions({ citizenships: ["CH"], work_rights: [] }).some((s) => s.region === "EEA"));
});

test("academic requirements", () => {
  const grad = opp({ requirements: [req("graduation_year", { years: [2027] })] });
  assert.equal(assess(grad, swissProfile, { today: TODAY }).outcome, "ineligible");
  const pen = opp({ intake_year: 2027, requirements: [req("graduation_year", { penultimate: true })] });
  assert.equal(assess(pen, swissProfile, { today: TODAY }).outcome, "eligible");
  const master = opp({ requirements: [req("degree_level", { levels: ["master"] })] });
  assert.equal(assess(master, swissProfile, { today: TODAY }).outcome, "ineligible");
  const enrolled = opp({ requirements: [req("enrollment", { required: true })] });
  assert.equal(assess(enrolled, { ...swissProfile, enrolled: null }, { today: TODAY }).outcome, "potential");
});

test("languages: missing from a filled profile excludes; empty profile is unknown", () => {
  const fr = opp({ requirements: [req("language", { code: "fr" })] });
  assert.equal(assess(fr, swissProfile, { today: TODAY }).outcome, "ineligible");
  assert.equal(assess(fr, { work_rights: swissProfile.work_rights }, { today: TODAY }).outcome, "potential");
});

test("languages: speaking some but not all of the required languages is a gap, not an exclusion", () => {
  const enFr = opp({ languages: ["en", "fr"] });
  const a = assess(enFr, swissProfile, { today: TODAY });
  assert.equal(a.outcome, "language_gap");
  const fr = a.lines.find((l) => l.code === "fr");
  assert.equal(fr.result, "gap");
  assert.match(fr.reason, /gap rather than an exclusion/);
  assert.equal(assess(opp({ languages: ["fr", "it"] }), swissProfile, { today: TODAY }).outcome, "ineligible");
  assert.equal(assess(opp({ languages: ["en", "de"] }), swissProfile, { today: TODAY }).outcome, "eligible");
  // Another failing requirement still excludes.
  const grad = opp({ languages: ["en", "fr"], requirements: [req("graduation_year", { years: [2027] })] });
  assert.equal(assess(grad, swissProfile, { today: TODAY }).outcome, "ineligible");
  const r = rank({ status: "open" }, { eligibility: "language_gap" });
  assert.equal(r.score, -2);
});

test("empty profile or unknown country: insufficient information", () => {
  assert.equal(assess(opp({}), {}, { today: TODAY }).outcome, "insufficient");
  assert.equal(assess(opp({ countries: [] }), swissProfile, { today: TODAY }).outcome, "insufficient");
});

test("ranking factors are named and add up", () => {
  const r = rank({ sectors: ["m_and_a"], countries: ["CH"], internship_type: "summer", closes_on: "2026-11-10", status: "open" },
    { prefs: { sectors: ["m_and_a"], countries: ["CH"], types: ["summer"] }, eligibility: "eligible", today: TODAY,
      daysUntil: (a, b) => Math.round((Date.parse(a) - Date.parse(b)) / 864e5) });
  assert.deepEqual(r.factors.map((f) => f.key), ["sector", "country", "type", "deadline_soon", "eligible"]);
  assert.equal(r.score, r.factors.reduce((s, f) => s + f.points, 0));
});

test("drafts use the profile and sourced notes, and placeholders where facts are missing", () => {
  const d = compose("speculative", { profile: { name: "A. Student", degree_level: "master", university: "Uni" },
    employer: { name: "Firm AG", sectors: ["private_equity"] }, intel: [{ kind: "teams", text: "A new growth equity team" }] });
  assert.match(d.text, /A new growth equity team/);
  assert.match(d.text, /private equity/);
  assert.match(compose("speculative", { employer: { name: "X" } }).text, /\[your name\]/);
  assert.ok(mailto({ to: "not an email", subject: "s", text: "t" }).startsWith("mailto:?"));
});
