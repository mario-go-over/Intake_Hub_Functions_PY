import re
from slugify import slugify
from metaphone import doublemetaphone

SPACE_RE = re.compile(r"\s+")

# Conservative expansions
STREET_MAP = {
    "st": "street", "ave": "avenue", "av": "avenue", "rd": "road",
    "blvd": "boulevard", "ln": "lane", "dr": "drive", "hwy": "highway",
    "pkwy": "parkway", "ct": "court", "cir": "circle", "ter": "terrace",
    "pl": "place", "ctr": "center",
    "n": "north", "s": "south", "e": "east", "w": "west",
}

def _expand_tokens(text: str) -> str:
    out = []
    for t in text.split():
        t_clean = re.sub(r"[^\w]", "", t).lower()
        out.append(STREET_MAP.get(t_clean, t.lower()))
    return SPACE_RE.sub(" ", " ".join(out)).strip()

def normalize_address(addr: str) -> dict:
    x = slugify(addr or "", lowercase=True, separator=" ")
    x = _expand_tokens(x)
    x = SPACE_RE.sub(" ", x).strip()

    # house number (first numeric token)
    house_no = ""
    parts = x.split()
    if parts and parts[0].isdigit():
        house_no = parts[0]
        parts = parts[1:]

    # remove unit (apt/suite/#...)
    street_core = re.sub(r"\b(apt|suite|ste|unit|#)\s*\w+\b", "", " ".join(parts)).strip()

    # phonetic on street core without directionals
    street_no_dir = re.sub(r"\b(north|south|east|west)\b", "", street_core).strip()
    street_phon = (doublemetaphone(street_no_dir)[0] or "")

    return {
        "addr_norm": x,
        "house_no": house_no,
        "street_core": street_core,
        "street_phon": street_phon,
    }
