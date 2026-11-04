// Personal application tracking, kept in this browser's localStorage.
// The site has no server or accounts, so export/import is the backup path.

const KEY = "fintrack.tracker.v1";
const PREFS = "fintrack.prefs.v1";

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
const SNAPSHOT = ["firm_name", "title", "url", "city", "countries", "sector", "season", "year", "closes", "languages_required"];

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

    exportJson: () => JSON.stringify({ version: 1, exported: new Date().toISOString(), entries }, null, 2),

    importJson(text) {
      const data = JSON.parse(text);
      if (!data || typeof data.entries !== "object") throw new Error("Not a Fintrack export");
      for (const [id, e] of Object.entries(data.entries)) {
        if (e && typeof e.stage === "string" && e.role) entries[id] = { ...e, id };
      }
      return persist();
    },
  };
}

export const prefs = {
  load: () => read(PREFS, {}),
  save: (p) => write(PREFS, p),
};
