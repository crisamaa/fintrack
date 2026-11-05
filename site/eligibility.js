// Eligibility: an opportunity's stated requirements (with evidence, from the tracker) against your profile
// (kept only in this browser). Pure functions, no DOM, so it runs under node --test.
//
// Outcomes, from strongest to weakest:
//   ineligible    a stated requirement excludes you (with the employer's words and your profile value)
//   sponsorship   you lack the right to work there, but the employer states that it sponsors
//   language_gap  nothing else excludes you, but you speak only some of the languages it asks for
//   eligible      you hold a confirmed right to work in its country, and nothing stated excludes you
//   potential     nothing excludes you, but something important is unknown (e.g. no sponsorship statement)
//   insufficient  not enough information about the role or your profile to say anything
// Work rights only count once you have confirmed them in your profile. Nothing here is legal advice.

const EU = ["AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT",
  "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE"];
export const REGIONS = {
  EU,
  EEA: [...EU, "IS", "LI", "NO"],
  EFTA: ["CH", "IS", "LI", "NO"],
};
export const OUTCOMES = {
  eligible: "Confirmed eligible",
  potential: "Potentially eligible",
  sponsorship: "Sponsorship route",
  language_gap: "Partial language match",
  ineligible: "Confirmed ineligible",
  insufficient: "Insufficient information",
};
const LEVEL_RANK = { basic: 1, working: 2, fluent: 3, native: 4 };
const DEGREE_RANK = { bachelor: 1, master: 2, phd: 3 };

export function expand(region) {
  return REGIONS[region] || [region];
}

// Countries where the profile holds a confirmed, unexpired right to work, with the right that grants it.
export function workRights(profile, today) {
  const out = new Map();
  for (const r of profile?.work_rights || []) {
    if (!r || !r.confirmed || !r.region) continue;
    if (r.expires && today && r.expires < today) continue;
    for (const c of expand(r.region)) if (!out.has(c)) out.set(c, r);
  }
  return out;
}

const reqs = (opp, kind) => (opp.requirements || []).filter((r) => r.kind === kind);
const quote = (r) => (r?.evidence ? `“${r.evidence}”` : "");

function workAuthorization(opp, profile, today) {
  const countries = opp.countries || [];
  const rights = workRights(profile, today);
  const sponsor = reqs(opp, "sponsorship");
  const offers = sponsor.find((r) => r.value?.available === true);
  const refuses = sponsor.find((r) => r.value?.available === false);
  const demands = reqs(opp, "work_authorization");
  if (!countries.length) {
    return { dim: "work_authorization", result: "unknown", reason: "The posting's country is not known yet." };
  }
  if (!rights.size) {
    return { dim: "work_authorization", result: "unknown",
      reason: "Add and confirm your work rights in Profile to assess this." };
  }
  const covered = countries.filter((c) => rights.has(c));
  if (covered.length) {
    const r = rights.get(covered[0]);
    const other = countries.length > covered.length ? ` (not for ${countries.filter((c) => !rights.has(c)).join(", ")})` : "";
    return { dim: "work_authorization", result: "pass",
      reason: `You have a confirmed right to work in ${covered.join(", ")} (${r.basis || "confirmed"})${other}.` };
  }
  if (offers) {
    return { dim: "work_authorization", result: "sponsorship",
      reason: `No confirmed right to work in ${countries.join(", ")}, but the employer states sponsorship: ${quote(offers)}` };
  }
  if (refuses) {
    return { dim: "work_authorization", result: "fail",
      reason: `No confirmed right to work in ${countries.join(", ")}, and the posting says ${quote(refuses)}` };
  }
  if (demands.length) {
    return { dim: "work_authorization", result: "fail",
      reason: `The posting requires existing work authorisation ${quote(demands[0])}, which your profile does not show for ${countries.join(", ")}.` };
  }
  return { dim: "work_authorization", result: "unknown",
    reason: `No confirmed right to work in ${countries.join(", ")}; the posting says nothing about sponsorship.` };
}

function degree(opp, profile) {
  const r = reqs(opp, "degree_level")[0];
  if (!r) return null;
  const levels = r.value?.levels || [];
  if (!profile?.degree_level) return { dim: "degree_level", result: "unknown", reason: `Requires ${levels.join(" or ")}; add your degree level in Profile.` };
  if (levels.includes("any") || levels.includes(profile.degree_level)) {
    return { dim: "degree_level", result: "pass", reason: `Open to ${levels.join("/")} students; you are ${profile.degree_level}.` };
  }
  return { dim: "degree_level", result: "fail", reason: `Requires ${levels.join(" or ")} ${quote(r)}; your profile says ${profile.degree_level}.` };
}

function graduation(opp, profile) {
  const out = [];
  for (const r of reqs(opp, "graduation_year")) {
    const g = profile?.graduation_year;
    if (r.value?.years?.length) {
      if (!g) { out.push({ dim: "graduation_year", result: "unknown", reason: `Requires graduation in ${r.value.years.join(" or ")}; add your graduation year.` }); continue; }
      out.push(r.value.years.includes(Number(g))
        ? { dim: "graduation_year", result: "pass", reason: `You graduate in ${g}, which the posting accepts.` }
        : { dim: "graduation_year", result: "fail", reason: `Requires graduation in ${r.value.years.join(" or ")} ${quote(r)}; you graduate in ${g}.` });
    } else if (r.value?.penultimate) {
      if (!g || !opp.intake_year) { out.push({ dim: "graduation_year", result: "unknown", reason: `For penultimate-year students ${quote(r)}; the intake year or your graduation year is not known.` }); continue; }
      const ok = Number(g) === Number(opp.intake_year) + 1;
      out.push({ dim: "graduation_year", result: ok ? "pass" : "fail",
        reason: ok ? `Penultimate year: you graduate in ${g}, the year after the ${opp.intake_year} internship.`
          : `For penultimate-year students ${quote(r)}; with graduation in ${g} you would not be in your penultimate year in ${opp.intake_year}.` });
    }
  }
  return out;
}

function enrollment(opp, profile) {
  const r = reqs(opp, "enrollment")[0];
  if (!r) return null;
  if (profile?.enrolled === true) return { dim: "enrollment", result: "pass", reason: "Requires student status; you are enrolled." };
  if (profile?.enrolled === false) return { dim: "enrollment", result: "fail", reason: `Requires student status ${quote(r)}; your profile says you are not enrolled.` };
  return { dim: "enrollment", result: "unknown", reason: "Requires student status; add your enrolment in Profile." };
}

function duration(opp, profile) {
  const r = reqs(opp, "duration_months")[0];
  if (!r) return null;
  const max = Number(profile?.max_months) || null;
  const { min, max: hi } = r.value || {};
  const label = min === hi ? `${min} months` : `${min}–${hi} months`;
  if (!max) return { dim: "duration_months", result: "info", reason: `Lasts ${label}.` };
  return min <= max ? { dim: "duration_months", result: "pass", reason: `Lasts ${label}; you are available for up to ${max}.` }
    : { dim: "duration_months", result: "fail", reason: `Lasts at least ${min} months ${quote(r)}; you are available for ${max}.` };
}

function languages(opp, profile, languageName = (c) => c) {
  const required = new Map();
  for (const r of reqs(opp, "language")) if (r.value?.code) required.set(r.value.code, r);
  for (const c of opp.languages || []) if (!required.has(c)) required.set(c, null);
  if (!required.size) return [];
  const mine = new Map((profile?.languages || []).filter((l) => l?.code).map((l) => [l.code, l.level || "working"]));
  const lines = [...required].map(([code, r]) => {
    if (!mine.size) return { dim: "language", result: "unknown", reason: `Requires ${languageName(code)}; add your languages in Profile.` };
    const level = mine.get(code);
    if (level && (LEVEL_RANK[level] || 0) >= LEVEL_RANK.working) return { dim: "language", result: "pass", code, reason: `${languageName(code)}: you have ${level}.` };
    return { dim: "language", result: "fail", code,
      reason: `Requires ${languageName(code)} ${r ? quote(r) : ""}; your profile ${level ? `says ${level}` : "does not list it"}.` };
  });
  // Several languages asked for and you speak some of them: a gap to weigh, not an exclusion. Postings often list
  // every language a team uses, and one strong language frequently carries an application.
  const spoken = lines.filter((l) => l.result === "pass");
  if (spoken.length) {
    const have = spoken.map((l) => languageName(l.code)).join(", ");
    for (const l of lines) if (l.result === "fail") { l.result = "gap"; l.reason += ` You do have ${have}, so this is a gap rather than an exclusion.`; }
  }
  return lines;
}

// The full assessment: an outcome and one line per dimension, each saying where it came from.
export function assess(opp, profile, { today, languageName } = {}) {
  const lines = [workAuthorization(opp, profile, today), degree(opp, profile), ...graduation(opp, profile),
    enrollment(opp, profile), duration(opp, profile), ...languages(opp, profile, languageName)].filter(Boolean);
  for (const r of opp.requirements || []) {
    if (r.kind === "experience" || r.kind === "other" || r.kind === "degree_field" || r.kind === "start") {
      lines.push({ dim: r.kind, result: "info", reason: `Not assessed automatically: ${r.value?.summary || ""} ${quote(r)}`.trim() });
    }
  }
  const work = lines[0];
  const profileEmpty = !profile || (!(profile.work_rights || []).length && !profile.degree_level && !(profile.languages || []).length);
  let outcome;
  if (lines.some((l) => l.result === "fail")) outcome = "ineligible";
  else if (lines.some((l) => l.result === "gap")) outcome = "language_gap";
  else if (work.result === "sponsorship") outcome = "sponsorship";
  else if (profileEmpty || !(opp.countries || []).length) outcome = "insufficient";
  else if (work.result === "pass" && !lines.some((l) => l.result === "unknown")) outcome = "eligible";
  else outcome = "potential";
  return { outcome, label: OUTCOMES[outcome], lines };
}

// Gentle profile hints: facts to confirm, never applied automatically.
export function suggestions(profile) {
  const out = [];
  const has = (region) => (profile?.work_rights || []).some((r) => r.region === region);
  const cits = profile?.citizenships || [];
  if (cits.includes("CH") && !has("EU") && !has("EEA")) {
    out.push({ region: "EEA", basis: "citizenship", note: "Swiss citizens generally have free movement in the EU/EEA under the bilateral agreements. Confirm before relying on it." });
  }
  if (cits.includes("CH") && !has("CH")) out.push({ region: "CH", basis: "citizenship", note: "Swiss citizenship: right to work in Switzerland." });
  if (cits.includes("PE") && !has("PE")) out.push({ region: "PE", basis: "citizenship", note: "Peruvian citizenship: right to work in Peru." });
  return out;
}
