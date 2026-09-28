from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db(path: Path) -> None:
    with connect(path) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS customers(id TEXT PRIMARY KEY, external_customer_id TEXT, name TEXT NOT NULL, email TEXT, phone TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tickets(id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(id), subject TEXT NOT NULL, description TEXT NOT NULL, channel TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Open', category TEXT, intent TEXT, priority TEXT NOT NULL DEFAULT 'Medium', severity TEXT NOT NULL DEFAULT 'Moderate', sentiment TEXT, customer_impact TEXT, assigned_agent TEXT, assigned_team TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, resolved_at TEXT);
        CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL REFERENCES tickets(id), customer_id TEXT NOT NULL REFERENCES customers(id), channel TEXT NOT NULL, direction TEXT NOT NULL, sender TEXT NOT NULL, content TEXT NOT NULL, external_message_id TEXT, delivery_status TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS outbound_action_keys(idempotency_key TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'complete');
        CREATE TABLE IF NOT EXISTS ai_analyses(id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL REFERENCES tickets(id), analysis_type TEXT NOT NULL, result_json TEXT NOT NULL, model TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ai_recommendations(id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL REFERENCES tickets(id), problem_summary TEXT, likely_cause TEXT, recommended_steps TEXT, escalation_required INTEGER DEFAULT 0, escalation_reason TEXT, evidence_json TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS knowledge_documents(id TEXT PRIMARY KEY, filename TEXT NOT NULL, document_type TEXT, status TEXT NOT NULL, chunk_count INTEGER NOT NULL DEFAULT 0, uploaded_at TEXT NOT NULL, indexed_at TEXT);
        CREATE TABLE IF NOT EXISTS knowledge_chunks(id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE, chunk_index INTEGER NOT NULL, content TEXT NOT NULL, metadata_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit_events(id TEXT PRIMARY KEY, ticket_id TEXT, event_type TEXT NOT NULL, actor_type TEXT NOT NULL, description TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS feedback(feedback_id TEXT PRIMARY KEY, name TEXT, email TEXT, rating INTEGER, feedback_type TEXT NOT NULL, message TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'New', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sla_policies(id TEXT PRIMARY KEY, name TEXT NOT NULL, first_response_minutes INTEGER NOT NULL, resolution_minutes INTEGER NOT NULL, applies_to_category TEXT, applies_to_priority TEXT, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ticket_sla(id TEXT PRIMARY KEY, ticket_id TEXT UNIQUE NOT NULL REFERENCES tickets(id), policy_id TEXT REFERENCES sla_policies(id), first_response_due_at TEXT, resolution_due_at TEXT, first_response_status TEXT, resolution_status TEXT, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS similarity_matches(id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, matched_ticket_id TEXT NOT NULL, match_type TEXT NOT NULL, similarity_score REAL NOT NULL, reason TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS routing_recommendations(id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, recommended_team TEXT, recommended_agent TEXT, reason TEXT, confidence REAL, accepted INTEGER, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ai_quality_metrics(id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, analysis_id TEXT, confidence REAL, validation_passed INTEGER, human_edited INTEGER DEFAULT 0, regenerated INTEGER DEFAULT 0, rag_evidence_available INTEGER DEFAULT 0, mcp_used INTEGER DEFAULT 0, mcp_succeeded INTEGER DEFAULT 0, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS timeline_events(id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, customer_id TEXT, event_type TEXT NOT NULL, actor_type TEXT NOT NULL, summary TEXT NOT NULL, metadata_json TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS copilot_messages(id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, customer_id TEXT, role TEXT NOT NULL, content TEXT NOT NULL, context_snapshot_json TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS bulk_jobs(id TEXT PRIMARY KEY, filename TEXT, status TEXT NOT NULL, total_rows INTEGER DEFAULT 0, processed_rows INTEGER DEFAULT 0, successful_rows INTEGER DEFAULT 0, failed_rows INTEGER DEFAULT 0, created_at TEXT NOT NULL, completed_at TEXT);
        CREATE INDEX IF NOT EXISTS idx_ticket_status ON tickets(status, priority);
        CREATE INDEX IF NOT EXISTS idx_ticket_customer ON tickets(customer_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_messages_ticket ON messages(ticket_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_timeline_ticket ON timeline_events(ticket_id, created_at);
        """)
        cols={r[1] for r in db.execute("PRAGMA table_info(outbound_action_keys)")}
        if "status" not in cols: db.execute("ALTER TABLE outbound_action_keys ADD COLUMN status TEXT NOT NULL DEFAULT 'complete'")


def audit(path: Path, event: str, description: str, ticket_id: str | None = None, actor: str = "system", metadata: dict[str, Any] | None = None) -> None:
    stamp = now_iso()
    with connect(path) as db:
        db.execute("INSERT INTO audit_events VALUES(?,?,?,?,?,?)", (str(uuid.uuid4()), ticket_id, event, actor, description, stamp))
        if ticket_id:
            row = db.execute("SELECT customer_id FROM tickets WHERE id=?", (ticket_id,)).fetchone()
            db.execute("INSERT INTO timeline_events VALUES(?,?,?,?,?,?,?,?)", (str(uuid.uuid4()), ticket_id, row[0] if row else None, event, actor, description, json.dumps(metadata or {}), stamp))


def seed_demo_data(path: Path) -> None:
    with connect(path) as db:
        if db.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]:
            return
        stamp = datetime.now(timezone.utc)
        customers = [
            ("cus_104", "C-104", "Maya Patel", "maya.patel@example.test", "+91 90000 00104"),
            ("cus_208", "C-208", "Arjun Mehta", "arjun.mehta@example.test", "+91 90000 00208"),
            ("cus_317", "C-317", "Sara Khan", "sara.khan@example.test", "+91 90000 00317"),
            ("cus_422", "C-422", "Dev Shah", "dev.shah@example.test", "+91 90000 00422"),
        ]
        for cid, ext, name, email, phone in customers:
            db.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)", (cid, ext, name, email, phone, stamp.isoformat(), stamp.isoformat()))
        tickets = [
            ("SUP-1042", "cus_104", "Premium charged, account still on free plan", "I was charged ₹999 for premium but my account still shows the free plan.", "Email", "Open", "Billing", "Subscription issue", "High", "Major", "Frustrated", "Paid access unavailable", "Billing Team", 0),
            ("SUP-1041", "cus_208", "Cannot reset my password", "The reset link expires as soon as I open it. I have tried twice.", "Web", "In Progress", "Account Access", "Password reset", "Medium", "Moderate", "Negative", "Account access blocked", "Technical Support", 4),
            ("SUP-1040", "cus_317", "Where can I download my invoices?", "I need the last three monthly invoices for accounting.", "SMS", "Open", "Billing", "Invoice request", "Low", "Minor", "Neutral", "Invoice access needed", "Billing Team", 9),
            ("SUP-1039", "cus_104", "App crashes during export", "Export fails with an error after processing for a few minutes.", "Email", "Resolved", "Technical", "Export failure", "Medium", "Moderate", "Negative", "Work delayed", "Technical Support", 26),
            ("SUP-1038", "cus_422", "Feature suggestion: keyboard shortcuts", "It would be helpful to navigate the workspace with keyboard shortcuts.", "Web", "Pending", "Feedback", "Feature request", "Low", "Minor", "Positive", "Convenience improvement", "Product Support", 41),
            ("SUP-1037", "cus_208", "Duplicate charge on last receipt", "My card was charged two times for the same monthly subscription.", "Email", "Escalated", "Billing", "Duplicate charge", "Urgent", "Critical", "Very negative", "Potential financial harm", "Billing Team", 53),
        ]
        for tid, cid, subject, desc, channel, status, category, intent, priority, severity, sentiment, impact, team, age in tickets:
            created = (stamp - timedelta(hours=age)).isoformat()
            db.execute("INSERT INTO tickets(id,customer_id,subject,description,channel,status,category,intent,priority,severity,sentiment,customer_impact,assigned_agent,assigned_team,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (tid,cid,subject,desc,channel,status,category,intent,priority,severity,sentiment,impact,"Support Team",team,created,created))
            db.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?)", (str(uuid.uuid4()),tid,cid,channel,"inbound",next(c[2] for c in customers if c[0]==cid),desc,None,"received",created))
            created_dt=stamp-timedelta(hours=age)
            first_due=(created_dt+timedelta(hours=1)).isoformat()
            resolution_due=(created_dt+timedelta(hours=48)).isoformat()
            first_state="breached" if age>1 else "on_track"
            remain=48-age
            resolution_state="breached" if remain<0 and status not in ("Resolved","Closed") else "at_risk" if remain<=6 and status not in ("Resolved","Closed") else "on_track"
            db.execute("INSERT INTO ticket_sla VALUES(?,?,?,?,?,?,?,?)", (str(uuid.uuid4()),tid,None,first_due,resolution_due,first_state,resolution_state,stamp.isoformat()))
            db.execute("INSERT INTO timeline_events VALUES(?,?,?,?,?,?,?,?)", (str(uuid.uuid4()),tid,cid,"ticket_created","system","Ticket created from customer request",json.dumps({"channel":channel}),created))
        policy = str(uuid.uuid4())
        db.execute("INSERT INTO sla_policies VALUES(?,?,?,?,?,?,?,?)", (policy,"Demo support targets",60,48,None,None,1,stamp.isoformat()))
        for row in [("Premium subscriptions", "Subscriptions activate after a successful payment. If the account remains on the free plan, confirm that the payment is settled, then sign out and back in. Do not ask customers to share full card details. If activation has not completed after 15 minutes, route to Billing for payment reconciliation."), ("Password reset troubleshooting", "Password reset links are single use and expire after 30 minutes. Request a new link, open the newest message, and check that the browser uses the same email account. Escalate if a fresh link expires immediately."), ("Invoices and receipts", "Monthly invoices are available under Account Settings → Billing → Invoices. Customers can download the latest invoice and previous billing documents from that page. Never request full payment-card data."), ("Duplicate payment review", "For a suspected duplicate charge, record the transaction date and amount, avoid promising a refund before review, and route the case to Billing for transaction reconciliation."), ("Export troubleshooting", "For failed exports, retry once with a smaller date range and confirm the account has permission to export. Capture the error timestamp and export format. Escalate repeated failures with those details.")]:
            docid = str(uuid.uuid4())
            db.execute("INSERT INTO knowledge_documents VALUES(?,?,?,?,?,?,?)", (docid,row[0],"Support guide","Indexed",1,stamp.isoformat(),stamp.isoformat()))
            db.execute("INSERT INTO knowledge_chunks VALUES(?,?,?,?,?)", (str(uuid.uuid4()),docid,0,row[1],json.dumps({"filename":row[0],"source":"Built-in demo knowledge"})))
