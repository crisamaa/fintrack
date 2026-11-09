// The matching engine: how well one opportunity fits one career target. Pure and deterministic, so it runs under
// node --test and gives the same answer on every load.
//
// Two separate questions, never merged: relevance (does it fit this target?) is computed here; eligibility (can you
// apply?) comes from eligibility.js and is passed in, reported beside the relevance, and never changes the score.
//
// 1. Hard constraints first: an excluded function, employer, or a country/format/intake the target *requires*.
//    Missing data never fails a hard constraint; it marks the match uncertain instead.
// 2. Relevance is a weighted average over the dimensions the target actually sets (role always counts), so a
//    target that says nothing about places does not dilute every score with a neutral value, and a posting with
//    more fields filled in is not rewarded for that alone. Unknown values score neutral (0.5), never zero.
// 3. A category from transparent thresholds, and one reason line per factor. The number orders the list; it is
//    never shown as a probability.
//
// The function taxonomy below is a copy of tracker/roles.py; tests/test_roles.py fails if they drift.

export const MATCH_VERSION = "match-1";
export const WEIGHTS = { role: 40, geo: 20, timing: 15, employer: 10, other: 10, behaviour: 5 };
export const LEVELS = { core: "Strongly preferred", ok: "Acceptable", low: "Deprioritised", exclude: "Excluded" };
export const CATEGORIES = { top: "Top match", good: "Good match", adjacent: "Adjacent", review: "Needs review",
  low: "Low match", excluded: "Excluded by this target" };

export const FAMILIES = {
  advisory: "Advisory & capital markets",
  markets: "Markets",
  investing: "Investing",
  wealth: "Wealth & private banking",
  banking: "Banking",
  corporate: "Corporate & risk finance",
  business: "Business functions",
  technology: "Technology & data",
};
export const FUNCTIONS = {
  investment_banking: { label: "Investment banking", family: "advisory", finance: true },
  m_and_a: { label: "M&A advisory", family: "advisory", finance: true },
  capital_markets: { label: "Capital markets", family: "advisory", finance: true },
  ecm: { label: "Equity capital markets", family: "advisory", finance: true },
  dcm: { label: "Debt capital markets & debt advisory", family: "advisory", finance: true },
  restructuring: { label: "Restructuring", family: "advisory", finance: true },
  corporate_finance: { label: "Corporate finance & valuation", family: "advisory", finance: true },
  sales_trading: { label: "Sales & trading", family: "markets", finance: true },
  research: { label: "Equity & credit research", family: "markets", finance: true },
  quant: { label: "Quantitative finance", family: "markets", finance: true },
  investing: { label: "Investing (team not stated)", family: "investing", finance: true },
  private_equity: { label: "Private equity", family: "investing", finance: true },
  venture_capital: { label: "Venture capital", family: "investing", finance: true },
  hedge_funds: { label: "Hedge funds", family: "investing", finance: true },
  alternatives: { label: "Alternatives & infrastructure", family: "investing", finance: true },
  real_estate: { label: "Real estate investing", family: "investing", finance: true },
  credit: { label: "Credit & lending", family: "investing", finance: true },
  asset_management: { label: "Asset management", family: "investing", finance: true },
  family_office: { label: "Family office", family: "investing", finance: true },
  wealth_management: { label: "Wealth management", family: "wealth", finance: true },
  private_banking: { label: "Private banking", family: "wealth", finance: true },
  banking: { label: "Corporate & retail banking", family: "banking", finance: true },
  treasury: { label: "Treasury", family: "corporate", finance: true },
  risk: { label: "Financial risk", family: "corporate", finance: true },
  finance_control: { label: "Finance & controlling", family: "corporate", finance: true },
  investment_consulting: { label: "Investment consulting", family: "corporate", finance: true },
  fintech: { label: "Fintech & financial strategy", family: "corporate", finance: true },
  other_finance: { label: "Other finance", family: "corporate", finance: true },
  investor_relations: { label: "Investor relations & fundraising", family: "business", finance: false },
  strategy: { label: "Strategy & business management", family: "business", finance: false },
  sustainability: { label: "Sustainability & ESG", family: "business", finance: false },
  sales: { label: "Sales & business development", family: "business", finance: false },
  operations: { label: "Operations", family: "business", finance: false },
  compliance_legal: { label: "Compliance & legal", family: "business", finance: false },
  hr: { label: "HR & recruiting", family: "business", finance: false },
  marketing: { label: "Marketing & communications", family: "business", finance: false },
  product: { label: "Product", family: "technology", finance: false },
  data: { label: "Data & analytics", family: "technology", finance: false },
  technology: { label: "Technology & engineering", family: "technology", finance: false },
};
export const GENERIC = new Set(["investing", "other_finance"]);

export function emptyPrefs() {
  return { roles: {}, anyFinance: false, countries: [], lowCountries: [], cities: [], countriesHard: false,
    types: [], lowTypes: [], typesHard: false, years: [], yearsHard: false, employers: { prefer: [], exclude: [] },
    employerTypes: [], languages: [] };
}

const fold = (s) => String(s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
export const functionLabel = (fn) => FUNCTIONS[fn]?.label || (fn === "unknown" ? "Not determined" : String(fn || ""));
const lower = (s) => s.charAt(0).toLowerCase() + s.slice(1);
const round1 = (x) => Math.round(x * 10) / 10;

// The classification in force: the user's correction if there is one, else the tracker's. The source record is
// never modified.
export function effectiveRole(opp, override) {
  const r = opp.role;
  if (override && FUNCTIONS[override]) {
    return { function: override, functions: [override, ...(r?.functions || []).filter((f) => f !== override)],
      confidence: "high", method: "user", evidence: "corrected by you", corrected: true, original: r?.function || "unknown" };
  }
  if (!r || !r.function || r.function === "unknown") {
    return { function: "unknown", functions: [], confidence: "none", method: r?.method || "none", evidence: null };
  }
  return { ...r, functions: r.functions?.length ? r.functions : [r.function] };
}

export function levelOf(prefs, fn) {
  const l = prefs.roles?.[fn];
  if (l) return l;
  if (prefs.anyFinance && FUNCTIONS[fn]?.finance) return "ok";
  return null;
}

// Families the target is interested in: those of its strongly preferred and acceptable functions.
function targetFamilies(prefs) {
  const fams = new Set();
  for (const [fn, l] of Object.entries(prefs.roles || {})) if ((l === "core" || l === "ok") && FUNCTIONS[fn]) fams.add(FUNCTIONS[fn].family);
  if (prefs.anyFinance) for (const f of Object.values(FUNCTIONS)) if (f.finance) fams.add(f.family);
  return fams;
}

function scoreFunction(prefs, fn, fams) {
  const label = functionLabel(fn);
  const lvl = levelOf(prefs, fn);
  if (lvl === "core") return { s: 1, lvl, code: "role_core", tone: "+", text: `${label} — a function you strongly prefer` };
  if (lvl === "ok") return { s: 0.75, lvl, code: "role_ok", tone: "+", text: `${label} — a function you accept` };
  if (lvl === "low") return { s: 0.2, lvl, code: "role_low", tone: "-", text: `${label} — deprioritised in this target` };
  if (lvl === "exclude") return { s: 0, lvl, code: "role_excluded", tone: "-", text: `${label} — excluded in this target` };
  const f = FUNCTIONS[fn];
  if (GENERIC.has(fn) && fams.has(f.family)) {
    return { s: 0.6, code: "role_generic", tone: "?", text: `${label}: the posting does not say which team` };
  }
  if (fams.has(f.family)) return { s: 0.55, code: "role_adjacent", tone: "·", text: `${label} — adjacent to your target functions` };
  if (f.finance) return { s: 0.3, code: "role_other_finance", tone: "-", text: `${label} — finance, but not a function you chose` };
  return { s: 0.05, code: "role_outside", tone: "-", text: `${label} — not a finance function you target` };
}

function roleFactor(prefs, role, employer, out) {
  const fams = targetFamilies(prefs);
  if (role.function === "unknown") {
    out.uncertain.push("role_unknown");
    const wanted = (employer?.sectors || []).find((s) => ["core", "ok"].includes(levelOf(prefs, s)));
    if (wanted) {
      out.reasons.push({ code: "role_unknown_firm_hint", tone: "?",
        text: `Role not stated clearly; the firm works in ${lower(functionLabel(wanted))}` });
      return 0.55;
    }
    out.reasons.push({ code: "role_unknown", tone: "?", text: "Role function could not be determined from the posting" });
    return 0.4;
  }
  const primary = scoreFunction(prefs, role.function, fams);
  out.reasons.push({ code: primary.code, tone: primary.tone, text: primary.text });
  if (primary.code === "role_excluded") out.hard.push({ code: "hard_role", text: `${functionLabel(role.function)} is excluded in this target` });
  if (primary.code === "role_generic") out.uncertain.push("role_generic");
  let s = primary.s;
  const financePrimary = FUNCTIONS[role.function]?.finance;
  for (const sec of role.functions.slice(1)) {
    if (!FUNCTIONS[sec]) continue;
    const sc = scoreFunction(prefs, sec, fams);
    if (!["core", "ok"].includes(sc.lvl)) continue;
    // A finance function named beside a non-finance primary is the area of the work, not the work itself.
    const v = financePrimary ? sc.s * 0.8 : Math.min(sc.s * 0.8, 0.35);
    if (v > s) {
      s = v;
      out.reasons.push(financePrimary
        ? { code: "role_secondary", tone: "+", text: `Also involves ${lower(functionLabel(sec))}, a function you chose` }
        : { code: "role_domain", tone: "·", text: `${functionLabel(role.function)} work in ${lower(functionLabel(sec))}` });
    }
  }
  if (role.confidence === "low") {
    s = 0.4 + (s - 0.4) * 0.6;
    out.uncertain.push("role_low_confidence");
    out.reasons.push({ code: "role_low_confidence", tone: "?", text: "Function inferred from the description only — verify it" });
  } else if (role.method === "ai") {
    out.reasons.push({ code: "role_ai", tone: "·", text: "Function read by AI from the posting text" });
  } else if (role.corrected) {
    out.reasons.push({ code: "role_corrected", tone: "·", text: "Function corrected by you" });
  }
  return s;
}

function geoFactor(prefs, opp, out, countryName) {
  const cs = opp.countries || [];
  const listed = prefs.countries.length || prefs.cities.length;
  if (!listed && !prefs.lowCountries.length) return null;
  if (!cs.length && !opp.city) {
    out.uncertain.push("location_unknown");
    out.reasons.push({ code: "geo_unknown", tone: "?", text: "Location not stated" });
    return 0.5;
  }
  const city = prefs.cities.find((c) => opp.city && fold(opp.city).includes(fold(c)));
  if (city) { out.reasons.push({ code: "geo_city", tone: "+", text: `In ${opp.city}, a preferred city` }); return 1; }
  const hit = cs.find((c) => prefs.countries.includes(c));
  if (hit) {
    out.reasons.push({ code: "geo_country", tone: "+", text: `In ${countryName(hit)}, a preferred country` });
    return prefs.cities.length ? 0.85 : 1;
  }
  const low = cs.find((c) => prefs.lowCountries.includes(c));
  if (prefs.countriesHard && prefs.countries.length && cs.length) {
    out.hard.push({ code: "hard_country", text: "Outside the countries this target requires" });
  }
  if (low) { out.reasons.push({ code: "geo_low", tone: "-", text: `In ${countryName(low)}, deprioritised in this target` }); return 0.15; }
  if (!listed) return 0.6;
  if (!cs.length) {
    out.uncertain.push("location_unknown");
    out.reasons.push({ code: "geo_unknown", tone: "?", text: "Country not stated" });
    return 0.5;
  }
  out.reasons.push({ code: "geo_outside", tone: "-", text: `Outside your preferred places (${cs.map(countryName).join(", ")})` });
  return 0.2;
}

function timingFactor(prefs, opp, out, typeName) {
  const typeActive = prefs.types.length || prefs.lowTypes.length;
  const yearActive = prefs.years.length;
  if (!typeActive && !yearActive) return null;
  let t = 0.5, y = 0.5;
  const type = opp.internship_type && opp.internship_type !== "unknown" ? opp.internship_type : null;
  if (typeActive) {
    if (!type) {
      out.uncertain.push("format_unknown");
      out.reasons.push({ code: "timing_type_unknown", tone: "?", text: "Format (summer, off-cycle…) not stated" });
    } else if (prefs.types.includes(type)) {
      t = 1; out.reasons.push({ code: "timing_type", tone: "+", text: `${typeName(type)} — a format you want` });
    } else {
      t = prefs.lowTypes.includes(type) ? 0.1 : 0.25;
      out.reasons.push({ code: "timing_type_other", tone: "-", text: `${typeName(type)} — not a format you chose` });
      if (prefs.typesHard && prefs.types.length) out.hard.push({ code: "hard_type", text: `${typeName(type)} is not a format this target requires` });
    }
  }
  if (yearActive) {
    const yr = opp.intake_year;
    if (!yr) {
      out.uncertain.push("intake_unknown");
      out.reasons.push({ code: "timing_year_unknown", tone: "?", text: "Intake year not stated" });
    } else if (prefs.years.includes(yr)) {
      y = 1; out.reasons.push({ code: "timing_year", tone: "+", text: `Intake ${yr}, one you selected` });
    } else {
      y = 0.1; out.reasons.push({ code: "timing_year_other", tone: "-", text: `Intake ${yr} is not one you selected` });
      if (prefs.yearsHard) out.hard.push({ code: "hard_year", text: `Intake ${yr} is outside the years this target requires` });
    }
  }
  return typeActive && yearActive ? 0.6 * t + 0.4 * y : typeActive ? t : y;
}

function employerFactor(prefs, opp, employer, out) {
  const { prefer = [], exclude = [] } = prefs.employers || {};
  if (exclude.includes(opp.firm_id)) out.hard.push({ code: "hard_employer", text: `${opp.firm_name} is excluded in this target` });
  if (!prefer.length && !prefs.employerTypes.length) return null;
  if (prefer.includes(opp.firm_id)) {
    out.reasons.push({ code: "employer_preferred", tone: "+", text: `${opp.firm_name} is an employer you prefer` });
    return 1;
  }
  const type = (employer?.sectors || []).find((s) => prefs.employerTypes.includes(s));
  if (type) {
    out.reasons.push({ code: "employer_type", tone: "+", text: `${opp.firm_name} works in ${lower(functionLabel(type))}` });
    return 0.75;
  }
  return 0.3;
}

function otherFactor(prefs, opp, out, languageName) {
  let s = 0.7;
  if (opp.lifecycle === "pending") {
    s = 0.35;
    out.reasons.push({ code: "unconfirmed", tone: "?", text: "Not yet confirmed on the employer's own site" });
  }
  const langs = opp.languages_required || [];
  if (prefs.languages.length && langs.length) {
    const hit = langs.find((l) => prefs.languages.includes(l));
    if (hit) { s = Math.min(1, s + 0.3); out.reasons.push({ code: "language", tone: "+", text: `Works in ${languageName(hit)}` }); }
    else { s = Math.max(0, s - 0.3); out.reasons.push({ code: "language_other", tone: "-", text: `Asks for ${langs.map(languageName).join(", ")}` }); }
  }
  return s;
}

const ELIGIBILITY_REASON = {
  eligible: { tone: "+", text: "You meet the stated requirements" },
  sponsorship: { tone: "·", text: "Needs sponsorship, which is offered" },
  language_gap: { tone: "-", text: "You speak only some of the languages it asks for" },
  ineligible: { tone: "-", text: "A stated requirement excludes you" },
  potential: { tone: "?", text: "Eligibility not fully established — see requirements" },
  insufficient: { tone: "?", text: "Eligibility cannot be assessed yet — complete your Profile or check the ad" },
};

// ctx: { employers: Map, eligibility: outcome, override: function id, behaviour: { fn: -1..1 }, countryName,
//        typeName, languageName }
export function evaluate(opp, target, ctx = {}) {
  const prefs = { ...emptyPrefs(), ...(target?.prefs || {}) };
  const id = (x) => x;
  const countryName = ctx.countryName || id, typeName = ctx.typeName || id, languageName = ctx.languageName || id;
  const out = { reasons: [], uncertain: [], hard: [] };
  const employer = ctx.employers?.get?.(opp.firm_id) || null;
  const role = effectiveRole(opp, ctx.override);
  const factors = {};
  factors.role = roleFactor(prefs, role, employer, out);
  const geo = geoFactor(prefs, opp, out, countryName); if (geo !== null) factors.geo = geo;
  const timing = timingFactor(prefs, opp, out, typeName); if (timing !== null) factors.timing = timing;
  const emp = employerFactor(prefs, opp, employer, out); if (emp !== null) factors.employer = emp;
  factors.other = otherFactor(prefs, opp, out, languageName);
  const weigh = (fs) => {
    let num = 0, den = 0;
    for (const [k, s] of Object.entries(fs)) { num += WEIGHTS[k] * s; den += WEIGHTS[k]; }
    return round1((100 * num) / den);
  };
  const categoryScore = weigh(factors);
  const b = ctx.behaviour && Object.keys(ctx.behaviour).length ? Math.max(-1, Math.min(1, ctx.behaviour[role.function] || 0)) : null;
  if (b !== null) {
    factors.behaviour = 0.5 + 0.5 * b;
    if (b >= 0.5) out.reasons.push({ code: "behaviour_saved", tone: "+", text: "Similar to roles you saved in this target" });
    if (b <= -0.5) out.reasons.push({ code: "behaviour_dismissed", tone: "-", text: "Similar to roles you dismissed in this target" });
  }
  const score = weigh(factors);
  const eligibility = ctx.eligibility || "insufficient";
  if (ELIGIBILITY_REASON[eligibility]) out.reasons.push({ code: `elig_${eligibility}`, ...ELIGIBILITY_REASON[eligibility] });
  if (eligibility === "insufficient" || eligibility === "potential") out.uncertain.push("eligibility_unknown");

  const rs = factors.role;
  const roleUncertain = out.uncertain.some((u) => u.startsWith("role_"));
  const hardUnknown = (prefs.countriesHard && out.uncertain.includes("location_unknown"))
    || (prefs.typesHard && out.uncertain.includes("format_unknown")) || (prefs.yearsHard && out.uncertain.includes("intake_unknown"));
  const against = ["geo", "timing"].some((k) => k in factors && factors[k] < 0.5);
  let category;
  if (out.hard.length) category = "excluded";
  else if ((roleUncertain || hardUnknown) && rs >= 0.5) category = "review";
  else if (rs >= 0.9 && categoryScore >= 75 && !against) category = "top";
  else if (rs >= 0.7 && categoryScore >= 55) category = "good";
  else if (rs >= 0.45) category = "adjacent";
  else category = "low";
  return { id: opp.id, version: MATCH_VERSION, score, categoryScore, category, role, roleScore: round1(rs), factors,
    reasons: out.reasons, uncertain: [...new Set(out.uncertain)], hard: out.hard, eligibility,
    closes: opp.closes_on || opp.closes || null };
}

// Best first; ties by the sooner closing date, then id, so the order is stable between loads.
export function compareMatches(a, b) {
  return b.score - a.score || (a.closes || "9999").localeCompare(b.closes || "9999") || String(a.id).localeCompare(String(b.id));
}
