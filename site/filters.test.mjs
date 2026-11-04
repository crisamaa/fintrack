import { test } from "node:test";
import assert from "node:assert/strict";
import { visibleRoles, sortRoles, safeUrl, formatDate } from "./filters.js";

const role = (o) => ({
  id: "x", countries: ["CH"], sector: "banking", season: "summer",
  languages_required: [], status: "open", closes: null, title: "T", ...o,
});
const none = { countries: [], sectors: [], seasons: [], languages: [], openOnly: false };

test("language filter hides roles needing an unticked language", () => {
  const r = role({ languages_required: ["German"] });
  assert.deepEqual(visibleRoles([r], { ...none, languages: ["English"] }), []);
});
test("language filter keeps roles needing only ticked languages", () => {
  const r = role({ languages_required: ["English", "German"] });
  assert.deepEqual(visibleRoles([r], { ...none, languages: ["English", "German"] }), [r]);
});
test("unstated language stays visible", () => {
  const r = role({ languages_required: [] });
  assert.deepEqual(visibleRoles([r], { ...none, languages: ["English"] }), [r]);
});
test("no language ticked means no language filter", () => {
  const r = role({ languages_required: ["German"] });
  assert.deepEqual(visibleRoles([r], none), [r]);
});
test("open only hides closed roles", () => {
  const a = role({ id: "a" }), b = role({ id: "b", status: "closed" });
  assert.deepEqual(visibleRoles([a, b], { ...none, openOnly: true }), [a]);
  assert.equal(visibleRoles([a, b], none).length, 2);
});
test("role in any selected country matches", () => {
  const r = role({ countries: ["CH", "DE"] });
  assert.equal(visibleRoles([r], { ...none, countries: ["DE", "FR"] }).length, 1);
  assert.equal(visibleRoles([r], { ...none, countries: ["FR"] }).length, 0);
});
test("sector and season filters", () => {
  const r = role({});
  assert.equal(visibleRoles([r], { ...none, sectors: ["fintech"] }).length, 0);
  assert.equal(visibleRoles([r], { ...none, sectors: ["banking"], seasons: ["summer"] }).length, 1);
  assert.equal(visibleRoles([r], { ...none, seasons: ["winter"] }).length, 0);
});
test("missing closing dates sort last", () => {
  const rs = [role({ id: "n" }), role({ id: "b", closes: "2026-12-01" }), role({ id: "a", closes: "2026-11-10" })];
  assert.deepEqual(sortRoles(rs, "closes", "asc").map((r) => r.id), ["a", "b", "n"]);
  assert.deepEqual(sortRoles(rs, "closes", "desc").map((r) => r.id), ["b", "a", "n"]);
});
test("sort does not mutate and sorts text case-insensitively", () => {
  const rs = [role({ id: "1", title: "b" }), role({ id: "2", title: "A" })];
  assert.deepEqual(sortRoles(rs, "title", "asc").map((r) => r.id), ["2", "1"]);
  assert.equal(rs[0].id, "1");
});
test("safeUrl rejects javascript and data links", () => {
  assert.equal(safeUrl("javascript:alert(1)"), null);
  assert.equal(safeUrl("data:text/html,x"), null);
  assert.equal(safeUrl("https://a.ch/x"), "https://a.ch/x");
  assert.equal(safeUrl("http://a.ch/x"), "http://a.ch/x");
  assert.equal(safeUrl("not a url"), null);
  assert.equal(safeUrl(null), null);
  assert.equal(safeUrl(" JaVaScRiPt:alert(1)"), null);
});
test("formatDate shows date-only strings as that calendar day in any time zone", () => {
  assert.equal(formatDate("2026-12-01"), "1 Dec 2026");
  assert.equal(formatDate("2026-01-01"), "1 Jan 2026");
  assert.equal(formatDate(null), null);
  assert.equal(formatDate("garbage"), "garbage");
});

import { searchRoles, daysUntil, urgency, deadlineBucket, stageById } from "./filters.js";

test("search matches every word across firm, title and city, ignoring accents", () => {
  const rs = [role({ id: "a", firm_name: "Banque Cantonale de Genève", title: "Stage Crédit", city: "Genève" }),
    role({ id: "b", firm_name: "UBS", title: "Summer Analyst", city: "Zurich" })];
  assert.deepEqual(searchRoles(rs, "geneve credit").map((r) => r.id), ["a"]);
  assert.deepEqual(searchRoles(rs, "  ").map((r) => r.id), ["a", "b"]);
  assert.deepEqual(searchRoles(rs, "ubs paris"), []);
});
test("daysUntil counts calendar days and handles missing values", () => {
  assert.equal(daysUntil("2026-11-11", "2026-11-04"), 7);
  assert.equal(daysUntil("2026-11-03", "2026-11-04"), -1);
  assert.equal(daysUntil(null, "2026-11-04"), null);
});
test("urgency and buckets", () => {
  assert.equal(urgency(-1), "past");
  assert.equal(urgency(0), "urgent");
  assert.equal(urgency(8), "soon");
  assert.equal(urgency(40), "later");
  assert.equal(urgency(null), null);
  assert.equal(deadlineBucket(3), "This week");
  assert.equal(deadlineBucket(10), "Next week");
  assert.equal(deadlineBucket(null), "No closing date");
});
test("stage lookup", () => {
  assert.equal(stageById("applied").rank, 1);
  assert.equal(stageById("nope"), null);
});
