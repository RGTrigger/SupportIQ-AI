from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import pandas as pd

from supportiq.config import Settings
from supportiq.communications import SMTPEmailAdapter
from supportiq.db import connect, init_db, seed_demo_data
from supportiq.rag import delete_document, index_text, reindex_document, retrieve
from supportiq.ai import validate_response
from supportiq.mcp_tools import call_tool
from supportiq.services import (add_feedback, analyze, bulk_analyze, create_ticket,
                                daily_brief, metrics, send_approved, send_demo, set_status, ticket)


class SupportIQTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root=Path(self.temp.name)
        self.settings=Settings(root/"app.sqlite3",root/"vectors","demo",None,"openai/gpt-oss-20b")
        init_db(self.settings.db_path); seed_demo_data(self.settings.db_path)

    def tearDown(self): self.temp.cleanup()

    def test_seed_persistence_and_ticket_creation(self):
        self.assertGreater(metrics(self.settings)["total"],0)
        tid=create_ticket(self.settings,"Test Customer","test@example.test","Invoice help","Need my latest invoice","Email")
        self.assertEqual(ticket(self.settings,tid)["customer_name"],"Test Customer")
        with connect(self.settings.db_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages WHERE ticket_id=?",(tid,)).fetchone()[0],1)

    def test_ai_graph_rag_and_timeline(self):
        item=ticket(self.settings,"SUP-1042")
        result=analyze(self.settings,item["id"])
        self.assertEqual(result["analysis"]["category"],"Billing")
        self.assertTrue(result["evidence"])
        self.assertTrue(result["mcp_used"])
        self.assertTrue(result["mcp_succeeded"])
        self.assertIn("problem_summary",result["recommendation"])
        with connect(self.settings.db_path) as db:
            self.assertGreater(db.execute("SELECT COUNT(*) FROM timeline_events WHERE ticket_id=?",(item["id"],)).fetchone()[0],1)
            self.assertGreater(db.execute("SELECT COUNT(*) FROM ai_quality_metrics WHERE ticket_id=?",(item["id"],)).fetchone()[0],0)

    def test_document_ingestion_retrieval_and_unknown_knowledge(self):
        count=index_text(self.settings.db_path,self.settings.vector_path,"test_policy.txt","For product resets, generate a fresh single-use recovery link.")
        self.assertEqual(count,1)
        self.assertTrue(retrieve(self.settings.db_path,self.settings.vector_path,"fresh recovery link"))
        with connect(self.settings.db_path) as db: doc_id=db.execute("SELECT id FROM knowledge_documents WHERE filename='test_policy.txt'").fetchone()[0]
        self.assertEqual(reindex_document(self.settings.db_path,self.settings.vector_path,doc_id),1)
        delete_document(self.settings.db_path,self.settings.vector_path,doc_id)
        with connect(self.settings.db_path) as db: self.assertIsNone(db.execute("SELECT id FROM knowledge_documents WHERE id=?",(doc_id,)).fetchone())

    def test_status_feedback_and_approved_demo_communication(self):
        tid="SUP-1042"; set_status(self.settings,tid,"In Progress")
        with self.assertRaisesRegex(ValueError,"explicitly approve"):
            send_approved(self.settings,tid,"We are reviewing the account details.","Email","maya.patel@example.test")
        with self.assertRaisesRegex(ValueError,"safety validation"):
            send_approved(self.settings,tid,"Your refund is guaranteed and definitely approved.","Email","maya.patel@example.test",agent_approved=True)
        send_demo(self.settings,tid,"We are reviewing the account details.","Email","maya.patel@example.test")
        send_demo(self.settings,tid,"We are reviewing your payment issue.","SMS","+91 90000 00104")
        add_feedback(self.settings,"Agent","private@example.test",5,"General feedback","Helpful workflow")
        with connect(self.settings.db_path) as db:
            sent=db.execute("SELECT delivery_status FROM messages WHERE ticket_id=? AND direction='outbound' ORDER BY channel",(tid,)).fetchall()
            self.assertEqual([r[0] for r in sent],["demo_sent","demo_sent"])
            self.assertEqual(db.execute("SELECT email FROM feedback").fetchone()[0],"private@example.test")
        self.assertEqual(ticket(self.settings,tid)["status"],"In Progress")
        with self.assertRaises(ValueError):
            send_demo(self.settings,tid,"We are reviewing the account details.","Email","maya.patel@example.test")

    def test_bulk_and_daily_brief(self):
        progress=[]
        data,failed=bulk_analyze(self.settings,pd.DataFrame([{"subject":"Subscription charge","description":"Charged but plan not active"}]),lambda done,total:progress.append((done,total)))
        self.assertEqual(failed,0); self.assertEqual(data.iloc[0]["category"],"Billing")
        self.assertEqual(progress,[(1,1)])
        self.assertEqual(data.iloc[0]["analysis_mode"],"Demo")
        report=daily_brief(self.settings)
        self.assertGreater(report["open"],0)
        self.assertIn("tickets_today",report)

    def test_bulk_uses_groq_and_sanitizes_bad_rows(self):
        live=Settings(self.settings.db_path,self.settings.vector_path,"demo","test-key","openai/gpt-oss-20b")
        payload={"category":"Billing","subcategory":"Payment","intent":"Payment issue","priority":"High","severity":"Major","sentiment":"Negative","customer_impact":"Paid access unavailable","key_information":[],"missing_information":[],"urgency_reason":"Paid access is blocked","confidence":0.91,"uncertainty_flags":[]}
        progress=[]
        with patch("supportiq.ai._groq_json",return_value=payload) as model:
            data,failed=bulk_analyze(live,pd.DataFrame([{"subject":"Paid plan","description":"Payment succeeded"},{"subject":"","description":""}]),lambda done,total:progress.append((done,total)))
        self.assertEqual(model.call_count,1)
        self.assertEqual(data.iloc[0]["analysis_mode"],"Groq")
        self.assertEqual(failed,1)
        self.assertNotIn("Payment succeeded",str(data.iloc[1].to_dict()))
        self.assertEqual(progress,[(1,2),(2,2)])

    def test_bulk_rejects_oversized_batches(self):
        with self.assertRaisesRegex(ValueError,"5,000-row limit"):
            bulk_analyze(self.settings,pd.DataFrame([{"subject":"x","description":"y"}] * 5001))

    def test_feedback_rating_is_optional(self):
        add_feedback(self.settings,"","",None,"General feedback","A note without a score")
        with connect(self.settings.db_path) as db:
            self.assertIsNone(db.execute("SELECT rating FROM feedback ORDER BY created_at DESC LIMIT 1").fetchone()[0])

    def test_rag_upload_size_cap(self):
        from supportiq.rag import extract_text
        with self.assertRaisesRegex(ValueError,"10 MB or smaller"):
            extract_text("large.txt",b"x"*(10*1024*1024+1))

    def test_html_badges_escape_dynamic_text(self):
        from supportiq.ui import _pill
        self.assertNotIn("<script>",_pill("<script>alert(1)</script>"))
        self.assertIn("&lt;script&gt;",_pill("<script>alert(1)</script>"))

    def test_smtp_live_requires_credentials_and_reports_provider_acceptance(self):
        incomplete=Settings(self.settings.db_path,self.settings.vector_path,"demo",None,"openai/gpt-oss-20b",email_provider="smtp",email_from="support@example.test",smtp_host="smtp.example.test")
        self.assertFalse(incomplete.email_live_ready)
        with self.assertRaisesRegex(RuntimeError,"username, password"):
            SMTPEmailAdapter(incomplete).send("customer@example.test","Support update","We are reviewing your request.")
        complete=Settings(self.settings.db_path,self.settings.vector_path,"demo",None,"openai/gpt-oss-20b",email_provider="smtp",email_from="support@example.test",smtp_host="smtp.example.test",smtp_username="support",smtp_password="configured",sms_provider="demo")
        self.assertTrue(complete.email_live_ready)
        with patch("supportiq.communications.smtplib.SMTP") as smtp:
            server=smtp.return_value.__enter__.return_value
            server.send_message.return_value={}
            result=SMTPEmailAdapter(complete).send("customer@example.test","Support update","We are reviewing your request.")
        self.assertEqual(result.status,"sent_to_provider")
        self.assertIn("accepted the message",result.detail)
        self.assertNotIn("delivered",result.detail.lower())

    def test_smtp_refused_recipient_is_not_reported_as_accepted(self):
        complete=Settings(self.settings.db_path,self.settings.vector_path,"demo",None,"openai/gpt-oss-20b",email_provider="smtp",email_from="support@example.test",smtp_host="smtp.example.test",smtp_username="support",smtp_password="configured")
        with patch("supportiq.communications.smtplib.SMTP") as smtp:
            server=smtp.return_value.__enter__.return_value
            server.send_message.return_value={"customer@example.test":(550,b"refused")}
            with self.assertRaisesRegex(RuntimeError,"could not accept"):
                SMTPEmailAdapter(complete).send("customer@example.test","Support update","We are reviewing your request.")

    def test_sla_routing_and_safe_failure_paths(self):
        tid="SUP-1042"
        analysis=analyze(self.settings,tid)
        self.assertTrue(analysis["sla_assessment"]["configured"])
        self.assertTrue(rows := call_tool(self.settings,"get_order_status",{"order_id":"not-connected"}))
        self.assertFalse(rows["ok"])
        bad=validate_response("Your refund is guaranteed and definitely approved.",[])
        self.assertFalse(bad["safe"])
        self.assertTrue(bad["issues"])


if __name__=="__main__": unittest.main()
