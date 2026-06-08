# util/sync.py
import datetime as dt
import logging
from typing import Dict, Any, List, Optional

from .config import (
    HMAC_KEY,
    BATCH_SIZE,
    MSSQL_SOURCES,        # [{'name': 'primary','dsn': '...'}, ...]
    checkpoint_path,      # def checkpoint_path(source_name) -> Path
)
from .db import mssql as mssql_db
from .db import postgres as pg_db
from .models import Record
from .models.record import canonicalize
from .normalize.blocking import blocking_keys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("etl.sync")


def _read_checkpoint(path) -> Optional[dt.datetime]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            s = f.read().strip()
            return dt.datetime.fromisoformat(s) if s else None
    except FileNotFoundError:
        return None


def _write_checkpoint(path, ts: dt.datetime) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(ts.replace(tzinfo=None).isoformat())


def _to_row(v: Dict[str, Any], source_name: str) -> Dict[str, Any]:
    """Build canonical + blocking fields for a single vendor row."""
    rec = Record(
        id=str(v["insured_guid"]),
        name=v.get("name") or "",
        address=v.get("address") or "",
        city=v.get("city") or "",
        state=v.get("state") or "",
        zip5=v.get("postal") or "",
        tax_id=v.get("taxid") or "",
    )
    can = canonicalize(rec, hmac_key=HMAC_KEY)
    bks = blocking_keys(can)

    # defensive clamping for wide text columns (DB allows big values; keep generous but bounded)
    def cut(s: Optional[str], n: int) -> Optional[str]:
        return (s[:n] if s is not None else None)

    return {
        "insured_guid": v["insured_guid"],
        "name_norm": cut(can.name_norm, 500),
        "name_phon": cut(can.name_phon, 500),
        "house_no": cut(can.house_no, 16),
        "street_core": cut(can.street_core, 500),
        "street_phon": cut(can.street_phon, 500),
        "city_norm": cut(can.city, 500),
        "state_norm": cut(can.state, 2),
        "zip5": cut(can.zip5, 5),
        "tax_last4": cut(can.tax_last4, 4),
        "tax_hash": bytes.fromhex(can.tax_hash) if can.tax_hash else None,
        "bk1": bks["bk1"],
        "bk2": bks["bk2"],
        "bk3": bks["bk3"],
        "bk4": bks["bk4"],
        "source_name": source_name,
    }


def _sync_one_source(pg, source: Dict[str, str]) -> None:
    """Sync a single MSSQL source into Postgres using a per-source checkpoint."""
    name = source["name"]
    dsn = source["dsn"]
    ckpt_file = checkpoint_path(name)

    last = _read_checkpoint(ckpt_file)
    log.info("Source=%s Starting sync. Last checkpoint: %s", name, last.isoformat() if last else "None")

    ms = mssql_db.connect(dsn)
    total = 0
    try:
        while True:
            # fetch_vendor_batch returns (rows, max_last_modified)
            rows, new_last = mssql_db.fetch_vendor_batch(ms, since=last, batch=BATCH_SIZE)
            if not rows:
                log.info("Source=%s No more rows. Synced %s rows total.", name, total)
                break

            payload: List[Dict[str, Any]] = [_to_row(r, name) for r in rows]

            # Optional debug:
            # distinct_guids = len({p["insured_guid"] for p in payload})
            # log.info("Source=%s Batch size=%d distinct GUIDs=%d", name, len(payload), distinct_guids)

            pg_db.upsert_clearanceinsureds(pg, payload)
            pg.commit()

            total += len(rows)

            if new_last and (last is None or new_last > last):
                last = new_last
                _write_checkpoint(ckpt_file, last)

            # If the batch was smaller than requested, we likely reached the end for now
            if len(rows) < BATCH_SIZE:
                break

        log.info("Source=%s Done. Processed=%d rows. Last checkpoint: %s", name, total, last)
    finally:
        try:
            ms.close()
        except Exception:
            pass


def run_sync() -> None:
    pg = pg_db.connect()
    try:
        for src in MSSQL_SOURCES:
            _sync_one_source(pg, src)
        log.info("All sources complete.")
    finally:
        try:
            pg.close()
        except Exception:
            pass


if __name__ == "__main__":
    run_sync()
