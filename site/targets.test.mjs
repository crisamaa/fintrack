import { test } from "node:test";
import assert from "node:assert/strict";
import { createTargets, validatePrefs, prefsFromLegacy, TARGETS_KEY } from "./targets.js";

const memory = () => { const m = new Map(); return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), m }; };
let clock = 0;
const now = () => new Date(Date.UTC(2026, 10, 9, 12, 0, clock++)).toISOString();
const make = (storage = memory()) => ({ storage, t: createTargets({ storage, now }) });
const ibPrefs = { roles: { m_and_a: "core" }, countries: ["CH"] };

test("create persists a target, activates it and survives a reload", () => {
  const { storage, t } = make();
  const id = t.create({ name: "Investment Banking — Switzerland", prefs: ibPrefs });
  assert.equal(t.activeId(), id);
  assert.equal(t.get(id).prefs.roles.m_and_a, "core");
  const again = createTargets({ storage, now });
  assert.equal(again.activeId(), id);
  assert.equal(again.get(id).name, "Investment Banking — Switzerland");
  assert.ok(JSON.parse(storage.getItem(TARGETS_KEY)).profiles[id]);
});

test("invalid preferences are rejected with reasons", () => {
  assert.equal(validatePrefs({ roles: {} }).ok, false);
  assert.equal(validatePrefs({ roles: { m_and_a: "maybe" } }).ok, false);
  assert.equal(validatePrefs({ roles: { nope: "core" } }).ok, false);
  assert.equal(validatePrefs({ anyFinance: true, countriesHard: true, countries: [] }).ok, false);
  assert.equal(validatePrefs({ anyFinance: true, years: [1999] }).ok, false);
  assert.equal(validatePrefs({ anyFinance: true }).ok, true);
  const { t } = make();
  assert.throws(() => t.create({ name: " ", prefs: ibPrefs }), /name/i);
  assert.throws(() => t.create({ name: "X", prefs: { roles: {} } }), /function/i);
});

test("edit records a revision, bumps the version and can be reverted", () => {
  const { t } = make();
  const id = t.create({ name: "IB", prefs: ibPrefs });
  const v1 = t.get(id).prefsVersion;
  t.setPrefs(id, { ...t.get(id).prefs, countries: ["CH", "DE"] }, { reason: "edited" });
  assert.equal(t.get(id).prefsVersion, v1 + 1);
  const [rev] = t.revisions(id);
  assert.deepEqual(rev.before.countries, ["CH"]);
  t.revert(id, rev.id);
  assert.deepEqual(t.get(id).prefs.countries, ["CH"]);
  t.rename(id, "IB Zurich");
  assert.equal(t.get(id).name, "IB Zurich");
});

test("duplicate copies preferences but not decisions; profiles stay independent", () => {
  const { t } = make();
  const a = t.create({ name: "IB", prefs: ibPrefs });
  t.decide(a, "opp1", "dismissed", { reason: "wrong_field" });
  const b = t.duplicate(a);
  assert.notEqual(a, b);
  assert.match(t.get(b).name, /copy/);
  assert.equal(t.decision(b, "opp1"), null);
  t.setPrefs(b, { ...t.get(b).prefs, roles: { private_equity: "core" } });
  assert.equal(t.get(a).prefs.roles.m_and_a, "core");
  assert.equal(t.get(a).prefs.roles.private_equity, undefined);
  assert.equal(t.decision(a, "opp1").state, "dismissed");
});

test("switching, archiving, restoring and deleting", () => {
  const { t } = make();
  const a = t.create({ name: "IB", prefs: ibPrefs });
  const b = t.create({ name: "AM", prefs: { roles: { asset_management: "core" } } });
  assert.equal(t.activeId(), b);
  t.activate(a);
  assert.equal(t.activeId(), a);
  t.archive(a);
  assert.equal(t.activeId(), b, "archiving the active target switches to another");
  assert.deepEqual(t.list().map((p) => p.id), [b]);
  assert.deepEqual(t.list({ archived: true }).map((p) => p.id), [a]);
  t.restore(a);
  assert.equal(t.list().length, 2);
  t.decide(a, "o", "saved");
  t.remove(a);
  assert.equal(t.get(a), null);
  assert.equal(t.events(a).length, 0);
  assert.equal(t.activeId(), b);
});

test("decisions are per target and every change is an event; undo reverses", () => {
  const { t } = make();
  const a = t.create({ name: "IB", prefs: ibPrefs });
  const b = t.create({ name: "AM", prefs: { roles: { asset_management: "core" } } });
  const ev = t.decide(a, "o1", "dismissed", { fp: { function: "risk" } });
  assert.equal(t.decision(a, "o1").state, "dismissed");
  assert.equal(t.decision(b, "o1"), null);
  t.setReason(a, "o1", "wrong_field");
  assert.equal(t.decision(a, "o1").reason, "wrong_field");
  assert.equal(t.events(a).find((e) => e.id === ev.id).reason, "wrong_field");
  t.undo(a, ev.id);
  assert.equal(t.decision(a, "o1"), null);
  const evs = t.events(a);
  assert.equal(evs.find((e) => e.id === ev.id).reversed, true);
  assert.equal(evs.at(-1).action, "undo");
  assert.equal(evs.at(-1).reverses, ev.id);
  assert.throws(() => t.decide(a, "o1", "maybe"), /state/);
});

test("visits keep the previous review time per target", () => {
  const { t } = make();
  const a = t.create({ name: "IB", prefs: ibPrefs });
  const created = t.get(a).created;
  const first = t.visit(a);
  assert.equal(first.previous, created);
  const second = t.visit(a);
  assert.equal(second.previous, first.current);
});

test("role corrections are global, reversible and never touch source data", () => {
  const { t } = make();
  t.setOverride("o1", "m_and_a");
  assert.equal(t.override("o1"), "m_and_a");
  t.setOverride("o1", null);
  assert.equal(t.override("o1"), null);
  assert.throws(() => t.setOverride("o1", "astrology"), /function/);
});

test("the onboarding draft persists until activation", () => {
  const { storage, t } = make();
  t.saveDraft({ step: 2, name: "IB", prefs: ibPrefs });
  assert.equal(createTargets({ storage, now }).draft().step, 2);
  t.create({ name: "IB", prefs: ibPrefs, fromDraft: true });
  assert.equal(t.draft(), null);
});

test("legacy ranking preferences prefill a draft, with the inference stated", () => {
  const d = prefsFromLegacy({ prefs: { sectors: ["private_equity", "banking"], countries: ["CH"], types: ["summer"] } });
  assert.equal(d.prefs.roles.private_equity, "core");
  assert.equal(d.prefs.roles.banking, "core");
  assert.deepEqual(d.prefs.countries, ["CH"]);
  assert.deepEqual(d.prefs.types, ["summer"]);
  assert.match(d.note, /Profile/);
  assert.equal(prefsFromLegacy({ prefs: { sectors: [], countries: [], types: [] } }), null);
});

test("export and import round-trip without losing other targets", () => {
  const { t } = make();
  const a = t.create({ name: "IB", prefs: ibPrefs });
  t.decide(a, "o1", "saved");
  const data = t.exportData();
  const other = make().t;
  const c = other.create({ name: "Other", prefs: ibPrefs });
  other.importData(data);
  assert.ok(other.get(a) && other.get(c));
  assert.equal(other.decision(a, "o1").state, "saved");
});

test("unreadable storage falls back to an empty, working store", () => {
  const storage = memory();
  storage.setItem(TARGETS_KEY, "{not json");
  const t = createTargets({ storage, now });
  assert.equal(t.list().length, 0);
  const id = t.create({ name: "IB", prefs: ibPrefs });
  assert.ok(t.get(id));
});

test("restored is a decision state, for undoing an old global dismissal in one target", () => {
  const { t } = make();
  const a = t.create({ name: "IB", prefs: ibPrefs });
  t.decide(a, "o", "restored");
  assert.equal(t.decision(a, "o").state, "restored");
});
