// Outreach drafts composed in the browser from your profile and the firm's research notes (each note cites the
// firm's own page). Plain templates, no AI call and no network: nothing personal leaves the browser, and nothing
// is ever sent. You copy a draft into your own mail or messaging app, and mark it sent yourself.

export const DRAFT_KINDS = {
  speculative: "Speculative application",
  networking: "Networking request",
  follow_up: "Follow-up",
  questions: "Questions for a conversation",
};

const SECTOR_WORDS = {
  investment_banking: "investment banking", m_and_a: "M&A advisory", private_equity: "private equity",
  venture_capital: "venture capital", asset_management: "asset management", wealth_management: "wealth management",
  private_banking: "private banking", hedge_funds: "hedge funds", family_office: "family office work",
  sales_trading: "markets", research: "research", corporate_finance: "corporate finance", fintech: "fintech",
  banking: "banking", alternatives: "alternative investments", risk: "risk", credit: "credit", treasury: "treasury",
};

function focus(employer) {
  const words = (employer.sectors || []).map((s) => SECTOR_WORDS[s]).filter(Boolean);
  return words.slice(0, 2).join(" and ") || "your work";
}

function hook(intel) {
  const pick = (kind) => (intel || []).find((n) => n.kind === kind);
  const n = pick("development") || pick("teams") || pick("business");
  return n ? n.text.replace(/\.$/, "") : null;
}

function me(profile) {
  const degree = [profile.degree_level ? profile.degree_level[0].toUpperCase() + profile.degree_level.slice(1) + "'s" : "",
    profile.degree_field ? `in ${profile.degree_field}` : ""].filter(Boolean).join(" ");
  const parts = [degree && `${degree} student`, profile.university && `at ${profile.university}`,
    profile.graduation_year && `graduating in ${profile.graduation_year}`].filter(Boolean);
  return parts.length ? parts.join(" ") : "[your degree, university and graduation year]";
}

export function compose(kind, { profile = {}, employer = {}, intel = [], contact = null, channel = null } = {}) {
  const name = profile.name || "[your name]";
  const greeting = contact?.name ? `Dear ${contact.name},` : "Dear Sir or Madam,";
  const h = hook(intel);
  const why = h ? `I have followed ${employer.name}'s work, in particular: ${h}.` :
    `[One specific, sourced reason you are interested in ${employer.name}.]`;
  const lines = {
    speculative: [
      greeting, "",
      `I am a ${me(profile)}, and I am writing to ask whether ${employer.name} might take on an intern in ${focus(employer)}.`,
      why,
      "[Two sentences on relevant experience or skills, with one concrete example.]",
      "I would be available for [dates / duration] and would be glad to send my CV or to talk briefly at your convenience.",
      "", "Kind regards,", name,
    ],
    networking: [
      greeting, "",
      `I am a ${me(profile)} with a strong interest in ${focus(employer)}.`,
      why,
      `Would you have 15 minutes in the coming weeks for a short call about your path into ${employer.name} and the team's work? I would value your perspective.`,
      "", "Thank you for considering it,", name,
    ],
    follow_up: [
      greeting, "",
      "I hope you are well. I wanted to follow up on my message of [date] and see whether you might have had a chance to consider it.",
      "[One line adding something new: an update, a result, a question.]",
      "", "Kind regards,", name,
    ],
    questions: [
      `Questions for ${contact?.name || "the conversation"} at ${employer.name}`, "",
      `- What does an intern in ${focus(employer)} at ${employer.name} actually work on in the first month?`,
      "- How does the firm usually find its interns: programmes, referrals, speculative applications?",
      h ? `- How has "${h}" changed the team's priorities?` : "- What is the team focused on this year?",
      "- What distinguishes the students who do well here?",
      "- Who else would you suggest I speak to?",
    ],
  }[kind] || [];
  const text = lines.join("\n");
  const subject = { speculative: `Internship enquiry: ${name}`, networking: `Short conversation about ${employer.name}?`,
    follow_up: "Following up", questions: "" }[kind] || "";
  return { kind, subject, text, to: contact?.email || channel || "" };
}

// A mailto: link the user's own mail client opens with the draft filled in; nothing is sent from here.
export function mailto({ to, subject, text }) {
  const addr = /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(to || "") ? to : "";
  return `mailto:${encodeURIComponent(addr)}?subject=${encodeURIComponent(subject || "")}&body=${encodeURIComponent(text || "")}`;
}
