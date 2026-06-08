from models.record import Canonical

def blocking_keys(c: Canonical) -> dict:
    """
    Multiple blocking keys to keep candidate sets small but complete.
    """
    return {
        "bk1": f"{c.zip5}|{(c.name_phon or '')[:6]}",
        "bk2": f"{c.city}|{c.state}|{c.house_no}|{(c.street_phon or '')[:8]}",
        "bk3": f"{c.state}|{c.tax_last4 or ''}",
        "bk4": f"{c.zip5}|{(c.name_norm or '')[:10]}",
    }
