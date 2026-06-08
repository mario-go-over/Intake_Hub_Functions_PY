import re, hmac, hashlib
from typing import Optional

def normalize_tax_id(tax_id: Optional[str], *, hmac_key: Optional[bytes] = None) -> dict:
    digits = re.sub(r"\D", "", tax_id or "")
    last4 = digits[-4:] if len(digits) >= 4 else ""
    h = ""
    if digits and hmac_key:
        h = hmac.new(hmac_key, digits.encode("utf-8"), hashlib.sha256).hexdigest()
    return {"tax_last4": last4, "tax_hash": h}
