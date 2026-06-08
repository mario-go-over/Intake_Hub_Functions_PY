from typing import Iterable, Mapping
import psycopg2
import psycopg2.extras

from config import PG_DSN

def connect():
    # psycopg2 will default to autocommit=False (transactions)
    return psycopg2.connect(PG_DSN)

UPSERT_SQL = """
INSERT INTO public.clearanceinsureds (
  insured_guid, name_norm, name_phon, house_no, street_core, street_phon,
  city_norm, state_norm, zip5, tax_last4, tax_hash,
  bk1, bk2, bk3, bk4, source_name, last_modified
) VALUES (
  %(insured_guid)s, %(name_norm)s, %(name_phon)s, %(house_no)s, %(street_core)s, %(street_phon)s,
  %(city_norm)s, %(state_norm)s, %(zip5)s, %(tax_last4)s, %(tax_hash)s,
  %(bk1)s, %(bk2)s, %(bk3)s, %(bk4)s, %(source_name)s, NOW()
)
ON CONFLICT (insured_guid) DO UPDATE SET
  name_norm     = EXCLUDED.name_norm,
  name_phon     = EXCLUDED.name_phon,
  house_no      = EXCLUDED.house_no,
  street_core   = EXCLUDED.street_core,
  street_phon   = EXCLUDED.street_phon,
  city_norm     = EXCLUDED.city_norm,
  state_norm    = EXCLUDED.state_norm,
  zip5          = EXCLUDED.zip5,
  tax_last4     = EXCLUDED.tax_last4,
  tax_hash      = EXCLUDED.tax_hash,
  bk1           = EXCLUDED.bk1,
  bk2           = EXCLUDED.bk2,
  bk3           = EXCLUDED.bk3,
  bk4           = EXCLUDED.bk4,
  source_name   = EXCLUDED.source_name,
  last_modified = NOW();
"""

def upsert_clearanceinsureds(conn, payload: Iterable[Mapping]):
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, UPSERT_SQL, payload, page_size=500)
