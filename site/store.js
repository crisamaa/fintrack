// Everything personal is kept in this browser's localStorage and nowhere else: applications, your eligibility
// profile, outreach contacts and drafts, dismissed postings. The tracker's data is public (the repository is
// public), so nothing personal is ever sent anywhere. Export/import is the backup path.
//
// Keys (each versioned on its own; v1 application data is read as-is, never rewritten):
//   fintrack.tracker.v1   applications keyed by opportunity id (unchanged from v1)
//   fintrack.profile.v1   eligibility profile and ranking preferences
//   fintrack.crm.v1       outreach relationships keyed by employer id
//   fintrack.dismissed.v1 opportunity ids marked "not interested"
//   fintrack.prefs.v1     view preferences

const KEY = "fintrack.tracker.v1";
const PREFS = "fintrack.prefs.v1";
const PROFILE = "fintrack.profile.v1";
const CRM = "fintrack.crm.v1";
const DISMISSED = "fintrack.dismissed.v1";

function read(key, fallback) {
  try {
    const v = JSON.parse(localStorage.getItem(key));
    return v && typeof v === "object" ? v : fallback;
  } catch {
    return fallback;
  }
}

function write(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false; // private mode or storage full: keep working in memory
  }
}

// A tracked role keeps a snapshot of the posting, so it stays visible after the ad disappears from the feed.
const SNAPSHOT = ["firm_name", "employer_id", "title", "url", "city", "countries", "sector", "season", "year", "closes",
  "languages_required"];

export function createTracker() {
  let entries = read(KEY, {});
  const listeners = new Set();
  // kind: "stage" when what is tracked changes, "fields" for typed notes and dates.
  const persist = (kind = "stage") => {
    const ok = write(KEY, entries);
    listeners.forEach((fn) => fn(kind));
    return ok;
  };

  return {
    get: (id) => entries[id] || null,
    all: () => entries,
    count: () => Object.keys(entries).length,
    onChange: (fn) => listeners.add(fn),

    setStage(role, stage) {
      const now = new Date().toISOString();
      if (!stage) {
        delete entries[role.id];
      } else {
        const prev = entries[role.id];
        const snap = Object.fromEntries(SNAPSHOT.map((k) => [k, role[k] ?? null]));
        entries[role.id] = {
          ...prev, id: role.id, role: snap, stage, updated: now,
          added: prev?.added || now,
          history: [...(prev?.history || []), { stage, at: now }].slice(-30),
        };
      }
      return persist();
    },

    update(id, fields) {
      if (!entries[id]) return false;
      entries[id] = { ...entries[id], ...fields, updated: new Date().toISOString() };
      return persist("fields");
    },

    exportData: () => entries,

    importEntries(incoming) {
      for (const [id, e] of Object.entries(incoming || {})) {
        if (e && typeof e.stage === "string" && e.role) entries[id] = { ...e, id };
      }
      return persist();
    },
  };
}

// A small keyed store with change listeners, for the profile, the CRM and the dismissed set.
function createDoc(key, fallback) {
  let doc = read(key, fallback);
  const listeners = new Set();
  return {
    get: () => doc,
    set(next) { doc = next; const ok = write(key, doc); listeners.forEach((fn) => fn()); return ok; },
    onChange: (fn) => listeners.add(fn),
  };
}

export const EMPTY_PROFILE = {
  name: "", university: "", degree_level: null, degree_field: "", graduation_year: null, enrolled: null,
  citizenships: [], work_rights: [], languages: [], max_months: null,
  prefs: { sectors: [], countries: [], types: [] },
};

export function createProfile() {
  const d = createDoc(PROFILE, structuredClone(EMPTY_PROFILE));
  return { ...d, get: () => ({ ...structuredClone(EMPTY_PROFILE), ...d.get() }) };
}

export const RELATIONSHIP_STAGES = [
  { id: "research", label: "To research" },
  { id: "to_contact", label: "To contact" },
  { id: "contacted", label: "Contacted" },
  { id: "replied", label: "Replied" },
  { id: "meeting", label: "Conversation held" },
  { id: "applied", label: "Applied speculatively" },
  { id: "closed", label: "Closed" },
];

export function createCrm() {
  const d = createDoc(CRM, {});
  const now = () => new Date().toISOString();
  const uid = () => Math.random().toString(36).slice(2, 10);
  const save = (id, rel) => { const all = { ...d.get(), [id]: { ...rel, updated: now() } }; return d.set(all); };
  return {
    all: () => d.get(),
    get: (id) => d.get()[id] || null,
    onChange: d.onChange,
    start(employer, stage = "research") {
      const cur = d.get()[employer.id];
      if (cur) return true;
      return save(employer.id, { employer_id: employer.id, employer_name: employer.name, stage, added: now(),
        contacts: [], interactions: [], drafts: [], next_date: "", next_step: "", notes: "" });
    },
    update(id, fields) { const cur = d.get()[id]; return cur ? save(id, { ...cur, ...fields }) : false; },
    remove(id) { const all = { ...d.get() }; delete all[id]; return d.set(all); },
    addContact(id, contact) {
      const cur = d.get()[id];
      return cur ? save(id, { ...cur, contacts: [...cur.contacts, { id: uid(), added: now(), ...contact }] }) : false;
    },
    removeContact(id, contactId) {
      const cur = d.get()[id];
      return cur ? save(id, { ...cur, contacts: cur.contacts.filter((c) => c.id !== contactId) }) : false;
    },
    log(id, interaction) {
      const cur = d.get()[id];
      return cur ? save(id, { ...cur, interactions: [{ id: uid(), at: now(), ...interaction }, ...cur.interactions].slice(0, 100) })
        : false;
    },
    saveDraft(id, draft) {
      const cur = d.get()[id];
      if (!cur) return false;
      const drafts = draft.id ? cur.drafts.map((x) => (x.id === draft.id ? { ...x, ...draft, updated: now() } : x))
        : [{ id: uid(), status: "draft", updated: now(), ...draft }, ...cur.drafts];
      return save(id, { ...cur, drafts });
    },
    replaceAll: (all) => d.set({ ...d.get(), ...(all || {}) }),
  };
}

export function createDismissed() {
  const d = createDoc(DISMISSED, {});
  return {
    has: (id) => !!d.get()[id],
    toggle(id) { const all = { ...d.get() }; if (all[id]) delete all[id]; else all[id] = new Date().toISOString(); return d.set(all); },
    all: () => d.get(),
    onChange: d.onChange,
    replaceAll: (all) => d.set({ ...d.get(), ...(all || {}) }),
  };
}

// One file with everything personal. Version 1 files (applications only) still import.
export function exportAll({ tracker, profile, crm, dismissed }) {
  return JSON.stringify({ version: 2, exported: new Date().toISOString(), entries: tracker.exportData(),
    profile: profile.get(), crm: crm.all(), dismissed: dismissed.all() }, null, 2);
}

export function importAll(text, { tracker, profile, crm, dismissed }) {
  const data = JSON.parse(text);
  if (!data || typeof data.entries !== "object") throw new Error("Not a Fintrack export");
  tracker.importEntries(data.entries);
  if (data.version >= 2) {
    if (data.profile && typeof data.profile === "object") profile.set({ ...profile.get(), ...data.profile });
    crm.replaceAll(data.crm);
    dismissed.replaceAll(data.dismissed);
  }
  return Object.keys(data.entries).length;
}

export const prefs = {
  load: () => read(PREFS, {}),
  save: (p) => write(PREFS, p),
};
