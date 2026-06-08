# util/scorer/app.py
import os
import psycopg2, psycopg2.extras
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, Request, HTTPException
from contextlib import asynccontextmanager
from rapidfuzz.distance import JaroWinkler
import logging

log = logging.getLogger("scorer")

# --- reuse your own code ---
# Use absolute imports from the top-level `normalize` package so imports
# work when the Functions host imports this module as a top-level package.
from normalize.name import normalize_name
from normalize.address import normalize_address
from normalize.taxid import normalize_tax_id
from normalize.blocking import blocking_keys

def name_similarity(a: str, b: str) -> float:
    return JaroWinkler.normalized_similarity(a or "", b or "")

def address_similarity(a_core: str, b_core: str, a_house: str, b_house: str) -> float:
    jw = JaroWinkler.normalized_similarity(a_core or "", b_core or "")
    boost = 0.10 if (a_house and b_house and a_house == b_house) else 0.0
    return min(1.0, 0.7 * jw + boost)

def score(incoming: dict, cand: dict) -> float:
    n = name_similarity(incoming["name_norm"], cand["name_norm"])
    a = address_similarity(incoming["street_core"], cand["street_core"], incoming["house_no"], cand["house_no"])
    tax_avail = bool(incoming["tax_last4"]) and bool(cand.get("tax_last4"))
    tax_match = tax_avail and (incoming["tax_last4"] == (cand.get("tax_last4") or ""))
    if tax_avail:
        return 0.45 * n + 0.35 * a + 0.20 * (1.0 if tax_match else 0.0)
    else:
        return 0.55 * n + 0.45 * a

def connect_pg():
    dsn = os.getenv("PG_DSN")
    if not dsn:
        raise RuntimeError("PG_DSN environment variable not set")
    conn = psycopg2.connect(dsn, cursor_factory=RealDictCursor)
    conn.autocommit = True
    return conn

def ensure_conn(app: FastAPI):
    # Reconnect if dropped
    conn = getattr(app.state, "pg", None)
    if conn is None or getattr(conn, "closed", False):
        try:
            app.state.pg = connect_pg()
        except Exception as e:
            # leave app.state.pg as None and raise an HTTP 503 so callers
            # (endpoints) return a service-unavailable response instead of
            # bringing down ASGI startup.
            app.state.pg = None
            raise HTTPException(status_code=503, detail=f"Database connection error: {str(e)[:200]}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load secrets
    app.state.hmac_key = os.getenv("HMAC_KEY", "")  # used by normalize_tax_id
    app.state.pg = None
    try:
        try:
            app.state.pg = connect_pg()
            # quick warm-up
            try:
                with app.state.pg.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
                log.info("[Startup] Connected to PostgreSQL.")
            except Exception:
                # If the warm-up fails, close the connection and continue.
                try:
                    app.state.pg.close()
                except Exception:
                    pass
                app.state.pg = None
                log.warning("[Startup] PostgreSQL warm-up failed; continuing without DB connection.")
        except Exception as e:
            # Could not connect at startup (DNS, credentials, etc.). Don't fail startup.
            app.state.pg = None
            log.warning(f"[Startup] Could not connect to PostgreSQL: {e}; continuing without DB connection.")

        yield
    finally:
        try:
            if app.state.pg:
                app.state.pg.close()
        except Exception:
            pass

app = FastAPI(title="Duplicate Scorer", version="1.0.0", lifespan=lifespan)

@app.get("/ping")
def ping():
    return {"status": "ok"}

@app.get("/healthz")
def healthz(request: Request):
    try:
        ensure_conn(request.app)
        with request.app.state.pg.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

@app.post("/duplicates/check")
def check_dup(payload: dict, request: Request):
    ensure_conn(request.app)

    # Canonicalize incoming
    n = normalize_name(payload.get("name", ""))
    a = normalize_address(payload.get("address", ""))
    t = normalize_tax_id(payload.get("taxId", ""), hmac_key=request.app.state.hmac_key)

    incoming = {
        "name_norm": n["name_norm"],
        "name_phon": n["name_phon"],
        "house_no": a["house_no"],
        "street_core": a["street_core"],
        "street_phon": a["street_phon"],
        "city": (payload.get("city", "") or "").lower(),
        "state": (payload.get("state", "") or "").lower(),
        "zip5": (payload.get("zip", "") or "")[:5],
        "tax_last4": t["tax_last4"],
        "tax_hash_hex": t["tax_hash"],  # hex string
    }

    # Shortcut #1: exact tax hash
    if incoming["tax_hash_hex"]:
        tax_hash_bytes = bytes.fromhex(incoming["tax_hash_hex"])
        with request.app.state.pg.cursor() as cur:
            cur.execute(
                """
                SELECT insured_guid AS id, name_norm, house_no, street_core,
                       city_norm, state_norm, zip5, tax_last4
                FROM public.clearanceinsureds
                WHERE tax_hash = %s
                LIMIT 5
                """,
                (psycopg2.Binary(tax_hash_bytes),),
            )
            exact_tax = cur.fetchall()
        if exact_tax:
            top = [{
                "id": str(r["id"]),
                "score": 1.0,
                "decision": "AUTO_DUPLICATE",
                "name_norm": r["name_norm"] or "",
                "house_no": r["house_no"] or "",
                "street_core": r["street_core"] or "",
                "city_norm": r["city_norm"] or "",
                "state_norm": r["state_norm"] or "",
                "zip5": (r["zip5"] or "").strip(),
                "tax_last4": (r["tax_last4"] or "").strip(),
            } for r in exact_tax]
            return {"decision": "AUTO_DUPLICATE", "topCandidates": top[:5]}

    # Shortcut #2: exact name+house+zip
    with request.app.state.pg.cursor() as cur:
        cur.execute(
            """
            SELECT insured_guid AS id, name_norm, house_no, street_core,
                   city_norm, state_norm, zip5, tax_last4
            FROM public.clearanceinsureds
            WHERE name_norm = %s AND zip5 = %s AND house_no = %s
            LIMIT 1
            """,
            (incoming["name_norm"], incoming["zip5"], incoming["house_no"]),
        )
        exact = cur.fetchone()
    if exact:
        return {
            "decision": "AUTO_DUPLICATE",
            "topCandidates": [{
                "id": str(exact["id"]),
                "score": 1.0,
                "decision": "AUTO_DUPLICATE",
                "name_norm": exact["name_norm"] or "",
                "house_no": exact["house_no"] or "",
                "street_core": exact["street_core"] or "",
                "city_norm": exact["city_norm"] or "",
                "state_norm": exact["state_norm"] or "",
                "zip5": (exact["zip5"] or "").strip(),
                "tax_last4": (exact["tax_last4"] or "").strip(),
            }],
        }

    # BK lookup + fuzzy scoring
    bks = blocking_keys(type("C", (), incoming))

    sql = """
        SELECT * FROM public.surg_find_dup_candidates_by_bk(
            %s::text, %s::text, %s::text, %s::text,
            %s::int,  %s::int,
            %s::uuid
        )
    """
    params = (bks["bk1"], bks["bk2"], bks["bk3"], bks["bk4"], 200, 500, None)

    try:
        with request.app.state.pg.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database error: {str(e)[:300]}")

    results = []
    for r in rows:
        cand = {
            "id": str(r["insured_id"]),
            "name_norm": r["name_norm"] or "",
            "house_no": r["house_no"] or "",
            "street_core": r["street_core"] or "",
            "city_norm": r["city_norm"] or "",
            "state_norm": r["state_norm"] or "",
            "zip5": (r["zip5"] or "").strip(),
            "tax_last4": (r["tax_last4"] or "").strip(),
        }
        s = round(score(incoming, cand), 4)
        decision = "AUTO_DUPLICATE" if s >= 0.90 else ("REVIEW" if s >= 0.80 else "NOT_DUPLICATE")
        results.append({**cand, "score": s, "decision": decision})

    results.sort(key=lambda x: x["score"], reverse=True)
    final = "NOT_DUPLICATE"
    if results:
        top = results[0]["score"]
        final = "AUTO_DUPLICATE" if top >= 0.90 else ("REVIEW" if top >= 0.80 else "NOT_DUPLICATE")

    return {"decision": final, "topCandidates": results[:5]}
