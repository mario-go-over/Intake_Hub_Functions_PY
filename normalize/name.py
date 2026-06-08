import re
from slugify import slugify
from metaphone import doublemetaphone

COMPANY_SUFFIXES = r"\b(inc|incorporated|llc|l\.l\.c|ltd|limited|co|corp|corporation|pllc|llp|pc|company|the)\b"
COMPANY_SUFFIXES_RE = re.compile(COMPANY_SUFFIXES, re.I)
AMP_RE = re.compile(r"&")
SPACE_RE = re.compile(r"\s+")

NUM_ORD_REPLACERS = [
    (re.compile(r"\b1st\b", re.I), "first"),
    (re.compile(r"\b2nd\b", re.I), "second"),
    (re.compile(r"\b3rd\b", re.I), "third"),
]

def normalize_name(name: str) -> dict:
    x = name or ""
    x = AMP_RE.sub(" and ", x)
    for pat, rep in NUM_ORD_REPLACERS:
        x = pat.sub(rep, x)
    # strip punctuation/accents & lowercase
    x = slugify(x, lowercase=True, separator=" ")
    # remove suffixes
    x = COMPANY_SUFFIXES_RE.sub("", x)
    # collapse spaces
    x = SPACE_RE.sub(" ", x).strip()

    # phonetic key
    m1, m2 = doublemetaphone(x)
    phon = m1 or m2 or ""

    return {
        "name_norm": x,
        "name_phon": phon,
    }
