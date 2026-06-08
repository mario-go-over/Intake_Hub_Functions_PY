from dataclasses import dataclass
from typing import Optional

from normalize.name import normalize_name
from normalize.address import normalize_address
from normalize.taxid import normalize_tax_id

@dataclass
class Record:
    id: str
    name: str
    address: str
    city: str
    state: str
    zip5: str
    tax_id: Optional[str] = None

@dataclass
class Canonical:
    id: str
    name_norm: str
    name_phon: str
    house_no: str
    street_core: str
    street_phon: str
    city: str
    state: str
    zip5: str
    tax_last4: str
    tax_hash: str  # hex string (we'll convert to bytes when storing)

def canonicalize(rec: Record, *, hmac_key: Optional[bytes] = None) -> Canonical:
    n = normalize_name(rec.name)
    a = normalize_address(rec.address)
    t = normalize_tax_id(rec.tax_id, hmac_key=hmac_key)
    return Canonical(
        id=rec.id,
        name_norm=n["name_norm"],
        name_phon=n["name_phon"],
        house_no=a["house_no"],
        street_core=a["street_core"],
        street_phon=a["street_phon"],
        city=(rec.city or "").strip().lower(),
        state=(rec.state or "").strip().lower(),
        zip5=(rec.zip5 or "")[:5],
        tax_last4=t["tax_last4"],
        tax_hash=t["tax_hash"],  # hex
    )
