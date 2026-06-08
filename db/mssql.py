# util/db/mssql.py
import datetime as dt
from typing import List, Optional, Dict, Any, Tuple

import pyodbc
from config import BATCH_SIZE


def connect(dsn: str) -> pyodbc.Connection:
    """
    Open a connection to a specific SQL Server source.
    Caller controls commit scope (autocommit=False).
    """
    return pyodbc.connect(dsn, autocommit=False)


def fetch_vendor_batch(
    conn: pyodbc.Connection,
    since: Optional[dt.datetime],
    batch: int = BATCH_SIZE,
) -> Tuple[List[Dict[str, Any]], Optional[dt.datetime]]:
    """
    Pull a batch from vendor tables (tblInsureds + tblInsuredLocations).
    Assumes columns:
      - tblInsureds.InsuredGUID UNIQUEIDENTIFIER
      - tblInsureds.Name NVARCHAR
      - tblInsuredLocations.Address1/City/State/ZipCode NVARCHAR
      - tblInsureds.FEIN / tblInsureds.SSN NVARCHAR (taxid)
      - tblInsuredLocations.DateAdded DATETIME2 (or DATETIME)
    Returns: (rows, max_last_modified_in_returned_rows)
    """
    sql = f"""
        SELECT TOP (?)
               tblInsureds.InsuredGUID                     AS insured_guid,
               tblInsureds.Name                            AS name,
               tblInsuredLocations.Address1                AS address,
               tblInsuredLocations.City                    AS city,
               tblInsuredLocations.State                   AS state,
               tblInsuredLocations.ZipCode                 AS postal,
               ISNULL(tblInsureds.FEIN, tblInsureds.SSN)   AS taxid,
               CAST(tblInsuredLocations.DateAdded AS DATETIME2) AS last_modified
        FROM dbo.tblInsureds WITH (READPAST)
        INNER JOIN dbo.tblInsuredLocations
            ON tblInsureds.InsuredGUID = tblInsuredLocations.InsuredGUID
        {"WHERE tblInsuredLocations.DateAdded > ?" if since else ""}
        ORDER BY tblInsuredLocations.DateAdded ASC;
    """

    rows: List[Dict[str, Any]] = []
    max_lm: Optional[dt.datetime] = None

    with conn.cursor() as cur:
        if since:
            cur.execute(sql, (batch, since))
        else:
            cur.execute(sql, (batch,))

        cols = [c[0] for c in cur.description]  # keep your original casing
        for rec in cur.fetchall():
            d = dict(zip(cols, rec))
            rows.append(d)
            lm = d.get("last_modified")
            if isinstance(lm, dt.datetime):
                if max_lm is None or lm > max_lm:
                    max_lm = lm

    return rows, max_lm
