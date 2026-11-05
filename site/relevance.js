// Relevance: a transparent, rule-based ranking. Each factor adds or removes named points and says why; the
// total orders the list and is never shown as a probability of getting an offer. Pure, testable under node.

export const WEIGHTS = {
  sector: 3, country: 2, type: 2, priority_firm: 1, deadline_soon: 2, new: 1, unconfirmed: -1,
  eligible: 2, sponsorship: 0, language_gap: -2, ineligible: -6, tracked: 1,
};

export function rank(opp, { prefs = {}, eligibility = null, today, tracked = false, daysUntil } = {}) {
  const factors = [];
  const add = (key, points, why) => { if (points) factors.push({ key, points, why }); };
  const sectors = prefs.sectors || [];
  const countries = prefs.countries || [];
  const types = prefs.types || [];
  const hitSector = (opp.sectors || []).find((s) => sectors.includes(s));
  if (hitSector) add("sector", WEIGHTS.sector, `in your sector ${hitSector.replaceAll("_", " ")}`);
  const hitCountry = (opp.countries || []).find((c) => countries.includes(c));
  if (hitCountry) add("country", WEIGHTS.country, `in ${hitCountry}, one of your countries`);
  if (types.includes(opp.internship_type)) add("type", WEIGHTS.type, `${opp.internship_type.replaceAll("_", " ")} is a format you want`);
  if (opp.employer_priority === 1) add("priority_firm", WEIGHTS.priority_firm, "a high-priority firm");
  const d = daysUntil && opp.closes_on ? daysUntil(opp.closes_on, today) : null;
  if (d !== null && d >= 0 && d <= 14) add("deadline_soon", WEIGHTS.deadline_soon, `closes in ${d} day${d === 1 ? "" : "s"}`);
  const seen = daysUntil && opp.first_seen_at ? daysUntil(opp.first_seen_at.slice(0, 10), today) : null;
  if (seen !== null && seen >= -6) add("new", WEIGHTS.new, "found in the last 7 days");
  if (opp.status === "pending") add("unconfirmed", WEIGHTS.unconfirmed, "not yet confirmed on the employer's own site");
  if (eligibility) add(eligibility, WEIGHTS[eligibility] || 0, {
    eligible: "you are eligible", sponsorship: "needs sponsorship, which is offered",
    language_gap: "you speak only some of the languages it asks for", ineligible: "a stated requirement excludes you",
  }[eligibility] || "");
  if (tracked) add("tracked", WEIGHTS.tracked, "you are tracking it");
  return { score: factors.reduce((s, f) => s + f.points, 0), factors };
}
