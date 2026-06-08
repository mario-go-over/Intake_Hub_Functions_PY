import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env (look in project root first, then CWD)
load_dotenv()

def _need(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"Missing required env var: {name}")
    return v

# ---------- Postgres / crypto / batch ----------
PG_DSN     = _need("PG_DSN")
HMAC_KEY   = os.environ.get("HMAC_KEY", "")[:32].encode("utf-8")
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "2000"))

# ---------- Multi-source MSSQL ----------
# MSSQL_SOURCES=spec,excesssurplus
# Expected env keys (case/format-insensitive):
#   MSSQL_DSN_SPEC=...
#   MSSQL_DSN_EXCESSSURPLUS=...
def _norm_key_fragment(name: str) -> str:
    # Uppercase and replace non-alphanum with underscore
    out = []
    for ch in name:
        out.append(ch.upper() if ch.isalnum() else "_")
    return "".join(out)

def _load_sources():
    names = os.environ.get("MSSQL_SOURCES", "").strip()
    if not names:
        # Backward compatibility: allow single source via MSSQL_DSN
        dsn = os.environ.get("MSSQL_DSN")
        if not dsn:
            raise RuntimeError("Provide MSSQL_SOURCES or MSSQL_DSN in .env")
        return [{"name": "primary", "dsn": dsn}]
    sources = []
    for raw in names.split(","):
        name = raw.strip()
        if not name:
            continue
        frag = _norm_key_fragment(name)           # e.g. 'excesssurplus' -> 'EXCESSSURPLUS'
        env_key = f"MSSQL_DSN_{frag}"             # MSSQL_DSN_EXCESSSURPLUS
        dsn = os.environ.get(env_key)
        if not dsn:
            # Also try exact-case name (rare)
            alt = f"MSSQL_DSN_{name}"
            dsn = os.environ.get(alt)
        if not dsn:
            raise RuntimeError(f"Missing DSN for source '{name}'. Set {env_key}=... in .env")
        sources.append({"name": name, "dsn": dsn})
    return sources

MSSQL_SOURCES = _load_sources()

# ---------- Per-source checkpoints ----------
CHECKPOINT_DIR = Path(os.environ.get("CHECKPOINT_DIR", ".")).resolve()
CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

def checkpoint_path(source_name: str) -> Path:
    # Source-safe filename: same normalization as above to avoid odd characters
    frag = _norm_key_fragment(source_name)
    return CHECKPOINT_DIR / f".etl_last_run.{frag}.txt"
