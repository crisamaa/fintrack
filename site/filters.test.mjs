import { test } from "node:test";
import assert from "node:assert/strict";
import { visibleRoles, sortRoles, safeUrl } from "./filters.js";

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
