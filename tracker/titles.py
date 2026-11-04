import re

# Whole-word, case-insensitive, so "Internal" and "International" never match "intern".
_INTERNSHIP = re.compile(
    r"""(?<!\w)(?:
        intern(?:ship)?s?
        | (?:summer|spring|winter)\s+analyst
        | off[-\s]?cycle
        | \w*praktikum | praktikant(?:in)?
        | stage | stagiaire | stagiair
        | tirocinio
        | pr[aá]cticas?
        | werkstudent(?:in)? | trainee
    )(?!\w)""",
    re.IGNORECASE | re.VERBOSE,
)

# "Stage" is an English word as well: "Stage Manager" is not an internship.
_NOT_INTERNSHIP = re.compile(r"(?<!\w)stage\s+manager(?!\w)", re.IGNORECASE)


def looks_like_internship(title: str) -> bool:
    return bool(_INTERNSHIP.search(_NOT_INTERNSHIP.sub(" ", title)))
