"""Controlled demo tools; no arbitrary SQL, shell, or external mutation is exposed."""
from __future__ import annotations

from supportiq.config import Settings
from supportiq.db import connect


def available_tools() -> list[dict[str, str]]:
    return [
        {"name":"get_customer","permission":"read","description":"Read a customer profile by SupportIQ customer ID."},
        {"name":"get_ticket","permission":"read","description":"Read a support ticket by ticket ID."},
        {"name":"get_order_status","permission":"read","description":"Demo lookup; reports external order system unavailable."},
    ]


def call_tool(settings: Settings, name: str, arguments: dict) -> dict:
    if name == "get_customer":
        cid=str(arguments.get("customer_id",""))
        with connect(settings.db_path) as db: row=db.execute("SELECT id,name,external_customer_id FROM customers WHERE id=?",(cid,)).fetchone()
        return {"ok":bool(row),"result":dict(row) if row else None,"mode":"demo","source":"local demo customer store"}
    if name == "get_ticket":
        tid=str(arguments.get("ticket_id",""))
        with connect(settings.db_path) as db: row=db.execute("SELECT id,subject,status,priority,category FROM tickets WHERE id=?",(tid,)).fetchone()
        return {"ok":bool(row),"result":dict(row) if row else None,"mode":"demo"}
    if name == "get_order_status": return {"ok":False,"result":None,"mode":"demo","error":"No external order system is connected. Verify manually."}
    return {"ok":False,"result":None,"error":"Tool is not permitted."}
