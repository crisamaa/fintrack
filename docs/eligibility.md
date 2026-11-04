# Eligibility and ranking

## Where it runs

Requirements are extracted by the tracker (public data); they are **evaluated in your browser** against your
profile, which never leaves it (`site/eligibility.js`). Nothing here is legal advice.

## Requirements (tracker)

`tracker/requirements.py` reads each posting sentence by sentence (EN/DE/FR/IT/ES) and records a requirement only
with the sentence that states it: work authorisation (regions), sponsorship (offered / not offered), degree level,
graduation year or penultimate year, enrolment, duration in months, languages. The AI enrichment may add more, but
each must quote the posting exactly or it is dropped.

## Your profile (browser)

Citizenships, work rights and permits are separate entries. A work right is a region (a country, or EU / EEA /
EFTA) with its basis (citizenship, permit, other), an optional expiry and a **Confirmed** tick. Only confirmed,
unexpired rights count. The profile may *suggest* rights to check (e.g. Swiss citizens and EU/EEA free movement),
but never adds a confirmed one for you.

## Outcomes

| Outcome | When |
| --- | --- |
| Confirmed ineligible | a stated requirement excludes you (shown with the employer's words and your profile value) |
| Sponsorship route | no right to work there, but the employer states that it sponsors |
| Confirmed eligible | a confirmed right to work in the role's country, and every stated requirement is met |
| Potentially eligible | nothing excludes you, but something is unknown (e.g. no sponsorship statement) |
| Insufficient information | the role's country is unknown, or the profile is empty |

Silence about sponsorship never excludes a role. Every outcome is reversible in the list: the Eligibility filter is
off by default and filters only when you choose.

## Ranking

`site/relevance.js` adds named points: your sectors (+3), countries (+2), formats (+2), a priority firm (+1), closing
within 14 days (+2), new this week (+1), unconfirmed (−1), eligible (+2), ineligible (−6), tracked (+1). The detail
pane lists the factors behind each score. It orders the list; it is not a probability of an offer.
