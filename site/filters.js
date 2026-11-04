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

export function visibleRoles(roles, { countries = [], sectors = [], seasons = [], languages = [], openOnly = false } = {}) {
  return roles.filter((r) => {
    if (openOnly && r.status !== "open") return false;
    if (!has(sectors, r.sector)) return false;
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
