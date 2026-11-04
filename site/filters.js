// Pure filtering, sorting and URL helpers. No DOM access, so it runs under node --test.

export function safeUrl(url) {
  if (typeof url !== "string") return null;
  let parsed;
  try {
    parsed = new URL(url.trim());
  } catch {
    return null;
  }
  return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : null;
}

const has = (selected, value) => !selected || selected.length === 0 || selected.includes(value);

export function visibleRoles(roles, { countries = [], sectors = [], seasons = [], languages = [], eligibility = [],
  openOnly = false, confirmedOnly = false, showHidden = false } = {}) {
  return roles.filter((r) => {
    if (openOnly && r.status !== "open") return false;
    if (confirmedOnly && r.lifecycle === "pending") return false;
    if (!showHidden && (r.excluded_reason || r.dismissed)) return false;
    if (eligibility.length > 0 && !eligibility.includes(r.eligibility)) return false;
    if (sectors.length > 0 && !(r.sectors || [r.sector]).some((x) => sectors.includes(x))) return false;
    if (!has(seasons, r.season)) return false;
    if (countries.length > 0 && !(r.countries || []).some((c) => countries.includes(c))) return false;
    if (languages.length > 0 && (r.languages_required || []).some((l) => !languages.includes(l))) return false;
    return true;
  });
}

function sortValue(role, key) {
  const v = role[key];
  if (Array.isArray(v)) return v.join(", ");
  return v;
}

export function sortRoles(roles, key, direction = "asc") {
  const sign = direction === "desc" ? -1 : 1;
  return [...roles].sort((a, b) => {
    const x = sortValue(a, key);
    const y = sortValue(b, key);
    const xm = x === null || x === undefined || x === "";
    const ym = y === null || y === undefined || y === "";
    if (xm || ym) return xm && ym ? 0 : xm ? 1 : -1; // missing always last
    const cmp = typeof x === "number" && typeof y === "number"
      ? x - y
      : String(x).localeCompare(String(y), "en", { sensitivity: "base" });
    return sign * cmp;
  });
}

// Date-only strings ("2026-12-01") are calendar days: format them in UTC so
// viewers west of UTC do not see the day before. Full timestamps use local time.
export function formatDate(s) {
  if (!s) return null;
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(s);
  const d = new Date(s);
  if (isNaN(d)) return String(s);
  return d.toLocaleDateString("en-GB", dateOnly ? { dateStyle: "medium", timeZone: "UTC" } : { dateStyle: "medium" });
}

// Free-text search over firm, title and city. Every word must match somewhere.
export function searchRoles(roles, query) {
  const words = String(query || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").split(/\s+/).filter(Boolean);
  if (words.length === 0) return roles;
  return roles.filter((r) => {
    const hay = [r.firm_name, r.title, r.city, ...(r.countries || [])].join(" ").toLowerCase()
      .normalize("NFD").replace(/[̀-ͯ]/g, "");
    return words.every((w) => hay.includes(w));
  });
}

// Whole calendar days from `today` (a "YYYY-MM-DD" string) to a date-only string. Null when either is missing.
export function daysUntil(dateStr, today) {
  if (!dateStr || !today) return null;
  const a = Date.parse(String(today).slice(0, 10) + "T00:00:00Z");
  const b = Date.parse(String(dateStr).slice(0, 10) + "T00:00:00Z");
  if (isNaN(a) || isNaN(b)) return null;
  return Math.round((b - a) / 86_400_000);
}

// "past" | "urgent" (0-7 days) | "soon" (8-21) | "later" | null
export function urgency(days) {
  if (days === null || days === undefined) return null;
  if (days < 0) return "past";
  if (days <= 7) return "urgent";
  if (days <= 21) return "soon";
  return "later";
}

// Application stages, in order. `rank` drives the progress marks; closed-out stages end the pipeline.
export const STAGES = [
  { id: "saved", label: "Shortlisted", rank: 0 },
  { id: "applied", label: "Applied", rank: 1 },
  { id: "test", label: "Online test", rank: 2 },
  { id: "interview", label: "Interviews", rank: 3 },
  { id: "final", label: "Final round", rank: 4 },
  { id: "offer", label: "Offer", rank: 5, closed: true },
  { id: "rejected", label: "Rejected", rank: -1, closed: true },
  { id: "withdrawn", label: "Withdrawn", rank: -1, closed: true },
];
export const stageById = (id) => STAGES.find((s) => s.id === id) || null;

// Group deadlines into agenda buckets relative to today.
export function deadlineBucket(days) {
  if (days === null) return "No closing date";
  if (days < 0) return "Closed";
  if (days <= 6) return "This week";
  if (days <= 13) return "Next week";
  if (days <= 30) return "Within a month";
  return "Later";
}

// v2 opportunity -> the role shape the views render. `status` keeps v1's two display values (open / closed);
// the full lifecycle is in `lifecycle` and shown as a tag.
export const LIVE = new Set(["open", "pending", "unavailable", "unknown"]);
export function toRole(o, employers = new Map()) {
  const e = employers.get(o.employer_id);
  return {
    ...o,
    firm_id: o.employer_id, firm_name: o.employer, lifecycle: o.status,
    status: LIVE.has(o.status) ? "open" : "closed",
    sector: (o.sectors || [])[0] || (e?.sectors || [])[0] || "other_finance",
    sectors: (o.sectors || []).length ? o.sectors : (e?.sectors || []),
    season: o.internship_type || "unknown", year: o.intake_year, closes: o.closes_on,
    languages_required: o.languages || [], first_seen: (o.first_seen_at || "").slice(0, 10),
    last_checked: (o.last_verified_at || "").slice(0, 10) || null, closed_on: (o.closed_at || "").slice(0, 10) || null,
    employer_priority: e?.priority,
  };
}

// The latest window of a program, and what the user should read it as.
export function programStatus(p, today) {
  const ws = p.windows || [];
  const year = Math.max(0, ...ws.map((w) => w.intake_year));
  const cur = ws.filter((w) => w.intake_year === year);
  const confirmed = cur.find((w) => w.kind === "confirmed");
  const observed = cur.find((w) => w.kind === "observed");
  const estimated = cur.find((w) => w.kind === "estimated");
  const open = (p.live || 0) > 0;
  if (open) {
    const seen = ws.filter((w) => w.kind === "observed" || w.kind === "confirmed").sort((a, b) => b.intake_year - a.intake_year)[0];
    return { key: "open", label: "Open now", year: seen?.intake_year || null, window: seen };
  }
  if (confirmed?.opens_on && confirmed.opens_on >= today) return { key: "confirmed", label: "Opening date announced", year, window: confirmed };
  if (estimated) return { key: "estimated", label: "Expected (estimate)", year, window: estimated };
  return { key: "history", label: "Closed — history only", year, window: confirmed || observed };
}
