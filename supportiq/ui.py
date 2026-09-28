from __future__ import annotations

import io
import json
import base64
import html
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from supportiq.ai import validate_response
from supportiq.config import Settings
from supportiq.db import connect
from supportiq.mcp_tools import available_tools, call_tool
from supportiq.rag import delete_document, extract_text, index_text, reindex_document, retrieve
from supportiq.services import (add_feedback, analyze, bulk_analyze, copilot_answer,
                                create_ticket, daily_brief, metrics, rows, send_approved,
                                accept_routing, set_status, sla_for, ticket, tickets)

NAV = {
    "Overview": ["Dashboard"],
    "Workspace": ["Inbox", "Tickets", "Customers", "Conversations"],
    "AI Operations": ["AI Analysis", "Knowledge Base", "AI Quality", "Bulk Analysis", "Daily Brief"],
    "Insights": ["Analytics"],
    "System": ["Settings"],
    "About": ["About / Creator", "Feedback & Review"],
}
CREATOR_ASSETS = Path(__file__).resolve().parents[1] / "assets" / "creator"
CREATOR_LINKS = {
    "LinkedIn": "https://www.linkedin.com/in/rgtrigger/",
    "GitHub": "https://github.com/RGTrigger",
    "Email": "mailto:rgtrigger.ai.dev@gmail.com",
    "Medium": "https://medium.com/@rgtrigger.ai.dev",
}

def _pill(text: str) -> str:
    styles={"Urgent":"#fee2e2","High":"#ffedd5","Medium":"#fef3c7","Low":"#dcfce7","Open":"#dbeafe","Escalated":"#fee2e2","Resolved":"#dcfce7"}
    color=styles.get(text,"#eef2ff")
    return f'<span style="display:inline-block;background:{color};padding:4px 10px;border-radius:99px;font-size:12px;font-weight:650;color:#253148">{html.escape(str(text))}</span>'

def _footer():
    project_repo=os.getenv("SUPPORTIQ_GITHUB_REPO","").strip().rstrip("/")
    with st.container(key="global_footer"):
        with st.container(key="footer-columns"):
            app,quick,connect,support=st.columns([2.1,1.05,1.1,1.25],gap="medium")
            with app:
                st.markdown("<div class='footer-brand'><span>🎧</span><div><b>SupportIQ AI</b><small>AI-Powered Customer Support Platform</small></div></div>",unsafe_allow_html=True)
                st.write("An open source project by Gaurav to demonstrate real-world AI applications for customer support using LLMs, RAG and LangGraph.")
            with quick:
                st.markdown("**Quick Links**")
                st.button("♙  About Creator",key="footer_about",on_click=_navigate_to_creator,width="stretch")
                st.button("▢  Give Feedback",key="footer_feedback",on_click=_navigate_to_feedback,width="stretch")
                if project_repo: st.link_button("⌘  GitHub Repository",project_repo,width="stretch")
                st.button("▧  Documentation",key="footer_docs",on_click=_navigate_to_page,args=("Knowledge Base",),width="stretch")
                st.link_button("⚑  Report Issue","mailto:rgtrigger.ai.dev@gmail.com?subject=SupportIQ%20AI%20Issue",width="stretch")
            with connect:
                st.markdown("**Connect with Me**")
                st.markdown("[▣  LinkedIn](https://www.linkedin.com/in/rgtrigger/)  \n[◉  GitHub](https://github.com/RGTrigger)  \n[✉  Email](mailto:rgtrigger.ai.dev@gmail.com)  \n[◎  Medium](https://medium.com/@rgtrigger.ai.dev)")
            with support:
                st.markdown("**Support the Project**")
                if project_repo: st.link_button("⭐  Star this project",project_repo,width="stretch")
                else: st.button("⭐  Star this project",key="footer_star_project",disabled=True,width="stretch")
                st.caption("If you find this project useful, consider starring the repository and sharing your feedback.")
        st.markdown("<div class='footer-rule'></div>",unsafe_allow_html=True)
        left,right=st.columns([3,1])
        left.caption("© 2025 SupportIQ AI. Created by Gaurav. Built with Streamlit, LangGraph and ❤️ for the AI community.")
        right.markdown("<span style='color:#53647f;font-size:10px'>Privacy　 |　 Terms　 |　 Contact</span>",unsafe_allow_html=True)

def _header(page: str, subtitle: str):
    st.markdown(f"<div class='page-header'><h1>{html.escape(page)}</h1><p>{html.escape(subtitle)}</p></div>",unsafe_allow_html=True)

def _selected_ticket(settings: Settings):
    ts=tickets(settings)
    if not ts: st.info("No tickets yet. Create one from the Inbox page."); return None
    ids=[t["id"] for t in ts]
    current=st.session_state.get("selected_ticket")
    idx=ids.index(current) if current in ids else 0
    choice=st.selectbox("Ticket",ids,index=idx,format_func=lambda tid: next(f"{tid} · {x['subject']}" for x in ts if x['id']==tid))
    st.session_state.selected_ticket=choice
    return ticket(settings,choice)

def _ticket_table(items):
    if not items: st.info("Nothing needs attention here right now."); return
    df=pd.DataFrame(items)
    cols=[c for c in ["id","subject","customer_name","channel","status","priority","severity","assigned_team","created_at"] if c in df]
    st.dataframe(df[cols],hide_index=True,width="stretch")

def _navigate_to_ticket(ticket_id: str):
    st.session_state.selected_ticket=ticket_id
    st.session_state.page_nav="Ticket Workspace"

def _navigate_to_feedback():
    st.session_state.page_nav="Feedback & Review"

def _navigate_to_creator():
    st.session_state.page_nav="About / Creator"

def _navigate_to_page(page: str):
    st.session_state.page_nav=page

def _creator_avatar(width: int=142):
    photo=CREATOR_ASSETS / "creator_photo.jpeg"
    if not photo.exists(): return
    encoded=base64.b64encode(photo.read_bytes()).decode("ascii")
    st.markdown(f"<div style='display:flex;justify-content:flex-start'><img src='data:image/jpeg;base64,{encoded}' style='width:{width}px;height:{width}px;object-fit:cover;object-position:center 38%;border-radius:50%;border:4px solid #bf8cf6;padding:3px;background:white;box-shadow:0 0 0 4px #dceeff,0 6px 18px #173d7a20'></div>",unsafe_allow_html=True)

def _ticket_detail(settings: Settings, item: dict, result: dict | None = None):
    st.markdown(f"### {html.escape(str(item['subject']))} &nbsp; {_pill(item['status'])} &nbsp; {_pill(item['priority'])}",unsafe_allow_html=True)
    c1,c2,c3,c4=st.columns(4); c1.metric("Customer",item["customer_name"]); c2.metric("Channel",item["channel"]); c3.metric("Severity",item.get("severity") or "—"); c4.metric("Team",item.get("assigned_team") or "Unassigned")
    st.write(item["description"])
    sla=sla_for(settings,item["id"])
    if sla: st.caption(f"SLA · first response {sla['first_response_status'].replace('_',' ')} · resolution {sla['resolution_status'].replace('_',' ')} · target {sla['resolution_due_at'] or 'not configured'}")
    if result:
        a=result["analysis"]; st.subheader("AI intelligence")
        x,y,z=st.columns(3); x.metric("Category",a.get("category","—")); y.metric("Intent",a.get("intent","—")); z.metric("Confidence",f"{a.get('confidence',0):.0%}")
        if a.get("confidence",0)<.6: st.warning("Low confidence. Verify classification and recommendations before taking action.")
        x,y,z=st.columns(3); x.markdown("**Priority**\n\n"+_pill(a.get("priority","Medium")),unsafe_allow_html=True); y.markdown("**Sentiment**\n\n"+a.get("sentiment","—")); z.markdown("**Severity**\n\n"+a.get("severity","—"))
        if a.get("uncertainty_flags"): st.warning("Uncertainty: "+" · ".join(a["uncertainty_flags"]))
        if a.get("missing_information"): st.info("Missing information: "+", ".join(a["missing_information"]))
        if result.get("errors"): st.warning(" ".join(result["errors"]))
        st.subheader("AI action plan")
        st.write(result.get("investigation","")); rec=result["recommendation"]
        if a.get("key_information"):
            st.markdown("**Known information**")
            for fact in a["key_information"]: st.markdown(f"- {fact}")
        if result.get("tool_results"):
            st.markdown("**Checks performed**")
            for outcome in result["tool_results"]: st.markdown(f"- {outcome['tool']}: {'customer record found' if outcome['ok'] else 'record unavailable'} ({outcome['mode']})")
        st.markdown("**Recommended steps**")
        for step in rec.get("recommended_steps",[]): st.markdown(f"- {step}")
        st.caption("Likely cause · "+str(rec.get("likely_cause","—")))
        st.caption("Customer action · "+str(rec.get("customer_action") or "No specific customer action identified."))
        st.caption("Internal action · "+str(rec.get("internal_action") or "Review and document the outcome."))
        if rec.get("escalation_required"): st.error("Escalation recommended · "+str(rec.get("escalation_reason")))
        st.subheader("Knowledge evidence")
        if result.get("evidence"):
            for hit in result["evidence"]:
                with st.expander(f"{hit['source']} · relevance {hit['score']:.0%}"): st.write(hit["content"])
        else: st.warning("No knowledge evidence retrieved. Verify policy or procedure manually.")
        st.subheader("Routing recommendation")
        rt=result.get("routing",{}); st.info(f"{rt.get('team','General Support')} · {rt.get('reason','')}")
        if st.button("Accept recommended team assignment",key=f"route_{item['id']}"):
            try: accept_routing(settings,item["id"]); st.success("Team assignment updated."); st.rerun()
            except ValueError as e: st.warning(str(e))

def _dashboard(settings: Settings):
    m=metrics(settings); ts=tickets(settings); states=[sla_for(settings,t["id"]) for t in ts]
    title_col,period_col=st.columns([6,1],vertical_alignment="center")
    with title_col: st.markdown("<div class='page-header dashboard-header'><span class='sr-only'>Dashboard</span><h1>Good morning, Gaurav ☀️</h1><p>Here's your support operations overview.</p></div>",unsafe_allow_html=True)
    period_col.markdown("<div class='period-filter'>Last 7 days</div>",unsafe_allow_html=True)
    st.markdown("<div style='height:33px'></div>",unsafe_allow_html=True)
    open_items=[t for t in ts if t["status"] not in {"Resolved","Closed"}]
    risks=sum(1 for s in states if s and (s["first_response_status"] in {"at_risk","breached"} or s["resolution_status"] in {"at_risk","breached"}))
    sla_rows=[s for s in states if s]
    met_count=sum(1 for s in sla_rows if s["first_response_status"] in {"met","on_track"} and s["resolution_status"] in {"met","on_track"})
    met_rate=met_count/max(1,len(sla_rows)); resolution_rate=m["resolved"]/max(1,m["total"])
    def kpi(label,value,tone,delta,icon):
        return f"<div class='ref-kpi' style='--tone:{tone}'><span class='ref-kpi-icon'>{icon}</span><small>{label}</small><strong>{value}</strong><em>{delta}</em></div>"
    kpis=st.columns(4,gap="small")
    items=[("Open Tickets",m["open"],"#365BFF",f"{len(open_items)} active","●"),("SLA At Risk",risks,"#E9A022",f"{risks} need attention","●"),("AI Resolution Rate",f"{resolution_rate:.0%}","#16A879",f"{m['resolved']} resolved","●"),("Escalations",m["escalated"],"#E94B62",f"{m['high']} high priority","●")]
    for col,item in zip(kpis,items): col.markdown(kpi(*item),unsafe_allow_html=True)
    st.markdown("<div style='height:30px'></div>",unsafe_allow_html=True)
    df=pd.DataFrame(ts)
    left,center,right=st.columns([1.85,1.05,.95],gap="small")
    with left:
        with st.container(border=True):
            st.subheader("Ticket Volume Trend")
            if not df.empty:
                end=datetime.now(timezone.utc).date(); days=pd.date_range(end=end,periods=7).date
                daily=df.assign(day=pd.to_datetime(df.created_at,utc=True).dt.date,Series=df.status.map({"Open":"Open tickets","In Progress":"Open tickets","Pending":"Open tickets","Resolved":"Resolved","Closed":"Resolved","Escalated":"Escalated"})).dropna(subset=["Series"]).groupby(["day","Series"]).size().rename("Tickets").reset_index()
                daily=daily[daily.day.isin(days)]; grid=pd.MultiIndex.from_product([days,["Open tickets","Resolved","Escalated"]],names=["day","Series"])
                daily=daily.set_index(["day","Series"]).reindex(grid,fill_value=0).reset_index()
                chart=px.line(daily,x="day",y="Tickets",color="Series",markers=True,color_discrete_map={"Open tickets":"#365BFF","Resolved":"#7656E8","Escalated":"#EA4B62"})
                chart.update_layout(height=254,margin=dict(l=10,r=10,t=4,b=2),paper_bgcolor="rgba(0,0,0,0)",plot_bgcolor="rgba(0,0,0,0)",legend=dict(orientation="h",y=1.12,x=0,font=dict(size=10)),font=dict(color="#71809D",size=10),xaxis_title=None,yaxis_title=None)
                chart.update_xaxes(showgrid=False,zeroline=False); chart.update_yaxes(gridcolor="#E8EDF5",zeroline=False)
                st.plotly_chart(chart,width="stretch",config={"displayModeBar":False})
            else: st.info("Ticket trends will appear once requests arrive.")
    with center:
        with st.container(border=True):
            st.subheader("Tickets by Category")
            if not df.empty:
                category=df.category.fillna("Unclassified").value_counts().rename_axis("Category").reset_index(name="Tickets")
                chart=px.pie(category,names="Category",values="Tickets",hole=.62,color_discrete_sequence=["#365BFF","#7957E8","#19A2B4","#E9A022","#E94B62"])
                chart.update_traces(textinfo="percent",textposition="inside",textfont_size=10)
                chart.update_layout(height=250,margin=dict(l=0,r=0,t=4,b=2),paper_bgcolor="rgba(0,0,0,0)",showlegend=True,legend=dict(font=dict(size=10),orientation="v",x=1.02,y=.5),font=dict(color="#71809D",size=10))
                st.plotly_chart(chart,width="stretch",config={"displayModeBar":False})
            else: st.info("No categorized tickets yet.")
    with right:
        with st.container(border=True):
            st.subheader("Recent Activity")
            for item in ts[:5]:
                status_color={"Urgent":"#E94B62","High":"#E94B62","Medium":"#E9A022","Low":"#19A879"}.get(item["priority"],"#365BFF")
                st.markdown(f"<div class='activity-ref'><b>{html.escape(str(item['id']))}</b><span style='color:{status_color}'>{html.escape(str(item['priority']))}</span><small>{html.escape(str(item['subject']))}</small></div>",unsafe_allow_html=True)
            if not ts: st.caption("New ticket activity will appear here.")
    st.markdown("<div style='height:24px'></div>",unsafe_allow_html=True)
    with st.container(border=True):
        st.subheader("SLA Health")
        sla_buckets={"On Time":0,"At Risk":0,"Breached":0}
        for state in sla_rows:
            pair={state["first_response_status"],state["resolution_status"]}
            label="Breached" if "breached" in pair else "At Risk" if "at_risk" in pair else "On Time"
            sla_buckets[label]+=1
        on_time=sla_buckets["On Time"]/max(1,len(sla_rows)); at_risk=sla_buckets["At Risk"]/max(1,len(sla_rows)); breached=sla_buckets["Breached"]/max(1,len(sla_rows))
        cols=st.columns(3,gap="small")
        for col,label,value,color in zip(cols,["On Time","At Risk","Breached"],[on_time,at_risk,breached],["#16A879","#E9A022","#E94B62"]):
            col.markdown(f"<div class='sla-label'><b>{label}</b><strong style='color:{color}'>{value:.0%}</strong></div>",unsafe_allow_html=True); col.progress(min(value,1.0))

def _inbox(settings: Settings):
    _header("Inbox","Unified customer communication across email and SMS.")
    filter_row=st.columns([2.55,1,1,1,1,1],gap="small")
    query=filter_row[0].text_input("Search messages",placeholder="⌕  Search messages…",key="inbox_search",label_visibility="collapsed")
    selected_filter=st.session_state.get("inbox_channel","All")
    for col,label in zip(filter_row[1:], ["All","Email","SMS","Internal","Unread"]):
        if col.button(label,key=f"inbox_filter_{label}",type="primary" if selected_filter==label else "secondary",width="stretch"):
            st.session_state.inbox_channel=label; st.rerun()
    st.markdown("<div style='height:20px'></div>",unsafe_allow_html=True)
    data=tickets(settings,query)
    if selected_filter in {"Email","SMS"}: data=[t for t in data if t["channel"]==selected_filter]
    elif selected_filter=="Internal": data=[t for t in data if rows(settings,"SELECT 1 FROM messages WHERE ticket_id=? AND direction='outbound' LIMIT 1",(t["id"],))]
    elif selected_filter=="Unread": data=[t for t in data if t["status"] not in {"Resolved","Closed"}]
    if not data: st.info("No messages match this filter.")
    else:
        ids=[t["id"] for t in data]; chosen=st.session_state.get("inbox_selected")
        if chosen not in ids: chosen=ids[0]; st.session_state.inbox_selected=chosen
        item=next(t for t in data if t["id"]==chosen)
        left,right=st.columns([1,1.08],gap="large")
        with left:
            with st.container(border=True):
                st.subheader("Messages")
                for ticket_item in data:
                    msgs=rows(settings,"SELECT content,created_at FROM messages WHERE ticket_id=? AND direction='inbound' ORDER BY created_at DESC LIMIT 1",(ticket_item["id"],))
                    preview=msgs[0]["content"] if msgs else ticket_item["description"]
                    st.button(f"{ticket_item['customer_name']}  ·  {ticket_item['category']}",key=f"inbox_select_{ticket_item['id']}",on_click=lambda tid=ticket_item['id']:st.session_state.update(inbox_selected=tid),type="secondary",width="stretch")
                    st.caption(f"{preview[:72]}{'…' if len(preview)>72 else ''}  ·  {ticket_item['id']}")
        with right:
            with st.container(border=True):
                st.subheader("Conversation Preview")
                st.markdown(f"### {html.escape(str(item['customer_name']))}  ·  {_pill(item['priority'])}",unsafe_allow_html=True)
                st.caption(f"{item['id']} · {item['category']} · {item['channel']}")
                for msg in rows(settings,"SELECT sender,direction,content,created_at FROM messages WHERE ticket_id=? ORDER BY created_at",(chosen,))[-8:]:
                    style="customer-message" if msg["direction"]=="inbound" else "agent-message"
                    st.markdown(f"<div class='{style}'><b>{html.escape(str(msg['sender']))}</b><br>{html.escape(str(msg['content']))}</div>",unsafe_allow_html=True)
                draft=st.text_area("Reply draft · approval required before sending",key=f"inbox_draft_{chosen}",placeholder="Type a reply (not sent until approved)…",height=80)
                validate_col,send_col=st.columns([1,1],gap="small")
                if validate_col.button("Validate",key=f"inbox_validate_{chosen}"):
                    verdict=validate_response(draft)
                    if verdict["safe"]: st.success("Draft passed basic response validation.")
                    else: st.warning("Draft needs review: "+" · ".join(verdict["issues"]))
                if send_col.button("Review & Send",key=f"inbox_send_{chosen}",type="primary"):
                    st.session_state[f"draft_{chosen}"]=draft
                    st.session_state.selected_ticket=chosen
                    st.session_state.page_nav="Ticket Workspace"
                    st.rerun()
    with st.expander("＋ Create a support request"):
        with st.form("new_ticket"):
            c1,c2=st.columns(2); name=c1.text_input("Customer name"); email=c2.text_input("Email (optional)"); phone=c1.text_input("Phone (optional)"); channel=c2.selectbox("Channel",["Email","SMS","Web"]); subject=st.text_input("Subject"); message=st.text_area("Customer message",height=120)
            ok=st.form_submit_button("Create ticket",type="primary")
        if ok:
            try:
                tid=create_ticket(settings,name,email,subject,message,channel,phone); st.session_state.selected_ticket=tid; st.success(f"Ticket {tid} created."); st.rerun()
            except ValueError as e: st.error(str(e))

def _tickets(settings: Settings):
    _header("Tickets","Search, filter and manage the support queue.")
    filters=st.columns([2,1,1,1,1],gap="small")
    search=filters[0].text_input("Search tickets",placeholder="⌕  Search by ticket, subject or customer")
    status=filters[1].selectbox("Status",["All Status","Open","In Progress","Pending","Escalated","Resolved","Closed"])
    priority=filters[2].selectbox("Priority",["All Priority","Urgent","High","Medium","Low"])
    category=filters[3].selectbox("Category",["All Categories"]+sorted({t.get("category") or "Unclassified" for t in tickets(settings)}))
    filters[4].button("＋ New Ticket",type="primary",on_click=lambda:st.session_state.update(page_nav="Inbox"),width="stretch")
    data=tickets(settings,search,None if status=="All Status" else status)
    if priority!="All Priority": data=[t for t in data if t["priority"]==priority]
    if category!="All Categories": data=[t for t in data if (t.get("category") or "Unclassified")==category]
    with st.container(border=True): _ticket_table(data)
    if data:
        ids=[t["id"] for t in data]; sel=st.selectbox("Open ticket workspace",ids,format_func=lambda x:next(f"{x} · {t['subject']}" for t in data if t["id"]==x))
        st.button("Open Ticket Workspace",type="primary",on_click=_navigate_to_ticket,args=(sel,))

def _workspace(settings: Settings):
    item=_selected_ticket(settings)
    if not item:return
    with st.container(key="workspace-title-accessibility"): st.title("Ticket Workspace")
    _header(f"{item['id']} · Ticket Workspace",f"Tickets / {item['id']}")
    st.markdown(f"### {item['id']}　{item['subject']}")
    a,b=st.columns([3,1]);
    with b:
        state=st.selectbox("Update status",["Open","In Progress","Pending","Escalated","Resolved","Closed"],index=["Open","In Progress","Pending","Escalated","Resolved","Closed"].index(item["status"]) if item["status"] in ["Open","In Progress","Pending","Escalated","Resolved","Closed"] else 0)
        confirm_close=st.checkbox("Confirm resolution/closure",key=f"confirm_close_{item['id']}") if state in {"Resolved","Closed"} else True
        if st.button("Save status",disabled=not confirm_close):
            set_status(settings,item["id"],state); st.success("Status updated."); st.rerun()
    stored=rows(settings,"SELECT result_json FROM ai_analyses WHERE ticket_id=? ORDER BY created_at DESC LIMIT 1",(item["id"],))
    result=None
    if stored:
        try: result=json.loads(stored[0]["result_json"])
        except Exception: result=None
    if st.button("Run AI Analysis",type="primary"):
        with st.status("Running support intelligence workflow…",expanded=True) as status_box:
            st.write("Loading customer/ticket context · retrieving knowledge · investigation · response validation")
            try: result=analyze(settings,item["id"]); st.session_state[f"result_{item['id']}"]=result; status_box.update(label="Analysis complete",state="complete")
            except Exception as exc: status_box.update(label="Analysis unavailable",state="error"); st.error(f"Analysis could not complete ({type(exc).__name__}).")
    result=result or st.session_state.get(f"result_{item['id']}")
    tabs=st.tabs(["Overview","Conversation","AI Analysis","Knowledge","Similar Tickets","Timeline","Tools"])
    with tabs[0]:
        customer_col,analysis_col,copilot_col=st.columns([.95,1.35,1.1],gap="medium")
        with customer_col:
            with st.container(border=True):
                st.subheader("Customer Information")
                st.markdown(f"### {item['customer_name']}"); st.caption(item.get("customer_email") or "No email on record")
                count=rows(settings,"SELECT COUNT(*) n FROM tickets WHERE customer_id=?",(item["customer_id"],))[0]["n"]
                st.caption("Previous Tickets"); st.markdown(f"**{count}**")
                st.caption("Channel"); st.markdown(f"**{item['channel']}**")
        with analysis_col:
            with st.container(border=True):
                st.subheader("AI Analysis & Recommendation")
                if result:
                    a=result["analysis"]
                    row1,row2=st.columns(2); row1.caption("Intent"); row1.markdown(f"**{a.get('intent','—')}**"); row2.caption("Priority"); row2.markdown(f"**{a.get('priority','—')}**")
                    row1,row2=st.columns(2); row1.caption("Sentiment"); row1.markdown(f"**{a.get('sentiment','—')}**"); row2.caption("Confidence"); row2.markdown(f"**{a.get('confidence',0):.0%}**")
                    st.markdown("**Recommended Action**"); st.write(result.get("recommendation",{}).get("problem_summary") or result.get("investigation","Review the full recommendation in AI Analysis."))
                else:
                    st.caption("Intent / Category"); st.write(f"{item.get('intent') or 'Not analyzed'} · {item.get('category') or 'Unclassified'}")
                    st.caption("Priority / Sentiment"); st.write(f"{item['priority']} · {item.get('sentiment') or 'Not analyzed'}")
                    st.info("Run AI Analysis to see grounded recommendations and knowledge evidence.")
        with copilot_col:
            with st.container(border=True):
                st.subheader("SupportIQ Copilot")
                st.caption("Answers use ticket context and retrieved knowledge. The copilot cannot send messages or change records.")
                question=st.text_input("Ask AI about this ticket",key=f"q_{item['id']}",placeholder="Ask AI about this ticket…")
                if st.button("Ask Copilot",key=f"ask_copilot_{item['id']}",type="primary") and question:
                    try: copilot_answer(settings,item["id"],question); st.rerun()
                    except Exception as e: st.error(f"Copilot response unavailable ({type(e).__name__}).")
                prior_messages=rows(settings,"SELECT role,content FROM copilot_messages WHERE ticket_id=? ORDER BY created_at DESC LIMIT 3",(item["id"],))
                for msg in prior_messages[::-1]: st.caption(("SupportIQ AI: " if msg["role"]=="assistant" else "You: ")+msg["content"][:150])
        if result:
            action,sources=st.columns([1,1],gap="medium")
            with action:
                with st.container(border=True):
                    st.subheader("Recommended Action")
                    for step in result.get("recommendation",{}).get("recommended_steps",[])[:5]: st.markdown(f"✓　{step}")
            with sources:
                with st.container(border=True):
                    st.subheader("Knowledge Sources")
                    for hit in result.get("evidence",[])[:4]: st.markdown(f"**{hit['source']}**　{hit['score']:.0%}")
                    if not result.get("evidence"): st.caption("No sources were retrieved for this analysis.")
    with tabs[1]:
        messages=rows(settings,"SELECT * FROM messages WHERE ticket_id=? ORDER BY created_at",(item["id"],))
        for m in messages: st.markdown(f"**{html.escape(str(m['sender']))} · {html.escape(str(m['channel']))} · {html.escape(str(m['direction']))}**  \n{html.escape(str(m['content']))}  \n<small>{html.escape(str(m['created_at']))} · {html.escape(str(m['delivery_status'] or ''))}</small>",unsafe_allow_html=True); st.divider()
        draft=(result or {}).get("draft","")
        prior=st.session_state.get(f"draft_{item['id']}",draft)
        if not prior and messages: prior="Thanks for contacting Support. We’re reviewing your request and will follow up shortly."
        content=st.text_area("Response composer · review and edit before approval",value=prior,height=170,key=f"composer_{item['id']}")
        channel=st.selectbox("Outbound channel",["Email","SMS"],index=0 if item["channel"]=="Email" else 1 if item["channel"]=="SMS" else 0,key=f"channel_{item['id']}")
        recipient=item.get("customer_email") if channel=="Email" else item.get("customer_phone")
        provider=settings.email_provider if channel=="Email" else settings.sms_provider
        live_provider=provider not in {"demo","mock",""}
        provider_ready=settings.email_live_ready if channel=="Email" else settings.sms_live_ready
        provider_note="Approval submits the message to the live provider; final delivery may be asynchronous." if live_provider and provider_ready else "Provider credentials or destination are incomplete; live sending is blocked." if live_provider else "Demo mode records locally; no external message is sent."
        st.caption(f"Recipient: {recipient or 'No saved destination'} · Provider: {provider.upper()} · {provider_note}")
        evidence=(result or {}).get("evidence",[]); verdict=validate_response(content,evidence)
        if verdict["safe"]: st.success("Response validation passed. Agent approval is still required.")
        else: st.warning("Response needs review: "+" · ".join(verdict["issues"]))
        if st.button("Approve & send" if live_provider else "Approve & record demo message",type="primary",disabled=not verdict["safe"] or live_provider and (not provider_ready or not recipient),key=f"approve_{item['id']}"):
            try:
                detail=send_approved(settings,item["id"],content,channel,recipient or "demo customer",subject=item["subject"],agent_approved=True)
                st.success(detail); st.rerun()
            except (ValueError,RuntimeError) as e: st.error(str(e))
    with tabs[2]:
        _ticket_detail(settings,item,result)
    with tabs[3]:
        evidence=(result or {}).get("evidence",[])
        if evidence:
            for hit in evidence:
                with st.container(border=True): st.markdown(f"**{hit['source']}** · {hit['score']:.0%}"); st.write(hit["content"])
        else: st.info("Run AI analysis to retrieve grounded knowledge sources.")
    with tabs[4]:
        matches=(result or {}).get("similar_tickets",[]); duplicates=(result or {}).get("possible_duplicates",[])
        if duplicates: st.warning(f"{len(duplicates)} probable duplicate match(es). Review manually; tickets were not merged or closed.")
        if matches:
            for hit in matches:
                st.write(f"{hit['ticket_id']} · {hit['subject']} · {hit['score']:.0%} semantic similarity · {hit['status']} · {hit['match_type'].replace('_',' ')}")
        else: st.info("Run AI analysis to compare this ticket with prior open and resolved cases.")
    with tabs[5]:
        timeline=rows(settings,"SELECT event_type,actor_type,summary,created_at FROM timeline_events WHERE ticket_id=? ORDER BY created_at DESC",(item["id"],))
        for ev in timeline: st.markdown(f"**{ev['event_type'].replace('_',' ').title()}** · {ev['created_at']}\n\n{ev['summary']}")
        if not timeline: st.info("Ticket history will appear here.")
    with tabs[6]:
        for outcome in (result or {}).get("tool_results",[]):
            st.info(f"Workflow tool · {outcome['tool']} · {outcome['mode']} · {'record found' if outcome['ok'] else 'record unavailable'} · {outcome['source']}")
        if not (result or {}).get("tool_results"):
            st.caption("This workflow did not require a customer-record lookup. Tools below are read-only and schema-controlled.")
        tool=st.selectbox("Controlled demo tool",[t["name"] for t in available_tools()]); args=st.text_input("Record ID",value=item["customer_id"] if tool=="get_customer" else item["id"]); key="customer_id" if tool=="get_customer" else "ticket_id"
        if st.button("Run read-only tool"):
            response=call_tool(settings,tool,{key:args}); st.json(response)

def _customers(settings: Settings):
    _header("Customers","Customer 360 with related cases and support history.")
    cs=rows(settings,"SELECT * FROM customers ORDER BY name")
    if not cs: st.info("No customer profiles yet."); return
    cid=st.selectbox("Customer profile",[c["id"] for c in cs],format_func=lambda x:next(f"{c['name']} · {c['email'] or c['phone'] or 'No contact'}" for c in cs if c['id']==x),label_visibility="collapsed"); c=next(c for c in cs if c["id"]==cid)
    own=tickets(settings)
    own=[t for t in own if t["customer_id"]==cid]
    open_count=sum(t["status"] not in {"Resolved","Closed"} for t in own)
    resolved_count=sum(t["status"] in {"Resolved","Closed"} for t in own); escalated=sum(t["status"]=="Escalated" for t in own)
    with st.container(border=True):
        summary,mt1,mt2,mt3,mt4=st.columns([2.1,1,1,1,1],vertical_alignment="center")
        summary.markdown(f"### {c['name']}"); summary.caption(f"{c['email'] or 'Email not provided'}  ·  {c['phone'] or 'Phone not provided'}")
        mt1.metric("Total Tickets",len(own)); mt2.metric("Resolved",resolved_count); mt3.metric("Open",open_count); mt4.metric("Escalated",escalated)
    overview,tickets_tab,conversations_tab,activity_tab,preferences_tab=st.tabs(["Overview","Tickets","Conversations","Activity","Preferences"])
    with overview:
        detail,recent,insights=st.columns([1,1.1,1],gap="medium")
        with detail:
            with st.container(border=True):
                st.subheader("Customer Details")
                for label,value in [("Email",c["email"] or "Not provided"),("Phone",c["phone"] or "Not provided"),("Plan",c.get("plan") or "Not recorded"),("Status",c.get("status") or "Active"),("Customer Since",(c.get("created_at") or "")[:10] or "Not recorded")]:
                    st.caption(label); st.markdown(f"**{value}**")
        with recent:
            with st.container(border=True):
                st.subheader("Recent Tickets")
                for case in own[:5]:
                    st.markdown(f"**{html.escape(str(case['id']))}**  ·  {_pill(case['priority'])}",unsafe_allow_html=True); st.caption(case["subject"]); st.caption(case["status"])
                if not own: st.info("No tickets are linked to this customer yet.")
        with insights:
            with st.container(border=True):
                st.subheader("Sentiment Trend")
                sentiment=[t.get("sentiment") for t in own if t.get("sentiment")]
                if sentiment:
                    frame=pd.Series(sentiment).value_counts().rename_axis("Sentiment").reset_index(name="Tickets")
                    plot=px.line(pd.DataFrame({"Ticket":range(1,len(sentiment)+1),"Sentiment":sentiment}),x="Ticket",y="Sentiment",markers=True)
                    plot.update_layout(height=155,margin=dict(l=4,r=4,t=4,b=4),paper_bgcolor="rgba(0,0,0,0)",plot_bgcolor="rgba(0,0,0,0)")
                    st.plotly_chart(plot,width="stretch",config={"displayModeBar":False})
                else: st.caption("Sentiment appears after a ticket has been analyzed.")
                st.subheader("Communication Channels")
                channels=sorted({t["channel"] for t in own})
                st.write("  ·  ".join(channels) if channels else "No communication history")
    with tickets_tab: _ticket_table(own)
    with conversations_tab:
        for msg in rows(settings,"SELECT m.*,t.subject FROM messages m JOIN tickets t ON t.id=m.ticket_id WHERE m.customer_id=? ORDER BY m.created_at DESC",(cid,)):
            with st.container(border=True): st.markdown(f"**{msg['channel']} · {msg['direction']}**  ·  {msg['subject']}"); st.caption(msg["created_at"]); st.write(msg["content"])
    with activity_tab:
        for case in own: st.markdown(f"**{case['id']}** · {case['status']} · {case['updated_at']}  \n{case['subject']}")
    with preferences_tab:
        st.info("Customer preferences are currently read-only in this workspace.")

def _conversations(settings: Settings):
    _header("Conversations","Unified conversation history across support channels.")
    all_items=tickets(settings); search=st.text_input("Search conversations",placeholder="⌕  Search customers or messages…",key="conversation_search")
    channel=st.radio("Channel",["All","Email","SMS","Web"],horizontal=True,key="conversation_channel")
    items=[t for t in all_items if (not search or search.lower() in (t["customer_name"]+t["subject"]+t["description"]).lower()) and (channel=="All" or t["channel"]==channel)]
    if not items: st.info("No conversations match your filters."); return
    ids=[t["id"] for t in items]; selected=st.session_state.get("conversation_selected")
    if selected not in ids: selected=ids[0]; st.session_state.conversation_selected=selected
    current=next(t for t in items if t["id"]==selected)
    left,right=st.columns([.78,1.4],gap="large")
    with left:
        with st.container(border=True):
            st.subheader("Conversation List")
            for item in items:
                last=rows(settings,"SELECT content FROM messages WHERE ticket_id=? ORDER BY created_at DESC LIMIT 1",(item["id"],))
                st.markdown(f"<div class='conversation-list-row'><b>{html.escape(str(item['customer_name']))}</b><small>{html.escape(str((last[0]['content'] if last else item['description'])[:48]))}…</small><small>{html.escape(str(item['created_at'][:16]))}</small></div>",unsafe_allow_html=True)
                st.button(f"{item['id']} · {item['channel']}",key=f"conversation_select_{item['id']}",on_click=lambda tid=item['id']:st.session_state.update(conversation_selected=tid),type="primary" if item['id']==selected else "secondary",width="stretch")
    with right:
        with st.container(border=True):
            st.subheader(f"{current['customer_name']} · {current['id']}")
            st.caption(f"{current['customer_email'] or 'No email'} · {current['category']} · {current['priority']}")
            for msg in rows(settings,"SELECT sender,direction,content,created_at FROM messages WHERE ticket_id=? ORDER BY created_at",(selected,)):
                style="customer-message" if msg["direction"]=="inbound" else "agent-message"
                st.markdown(f"<div class='{style}'><b>{html.escape(str(msg['sender']))}</b><br>{html.escape(str(msg['content']))}<small>{html.escape(str(msg['created_at'][:16]))}</small></div>",unsafe_allow_html=True)
            st.text_area("Type a message",key=f"conversation_draft_{selected}",placeholder="Type a message…")
            if st.button("Open approved response composer",key=f"conversation_compose_{selected}",type="primary"):
                st.session_state.selected_ticket=selected; st.session_state.page_nav="Ticket Workspace"; st.rerun()

def _analysis(settings: Settings):
    _header("AI Analysis","Structured ticket intelligence with evidence, uncertainty and human review.")
    item=_selected_ticket(settings)
    if item:
        if st.button("Analyze selected ticket",type="primary"):
            try: st.session_state[f"result_{item['id']}"]=analyze(settings,item["id"]); st.success("Analysis and recommendation saved.")
            except Exception as e: st.error(f"Analysis failed safely ({type(e).__name__}).")
        result=st.session_state.get(f"result_{item['id']}")
        if result: _ticket_detail(settings,item,result)

def _knowledge(settings: Settings):
    _header("Knowledge Base","Manage trusted support guidance and retrieve traceable evidence.")
    docs=rows(settings,"SELECT * FROM knowledge_documents ORDER BY uploaded_at DESC")
    left,right=st.columns([1.65,.85],gap="medium")
    with left:
        with st.container(border=True):
            st.subheader("Documents")
            query=st.text_input("Search documents",placeholder="⌕  Search knowledge base…")
            visible=[d for d in docs if not query or query.lower() in d["filename"].lower()]
            if visible:
                for doc in visible:
                    icon_color={"indexed":"#365BFF","ready":"#16A879"}.get(doc["status"].lower(),"#E9A022")
                    st.markdown(f"<div class='document-row'><span style='background:{icon_color}'>D</span><div><b>{html.escape(str(doc['filename']))}</b><small>{html.escape(str(doc['document_type'] or 'Document'))} · {int(doc['chunk_count'])} chunks</small></div><em>{html.escape(str(doc['status']))}</em></div>",unsafe_allow_html=True)
            else: st.info("No indexed documents. Upload approved support material to enable grounded responses.")
            if docs:
                doc_id=st.selectbox("Manage a document",[d["id"] for d in docs],format_func=lambda x:next(d["filename"] for d in docs if d["id"]==x),key="knowledge_doc_id")
                action_left,action_right=st.columns([1,1])
                if action_left.button("Re-index selected",key="reindex_document"):
                    try: st.success(f"Re-indexed {reindex_document(settings.db_path,settings.vector_path,doc_id)} chunks.")
                    except Exception as e: st.error(f"Re-index failed ({type(e).__name__}).")
                confirm=st.checkbox("Confirm permanent removal",key="confirm_delete_document")
                if action_right.button("Delete selected",disabled=not confirm,key="delete_document"):
                    try: delete_document(settings.db_path,settings.vector_path,doc_id); st.success("Document and its vector chunks were removed."); st.rerun()
                    except Exception as e: st.error(f"Document could not be deleted ({type(e).__name__}).")
    with right:
        with st.container(border=True):
            st.subheader("Upload Document")
            upload=st.file_uploader("Upload PDF / DOCX / TXT / CSV (max 10 MB; PDF max 200 pages)",type=["pdf","docx","txt","md","csv"],label_visibility="collapsed",max_upload_size=10)
            if upload and st.button("Index Document",type="primary",width="stretch"):
                try:
                    text=extract_text(upload.name,upload.getvalue()); count=index_text(settings.db_path,settings.vector_path,upload.name,text); st.success(f"Indexed {count} knowledge chunk(s).")
                except ValueError as e: st.error(str(e))
                except Exception as e: st.error(f"Document could not be indexed ({type(e).__name__}). Check the file and try again.")
        with st.container(border=True):
            st.subheader("Document Status")
            selected_doc=next((d for d in docs if d["id"]==st.session_state.get("knowledge_doc_id")),docs[0] if docs else None)
            st.caption("Indexed documents"); st.markdown(f"**{len(docs)}**")
            st.caption("Chunks"); st.markdown(f"**{selected_doc['chunk_count'] if selected_doc else 0}**")
            st.caption("Vector database"); st.success("Ready" if settings.vector_path.exists() else "Initializes on use")
            if selected_doc: st.caption("Last Updated · "+(selected_doc["indexed_at"] or selected_doc["uploaded_at"])[:10])
    query=st.text_input("Search indexed knowledge",placeholder="Example: password reset link expiry",key="rag_query")
    if query:
        hits=retrieve(settings.db_path,settings.vector_path,query,5)
        if hits:
            for h in hits:
                with st.expander(f"{h['source']} · relevance {h['score']:.0%}"): st.write(h["content"])
        else: st.warning("No relevant evidence found. The system should not claim an unsupported policy.")

def _analytics(settings: Settings):
    _header("Analytics","Operational volume and service patterns calculated from persisted tickets.")
    ts=tickets(settings); df=pd.DataFrame(ts)
    if df.empty: st.info("Analytics will appear after tickets are recorded."); return
    a,b,c,d=st.columns(4); a.metric("Tickets",len(df)); b.metric("Open",int(df.status.isin(["Open","In Progress","Pending"]).sum())); c.metric("Escalated",int((df.status=="Escalated").sum())); d.metric("Channels",int(df.channel.nunique()))
    col1,col2=st.columns(2)
    with col1:
        st.subheader("Category"); category=df.category.fillna("Unclassified").value_counts().rename_axis("Category").reset_index(name="Tickets")
        st.plotly_chart(px.pie(category,names="Category",values="Tickets",hole=.5),width="stretch")
    with col2:
        st.subheader("Priority"); priority=df.priority.value_counts().rename_axis("Priority").reset_index(name="Tickets")
        st.plotly_chart(px.bar(priority,x="Priority",y="Tickets",color="Priority",color_discrete_map={"Urgent":"#E5484D","High":"#F2994A","Medium":"#E9B949","Low":"#35A879"}),width="stretch")
    col1,col2=st.columns(2)
    with col1:
        st.subheader("Sentiment"); sentiment=df.sentiment.fillna("Not analyzed").value_counts().rename_axis("Sentiment").reset_index(name="Tickets")
        st.plotly_chart(px.bar(sentiment,x="Sentiment",y="Tickets",color="Sentiment"),width="stretch")
    with col2:
        st.subheader("Channel"); channel=df.channel.value_counts().rename_axis("Channel").reset_index(name="Messages")
        st.plotly_chart(px.pie(channel,names="Channel",values="Messages",hole=.5),width="stretch")
    df["created_day"]=pd.to_datetime(df.created_at,utc=True).dt.date.astype(str); trend=df.groupby("created_day").size().rename("Tickets").reset_index(); st.subheader("Ticket volume over time"); st.plotly_chart(px.line(trend,x="created_day",y="Tickets",markers=True),width="stretch")

def _quality(settings: Settings):
    _header("AI Quality","Quality signals traced to stored analysis and validation events.")
    data=rows(settings,"SELECT * FROM ai_quality_metrics ORDER BY created_at DESC")
    if not data: st.info("Run ticket analysis to start measuring confidence and validation quality."); return
    df=pd.DataFrame(data)
    approved=rows(settings,"SELECT COUNT(DISTINCT ticket_id) n FROM messages WHERE direction='outbound' AND delivery_status='demo_sent'")[0]["n"]
    analyzed_tickets=int(df.ticket_id.nunique())
    similar_count=rows(settings,"SELECT COUNT(*) n FROM similarity_matches WHERE match_type IN ('similar','resolved_case','probable_duplicate')")[0]["n"]
    duplicate_count=rows(settings,"SELECT COUNT(*) n FROM similarity_matches WHERE match_type='probable_duplicate'")[0]["n"]
    escalations=rows(settings,"SELECT COUNT(*) n FROM ai_recommendations WHERE escalation_required=1")[0]["n"]
    avg_conf=float(df.confidence.fillna(0).mean()); edit_rate=float(df.human_edited.mean()); failure_rate=float((df.validation_passed==0).mean()); accept_rate=approved/max(1,analyzed_tickets)
    cards=st.columns(4,gap="small")
    for col,(label,value,color) in zip(cards,[("AI Suggestion Acceptance",f"{accept_rate:.0%}","#365BFF"),("Average Confidence",f"{avg_conf:.0%}","#16A879"),("Human Edits",f"{edit_rate:.0%}","#E9A022"),("Validation Failure",f"{failure_rate:.0%}","#E94B62")]):
        col.markdown(f"<div class='ref-kpi' style='--tone:{color}'><span class='ref-kpi-icon'>●</span><small>{label}</small><strong>{value}</strong></div>",unsafe_allow_html=True)
    st.markdown("<div style='height:22px'></div>",unsafe_allow_html=True)
    trend_col,distribution_col,edit_col=st.columns([1.85,1,1.05],gap="small")
    with trend_col:
        with st.container(border=True):
            st.subheader("Acceptance Rate Trend")
            trend=df.assign(day=pd.to_datetime(df.created_at,utc=True).dt.date).groupby("day",as_index=False).agg(acceptance=("validation_passed","mean"))
            trend["Acceptance"]=trend.acceptance*100
            plot=px.line(trend,x="day",y="Acceptance",markers=True,color_discrete_sequence=["#365BFF"])
            plot.update_layout(height=220,margin=dict(l=5,r=5,t=5,b=5),paper_bgcolor="rgba(0,0,0,0)",plot_bgcolor="rgba(0,0,0,0)",showlegend=False,xaxis_title=None,yaxis_title=None)
            plot.update_yaxes(range=[0,100],gridcolor="#E8EDF5"); st.plotly_chart(plot,width="stretch",config={"displayModeBar":False})
    with distribution_col:
        with st.container(border=True):
            st.subheader("Confidence Distribution")
            buckets=pd.cut(df.confidence.fillna(0),[0,.5,.7,.9,1.0001],labels=["0–50%","50–70%","70–90%","90–100%"],include_lowest=True).value_counts().reindex(["0–50%","50–70%","70–90%","90–100%"],fill_value=0)
            for label,value in buckets.items(): st.markdown(f"**{label}**　{value}"); st.progress(float(value/max(1,len(df))))
    with edit_col:
        with st.container(border=True):
            st.subheader("Human Edit Reasons")
            st.markdown(f"Tone / clarity edits　**{int(df.human_edited.sum())}**"); st.progress(edit_rate)
            st.markdown(f"Validation failures　**{int((df.validation_passed==0).sum())}**"); st.progress(failure_rate)
            st.markdown(f"Regenerated　**{int(df.regenerated.sum())}**"); st.progress(float(df.regenerated.mean()))
    with st.container(border=True):
        st.subheader("AI Performance by Category")
        category_rows=[]
        for _,metric in df.iterrows():
            case=ticket(settings,metric.ticket_id)
            category_rows.append({"Category":case.get("category") or "Unclassified" if case else "Unclassified","Confidence":float(metric.confidence or 0),"Human Edit":bool(metric.human_edited)})
        category_df=pd.DataFrame(category_rows).groupby("Category",as_index=False).agg(Confidence=("Confidence","mean"),Human_Edits=("Human Edit","mean"))
        if not category_df.empty:
            for _,row in category_df.iterrows():
                st.markdown(f"**{row['Category']}**　{row['Confidence']:.0%} confidence　{row['Human_Edits']:.0%} human edits")
                st.progress(float(row["Confidence"]))
    with st.expander("Analysis quality records"):
        st.dataframe(df[["ticket_id","confidence","validation_passed","human_edited","regenerated","rag_evidence_available","mcp_used","created_at"]],hide_index=True,width="stretch")

def _bulk(settings: Settings):
    _header("Bulk Analysis","Validate and analyze CSV tickets. Batch processing never sends messages.")
    up=st.file_uploader("CSV with subject and description columns",type=["csv"],max_upload_size=10)
    if up:
        try:
            frame=pd.read_csv(up); st.write(f"{len(frame)} rows · {len(frame.columns)} columns"); st.dataframe(frame.head(8),hide_index=True,width="stretch")
            if st.button("Analyze batch",type="primary"):
                progress=st.progress(0.0,text="Preparing batch…")
                with st.status(f"Analyzing {len(frame)} ticket rows…",expanded=False):
                    result,failed=bulk_analyze(settings,frame,lambda done,total:progress.progress(done/max(total,1),text=f"Analyzed {done} of {total} rows")); st.session_state.bulk_result=result
                progress.empty()
                st.success(f"Batch complete · {len(result)-failed} successful · {failed} failed")
        except Exception as e: st.error(f"CSV could not be read ({type(e).__name__}). Check the file format and try again.")
    if st.session_state.get("bulk_result") is not None:
        result=st.session_state.bulk_result; st.dataframe(result,hide_index=True,width="stretch"); st.download_button("Download results CSV",result.to_csv(index=False).encode(),"supportiq_bulk_analysis.csv","text/csv")

def _brief(settings: Settings):
    title,actions=st.columns([5,1],vertical_alignment="center")
    with title: st.markdown("<div class='page-header'><span class='sr-only'>Daily Brief</span><h1>Daily Support Brief</h1><p>AI-generated operational summary for the support team.</p></div>",unsafe_allow_html=True)
    actions.button("Generate Brief",type="primary",on_click=lambda:st.session_state.update(daily_brief=daily_brief(settings)),width="stretch")
    st.session_state.setdefault("daily_brief",daily_brief(settings))
    report=st.session_state.daily_brief; m=metrics(settings)
    review=rows(settings,"SELECT AVG(rating) score FROM feedback")[0]["score"]
    values=[("New Tickets",report["tickets_today"],"#365BFF"),("Escalations",report["escalated"],"#E94B62"),("Resolved",m["resolved"],"#16A879"),("CSAT Score",f"{review:.1f}/5" if review is not None else "—","#7656E8")]
    cols=st.columns(4,gap="small")
    for col,(label,value,color) in zip(cols,values): col.markdown(f"<div class='ref-kpi' style='--tone:{color}'><span class='ref-kpi-icon'>●</span><small>{label}</small><strong>{value}</strong></div>",unsafe_allow_html=True)
    st.markdown("<div style='height:24px'></div>",unsafe_allow_html=True)
    issues,insights=st.columns([1,1.05],gap="medium")
    with issues:
        with st.container(border=True):
            st.subheader("Top Issues")
            if report["top_issues"]:
                for subject in report["top_issues"][:5]: st.markdown(f"**{subject}**"); st.caption("Review open support case")
            else: st.info("No open issues need review.")
    with insights:
        with st.container(border=True):
            st.subheader("AI Insights"); quality=report["quality_signal"]
            insights_text=[f"{len(report['sla_risks'])} ticket(s) are at risk or past an SLA target.",f"{quality['analyses']} ticket analysis record(s) are available.",f"{quality['validation_failures']} recent response validation failure(s)."]
            if report["categories"]: insights_text.append(f"Most common ticket category: {report['categories'][0][0]}.")
            for text in insights_text: st.markdown(f"•　{text}")
    actions_col,risks_col=st.columns([1,1.05],gap="medium")
    with actions_col:
        with st.container(border=True):
            st.subheader("Recommended Actions")
            for text in ["Review SLA risk tickets first.","Investigate escalated customer cases.","Check knowledge evidence before approving replies."]:
                st.markdown(f"<div class='brief-action'><b>NEXT</b>{text}</div>",unsafe_allow_html=True)
    with risks_col:
        with st.container(border=True):
            st.subheader("SLA Risks")
            if report["sla_risks"]:
                for risk in report["sla_risks"][:5]: st.markdown(f"**{risk['id']}** · {risk['resolution_status'].replace('_',' ').title()}"); st.caption(risk["subject"])
            else: st.success("No approaching or breached SLA markers among open tickets.")
    st.caption("Generated from persisted ticket and SLA records · "+report["generated_at"])

def _settings(settings: Settings):
    _header("Settings","Configure AI, RAG, communications, MCP, approval, and system behavior.")
    general,ai_tab,rag_tab,communications,mcp_tab,system_tab,about_tab=st.tabs(["General","AI Configuration","RAG","Communication","MCP Tools","System","About"])
    with general:
        left,right=st.columns([1,1],gap="large")
        with left:
            with st.container(border=True):
                st.subheader("AI Configuration")
                st.caption("Provider"); st.markdown(f"**{'Groq' if settings.ai_configured else 'Offline / Demo'}**")
                st.caption("Model"); st.markdown(f"**{settings.llm_model}**")
                st.caption("API key"); st.success("Configured · value hidden") if settings.ai_configured else st.warning("Not configured · key is read from local environment")
        with right:
            with st.container(border=True):
                st.subheader("RAG Configuration")
                st.caption("Embedding model"); st.markdown("**Sentence Transformers · local**")
                st.caption("Vector database"); st.markdown("**ChromaDB · persistent local storage**")
                st.caption("Knowledge files"); st.markdown(f"**{len(rows(settings,'SELECT id FROM knowledge_documents'))} indexed documents**")
        c1,c2=st.columns(2,gap="large")
        with c1:
            with st.container(border=True):
                st.subheader("Approval & System")
                st.write("Human approval required before any customer message is sent.")
                st.success(f"Application mode · {settings.mode.upper()}")
                st.caption("Outbound messages from demo adapters are recorded locally and do not contact customers.")
        with c2:
            with st.container(border=True):
                st.subheader("SLA Targets")
                first=st.number_input("First response target (minutes)",min_value=1,value=60)
                resolution=st.number_input("Resolution target (hours)",min_value=1,value=48)
                if st.button("Save Changes",type="primary",key="save_sla_policy"):
                    import uuid
                    with connect(settings.db_path) as db:
                        db.execute("UPDATE sla_policies SET active=0")
                        db.execute("INSERT INTO sla_policies VALUES(?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),"Configured support targets",int(first),int(resolution*60),None,None,1,datetime.now(timezone.utc).isoformat()))
                    st.success("SLA policy saved for new tickets.")
    with ai_tab:
        left,right=st.columns(2,gap="large")
        with left:
            with st.container(border=True):
                st.subheader("AI Configuration")
                st.text_input("LLM Provider",value="Groq" if settings.ai_configured else "Offline Demo",disabled=True)
                st.text_input("Model",value=settings.llm_model,disabled=True)
                st.text_input("API key",value="Configured · hidden" if settings.ai_configured else "Not configured",disabled=True)
                st.caption("Set credentials in the local environment file or Streamlit secrets. Secret values are never displayed here.")
        with right:
            with st.container(border=True):
                st.subheader("Runtime Controls")
                st.metric("Response validation","Enabled")
                st.metric("Human approval","Required")
                st.metric("Provider readiness","Live" if settings.ai_configured else "Offline fallback")
    with rag_tab:
        left,right=st.columns([1.5,1],gap="large")
        with left:
            with st.container(border=True):
                st.subheader("RAG Configuration")
                st.text_input("Embedding Model",value="Sentence Transformers (local)",disabled=True)
                st.text_input("Vector Database",value="ChromaDB",disabled=True)
                st.number_input("Top K results",min_value=1,max_value=20,value=5,disabled=True)
        with right:
            with st.container(border=True):
                st.subheader("Knowledge Status")
                st.metric("Documents",len(rows(settings,"SELECT id FROM knowledge_documents")))
                st.metric("Store","Ready" if settings.vector_path.exists() else "Initializes on use")
                st.button("Open Knowledge Base",on_click=_navigate_to_page,args=("Knowledge Base",))
    with communications:
        left,right=st.columns(2,gap="large")
        with left:
            with st.container(border=True):
                st.subheader("Email")
                st.text_input("Email Provider",value=settings.email_provider.upper(),disabled=True)
                st.text_input("Sender",value=settings.email_from or "Not configured",disabled=True)
                st.success("Configured · provider verifies on send" if settings.email_live_ready else "Demo recording only" if settings.email_provider in {"demo","mock",""} else "Setup incomplete")
        with right:
            with st.container(border=True):
                st.subheader("SMS")
                st.text_input("SMS Provider",value=settings.sms_provider.upper(),disabled=True)
                st.text_input("Sender number",value=settings.twilio_from or "Not configured",disabled=True)
                st.success("Credentials configured · provider verifies on send" if settings.sms_live_ready else "Demo recording only" if settings.sms_provider in {"demo","mock",""} else "Setup incomplete")
    with mcp_tab:
        st.subheader("Controlled MCP Tools")
        for tool in available_tools():
            with st.container(border=True): st.markdown(f"**{tool['name']}** · `{tool['permission']}`"); st.caption(tool["description"])
        st.button("Open MCP Tools",on_click=_navigate_to_page,args=("MCP Tools",),type="primary")
    with system_tab:
        left,right=st.columns(2,gap="large")
        with left:
            with st.container(border=True):
                st.subheader("System Status"); st.metric("Application mode",settings.mode.upper()); st.metric("Database","Connected · SQLite"); st.metric("Vector index","Local · ChromaDB")
        with right:
            with st.container(border=True):
                st.subheader("Local Paths"); st.code(f"Database: {settings.db_path}\nVector store: {settings.vector_path}",language=None)
    with about_tab:
        with st.container(border=True):
            st.subheader("About SupportIQ AI")
            st.write("SupportIQ AI is an AI-powered customer support workspace built with Streamlit, LangGraph, retrieval-augmented generation, and controlled support tools.")
            st.markdown("**Created by Gaurav · AI Engineer**")
            st.caption("Building AI systems with LLMs, Agentic AI, RAG, LangGraph, MCP and machine learning.")
            st.button("Open About / Creator",on_click=_navigate_to_creator)

def _mcp(settings: Settings):
    with st.container(key="route-title-accessibility"): st.title("MCP Tools")
    _header("MCP Tools","Schema-controlled, read-only demo tools with visible unavailable-system behavior.")
    for tool in available_tools():
        with st.container(border=True): st.markdown(f"**{tool['name']}** · `{tool['permission']}`"); st.caption(tool["description"])
    name=st.selectbox("Tool",[x["name"] for x in available_tools()]); value=st.text_input("Customer or ticket ID"); key="customer_id" if name=="get_customer" else "ticket_id"
    if st.button("Execute controlled tool"):
        response=call_tool(settings,name,{key:value}); st.json(response)
        if not response.get("ok"): st.warning(response.get("error","Record was not found; verify manually."))

def _creator_gate(settings: Settings):
    st.markdown("<div class='creator-page-title'>⌂　About / Creator</div>",unsafe_allow_html=True)
    photo=CREATOR_ASSETS / "creator_photo.jpeg"
    encoded=base64.b64encode(photo.read_bytes()).decode("ascii") if photo.exists() else ""
    links="".join(f"<a class='creator-social' href='{url}' target='_blank' rel='noopener noreferrer'>{label}</a>" for label,url in CREATOR_LINKS.items())
    projects=[
        ("🤖","AI Interview Agent","An intelligent agent for interview preparation using Agentic AI and RAG.","LangGraph · RAG · Streamlit","https://github.com/RGTrigger/AI-Interview-Agent","#7547F4"),
        ("🛡️","SurakshaSaathi","Disaster preparedness education system built for SIH 2025.","React · Supabase · TypeScript","https://github.com/RGTrigger/SIH-2025-SurakshaSaathi","#13A879"),
        ("💲","PayVision AI","Financial forecasting system using machine learning.","Python · ML · Analytics","https://github.com/RGTrigger/payvision-ai","#E9A022"),
        ("文","Indian Language STT","Speech-to-text translator for Indian languages.","Python · Whisper · NLP","https://github.com/RGTrigger/Indian-Language-Speech-to-Text-Translator","#2877F5"),
    ]
    project_cards="".join(f"<article class='creator-project'><div class='creator-project-title'><span style='--project-color:{color}'>{icon}</span><b>{name}</b></div><p>{description}</p><small>{tags}</small><a href='{repo}' target='_blank' rel='noopener noreferrer'>↗ View Project&nbsp;&nbsp;⌘ GitHub</a></article>" for icon,name,description,tags,repo,color in projects)
    st.markdown(f"""
      <section class='creator-hero'>
        <div class='creator-photo-wrap'><img src='data:image/jpeg;base64,{encoded}' alt='Gaurav'></div>
        <div class='creator-copy'><small>CREATED BY</small><h1>Gaurav</h1><h2>AI Engineer</h2>
          <p>Building AI systems with LLMs, Agentic AI, RAG, LangGraph, MCP and machine learning.</p>
          <div class='creator-links'>{links}</div>
        </div>
        <blockquote><span>“</span><p>Building AI systems that create real impact and make technology more accessible.</p><b>— Gaurav</b></blockquote>
      </section>
      <section class='creator-focus-grid'>
        <article><span>🎓</span><b>AI Engineering</b><p>LLMs, Agentic AI, RAG, LangGraph, MCP and machine learning.</p></article>
        <article><span>〈/〉</span><b>Interests</b><p>AI systems, retrieval, agent workflows and applied machine learning.</p></article>
        <article><span>🚀</span><b>Current Focus</b><p>Building practical AI applications and support automation.</p></article>
        <article><span>🎯</span><b>Goal</b><p>Create useful, grounded AI systems with human review.</p></article>
      </section>
      <div class='creator-section-heading'><h2>♡&nbsp; Featured Projects</h2><a href='https://github.com/RGTrigger?tab=repositories' target='_blank' rel='noopener noreferrer'>View All Projects on GitHub&nbsp; →</a></div>
      <section class='creator-project-grid'>{project_cards}</section>
      <section class='creator-connect'><div><b>♡&nbsp; Let's Connect</b>{links}</div><aside><span>▢</span><div><b>Open for Opportunities</b><small>Always excited to work on interesting AI/ML projects, collaborations, and learning opportunities.</small></div><a href='mailto:rgtrigger.ai.dev@gmail.com'>→</a></aside></section>
    """,unsafe_allow_html=True)

def _feedback(settings: Settings):
    st.markdown("<div class='feedback-page-head'><span>▣</span><div><b>Feedback &amp; Review</b><small>Your feedback helps me improve SupportIQ AI and build better open source projects.</small></div></div>",unsafe_allow_html=True)
    feedback=rows(settings,"SELECT name,rating,feedback_type,message,created_at FROM feedback ORDER BY created_at DESC LIMIT 40")
    form_tab,reviews_tab,features_tab,bugs_tab=st.tabs(["✉ Give Feedback","☆ Reviews","✧ Feature Requests","⚑ Bug Reports"])
    with form_tab:
        left,right=st.columns([1.15,1])
        with left:
            with st.container(border=True):
                st.markdown("### Share Your Feedback")
                st.caption("Tell me about your experience, suggestions, or ideas.")
                with st.form("feedback"):
                    name=st.text_input("Your Name (Optional)"); email=st.text_input("Your Email (Optional)"); kind=st.selectbox("Feedback Type",["General feedback","Feature request","Bug report","UI/UX feedback","AI quality feedback","RAG/knowledge feedback"]); message=st.text_area("Your Message",height=92,placeholder="Share your thoughts, suggestions, or ideas…"); rating_choice=st.radio("Rating (Optional)",["No rating",1,2,3,4,5],horizontal=True,index=0,format_func=lambda value:"No rating" if value=="No rating" else "★"*value+"☆"*(5-value)); rating=None if rating_choice=="No rating" else rating_choice; submitted=st.form_submit_button("➤ Submit Feedback",type="primary",width="stretch")
                if submitted:
                    try: add_feedback(settings,name,email,rating,kind,message); st.success("Thank you. Your feedback was saved locally."); st.rerun()
                    except ValueError as e: st.error(str(e))
        with right:
            with st.container(border=True):
                st.markdown("### ⭐ Why Your Feedback Matters")
                for reason in ["Helps improve the platform","Identifies new features","Finds issues and bugs","Makes the project more useful","Supports open source development"]: st.markdown(f"✓  {reason}")
            st.markdown("### Recent Reviews")
            if feedback:
                for f in feedback[:2]:
                    with st.container(border=True): st.markdown(f"**{f['name'] or 'Anonymous'}** · {'★'*int(f['rating'] or 0)}{'☆'*(5-int(f['rating'] or 0))}"); st.caption(f["created_at"][:10]+" · "+f["feedback_type"]); st.write(f["message"][:260])
            else: st.caption("Reviews will appear here after feedback is submitted.")
    with reviews_tab: _render_reviews(feedback)
    with features_tab: _render_reviews([f for f in feedback if "feature" in f["feedback_type"].lower()])
    with bugs_tab: _render_reviews([f for f in feedback if "bug" in f["feedback_type"].lower()])

def _render_reviews(feedback):
    if not feedback: st.info("No reviews in this category yet."); return
    for f in feedback:
        with st.container(border=True):
            st.markdown(f"**{f['name'] or 'Anonymous'}** · {'★'*int(f['rating'] or 0)}{'☆'*(5-int(f['rating'] or 0))} · {f['feedback_type']}")
            st.write(f["message"]); st.caption(f["created_at"][:10])

def render_app(settings: Settings):
    st.markdown("""
    <style>
    :root{--navy:#0D1A34;--navy2:#0D1A34;--blue:#365BFF;--line:#DCE5F2;--muted:#70809A}
    .stApp{background:#F4F7FC}.block-container{padding:0 28px 18px 40px;max-width:none}
    header[data-testid="stHeader"]{display:none}
    .st-key-global_header{position:relative;width:100%;box-sizing:border-box;background:#F4F7FC;padding:0 24px 30px 0;border-bottom:1px solid #DCE5F2}
    .st-key-global_header [data-testid="stHorizontalBlock"]{align-items:center!important}
    .st-key-global_header [data-testid="stTextInput"] input{height:38px!important;background:white!important;font-size:12px!important}
    .st-key-global_header [data-testid="stPopover"] button{height:42px!important;min-width:112px!important;white-space:nowrap!important;border:1px solid #DCE5F2!important;border-radius:24px!important;background:white!important;font-size:12px!important;font-weight:650!important}
    [data-testid="stSidebarCollapseButton"]{display:none!important}
    section[data-testid="stSidebar"]{background:var(--navy);border-right:1px solid #1b2a49;width:270px!important;min-width:270px!important}
    section[data-testid="stSidebar"]>div{width:270px!important}
    section[data-testid="stSidebar"]>div{background:transparent}
    section[data-testid="stSidebar"] [data-testid="stSidebarHeader"]{display:none!important}
    section[data-testid="stSidebar"] [data-testid="stSidebarContent"]{padding-top:12px!important}
    section[data-testid="stSidebar"] h1,section[data-testid="stSidebar"] h2,section[data-testid="stSidebar"] h3,section[data-testid="stSidebar"] p,section[data-testid="stSidebar"] label,section[data-testid="stSidebar"] span{color:#ECF3FF}
    section[data-testid="stSidebar"] h2{font-size:22px!important;white-space:nowrap;letter-spacing:-.3px}
    section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p{color:#AAC0E2!important;font-size:8px;line-height:1.1;letter-spacing:.015em}
    section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] a{color:#9CC4FF}
    section[data-testid="stSidebar"] button{width:100%;text-align:left;border-radius:8px;border:1px solid transparent;background:transparent;color:#DFE9FB;min-height:36px}
    section[data-testid="stSidebar"] button{margin-left:-16px!important;width:calc(100% + 32px)!important}
    section[data-testid="stSidebar"] button>div{justify-content:flex-start!important}
    section[data-testid="stSidebar"] button p{text-align:left!important;width:100%}
    section[data-testid="stSidebar"] [data-testid="stVerticalBlock"]{gap:.12rem}
    section[data-testid="stSidebar"] [data-testid="stCaptionContainer"]{margin:0;padding:0}
    section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p{font-size:9px!important;line-height:1.05}
    section[data-testid="stSidebar"] button{height:46px;min-height:46px;padding:.08rem .65rem;font-size:14px;line-height:1}
    section[data-testid="stSidebar"] button:hover{background:#1E3B68;border-color:#2B4B7B;color:white}
    section[data-testid="stSidebar"] button[kind="primary"]{background:#365BFF;border-color:#4D70FF;color:white;box-shadow:0 5px 14px #07193555}
    section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] hr{margin:.35rem 0 1.8rem}
    .stMain .stMainBlockContainer{min-height:100vh;box-sizing:border-box}
    .stMain .stMainBlockContainer>div[data-testid="stVerticalBlock"]{min-height:calc(100vh - 22px);display:flex;flex-direction:column}
    .stMain .stMainBlockContainer>div[data-testid="stVerticalBlock"]>div:has(> .st-key-global_footer){margin-top:auto!important}
    .st-key-global_footer{padding:18px 22px 12px!important;background:#fff;border:1px solid #D7E1EF;border-radius:12px;margin-top:12px!important}
    .footer-rule{height:1px;background:#DCE5F2;margin:14px 0 8px}
    .st-key-global_footer [data-testid="stMarkdownContainer"] p{font-size:12px;line-height:1.55;color:#263B5C}
    .st-key-global_footer [data-testid="stCaptionContainer"] p{font-size:10px;color:#405675}
    .st-key-global_footer [data-testid="stMarkdownContainer"] strong{color:#142649;font-size:14px}
    .st-key-global_footer button{min-height:24px!important;height:24px!important;padding:0!important;font-size:11px!important;text-align:left!important;border:0!important;background:transparent!important;color:#0649D9!important;box-shadow:none!important}
    .st-key-global_footer a{color:#1458E8}
    .st-key-global_footer [data-testid="stVerticalBlock"]{gap:.3rem}
    .stMain .block-container [data-testid="stVerticalBlock"]{gap:.35rem}
    .stMain .block-container h1{font-size:1.8rem!important;margin:.05rem 0 .15rem!important;line-height:1.2;font-weight:750}
    .stMain .block-container h2{font-size:1.35rem}
    .stMain .block-container h3{font-size:1.08rem}
    .page-header{margin:6px 0 15px;padding:0}.page-header.dashboard-header{margin-bottom:5px}.page-header h1{font-size:28px!important;line-height:1.2!important;font-weight:750;margin:0!important;padding:20px 0 0!important;color:#192841}.page-header p{margin:4px 0 0!important;color:#71809D;font-size:14px;line-height:1.35}
    .sr-only{position:absolute!important;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
    .stMain .block-container p{line-height:1.38}
    h1{font-size:28px!important;line-height:1.2!important;margin:.05rem 0 .15rem!important}
    h2{font-size:21px!important;line-height:1.2!important}
    h3{font-size:17px!important;line-height:1.2!important}
    [data-testid="stMetric"]{padding:12px 14px!important}
    [data-testid="stMetricLabel"]{font-size:12px!important}
    .creator-page-title{font-size:15px;font-weight:700;color:#102449;padding:0 0 7px;border-bottom:1px solid #DCE5F2;margin-bottom:10px}
    .st-key-workspace-title-accessibility,.st-key-route-title-accessibility{display:none!important}
    .creator-hero{display:grid;grid-template-columns:minmax(155px,.82fr) minmax(360px,2.4fr) minmax(210px,1.15fr);align-items:center;gap:26px;padding:22px 24px;margin:8px 0 14px;border:1px solid #DCE5F2;border-radius:15px;background:linear-gradient(115deg,#eefaff,#fbf9ff 52%,#effbff)}
    .creator-photo-wrap{display:flex;justify-content:center}.creator-photo-wrap img{width:190px;height:220px;object-fit:cover;object-position:center 38%;border-radius:50%;border:5px solid transparent;background:linear-gradient(white,white) padding-box,linear-gradient(145deg,#45c6ff,#c653fa) border-box;box-shadow:0 4px 16px #173d7a22}
    .creator-copy{min-width:0;max-width:100%;color:#172948}.creator-copy small{font-size:13px;color:#172948;font-weight:700;letter-spacing:.04em}.creator-copy h1{font-size:39px!important;line-height:1.08;margin:4px 0!important;padding:0!important;color:#102449}.creator-copy h2{font-size:22px!important;line-height:1.2;margin:5px 0 10px!important;padding:0!important;color:#102449}.creator-copy p{font-size:15px;color:#243650;line-height:1.5;margin:0;max-width:100%;white-space:normal;overflow-wrap:anywhere}.creator-links{display:flex;flex-wrap:wrap;gap:8px;margin-top:15px}.creator-social{display:inline-flex;align-items:center;justify-content:center;padding:9px 14px;border:1px solid #DCE5F2;border-radius:8px;background:#fff;color:#1749CC;text-decoration:none;font-size:12px;font-weight:650;white-space:nowrap}.creator-social:first-child{background:#1264ee;color:white}.creator-social:nth-child(2){background:#0b1b35;color:white}.creator-social:hover{filter:brightness(.97)}.creator-hero blockquote{margin:0;padding:18px 20px;border:1px solid #d8dfff;border-radius:13px;background:linear-gradient(145deg,#fbf3ff,#f1f8ff);color:#102449}.creator-hero blockquote span{font-size:35px;line-height:.7;color:#315cff;font-weight:800}.creator-hero blockquote p{font-size:16px;line-height:1.55;margin:4px 0 12px}.creator-hero blockquote b{font-size:14px}
    .creator-focus-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:0 0 17px}.creator-focus-grid article{padding:16px 18px;border:1px solid #dce5f2;border-radius:12px;background:linear-gradient(140deg,#fff,#f7fbff);min-height:120px}.creator-focus-grid article:nth-child(2){background:linear-gradient(140deg,#fff,#f7f3ff)}.creator-focus-grid article:nth-child(3){background:linear-gradient(140deg,#fff,#fff8ee)}.creator-focus-grid article:nth-child(4){background:linear-gradient(140deg,#fff,#f1fbf6)}.creator-focus-grid article>span{display:block;font-size:28px;color:#315cff;margin-bottom:7px}.creator-focus-grid article>b{display:block;font-size:15px;color:#102449}.creator-focus-grid article>p{font-size:13px;color:#405675;line-height:1.45;margin:5px 0 0}
    .creator-section-heading{display:flex;align-items:center;justify-content:space-between;margin:5px 0 10px}.creator-section-heading h2{font-size:22px!important;margin:0!important}.creator-section-heading>a{font-size:13px;color:#0649d9;text-decoration:none;font-weight:650}.creator-project-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.creator-project{display:flex;flex-direction:column;min-height:180px;padding:14px;border:1px solid #dce5f2;border-radius:12px;background:linear-gradient(145deg,#fff,#f8fbff)}.creator-project-title{display:flex;align-items:center;gap:10px;color:#11264b}.creator-project-title>span{display:grid;place-items:center;width:42px;height:42px;border-radius:10px;background:var(--project-color);color:white;font-size:22px}.creator-project-title>b{font-size:14px}.creator-project>p{font-size:12px;color:#405675;line-height:1.5;min-height:54px;margin:9px 0}.creator-project>small{font-size:10px;color:#315cff;background:#eef3ff;border-radius:6px;padding:5px 7px;align-self:flex-start}.creator-project>a{margin-top:auto;padding:8px 7px;border:1px solid #d3def0;border-radius:7px;color:#142b50;text-align:center;text-decoration:none;font-size:11px;font-weight:650}
    .creator-connect{display:grid;grid-template-columns:1.2fr 1fr;gap:20px;align-items:center;margin:16px 0 3px}.creator-connect>div{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.creator-connect>div>b{font-size:17px;color:#102449;margin-right:4px}.creator-connect .creator-social{padding:9px 11px}.creator-connect aside{display:flex;align-items:center;gap:12px;padding:12px;border:1px solid #dce5f2;border-radius:11px;background:#f9fbff}.creator-connect aside>span{font-size:22px;color:#315cff}.creator-connect aside b,.creator-connect aside small{display:block}.creator-connect aside small{font-size:11px;color:#526783;margin-top:3px}.creator-connect aside>a{margin-left:auto;color:#315cff;font-size:20px;text-decoration:none}
    .project-icon{height:34px;width:34px;border-radius:10px;background:#EEE9FF;color:#7949EE;display:flex;align-items:center;justify-content:center;font-size:20px}
    .feedback-page-head{display:flex;align-items:center;gap:12px;margin:3px 0 10px;color:#102449}
    .feedback-page-head>span{width:42px;height:42px;border-radius:10px;background:#e7e9ff;color:#5149F7;display:grid;place-items:center;font-size:22px}
    .feedback-page-head b{font-size:21px;display:block}
    .feedback-page-head small{font-size:10px;color:#65748e;display:block;margin-top:2px}
    .stMain .block-container [data-testid="stTabs"] [data-baseweb="tab-list"]{gap:0;border:1px solid #dce5f2;border-radius:9px;background:#fff;justify-content:space-around}
    .stMain .block-container [data-testid="stTabs"] [data-baseweb="tab"]{font-size:11px;padding:7px 9px}
    .stMain .block-container [data-testid="stTabs"] [aria-selected="true"]{background:#245bEE;color:#fff}
    .stMain .block-container [data-testid="stTextInput"] input,.stMain .block-container [data-testid="stTextArea"] textarea,.stMain .block-container [data-baseweb="select"]>div{border-color:#dce5f2!important;border-radius:7px!important}
    .stMetric{background:#fff;border:1px solid #E1E8F2;padding:15px 17px;border-radius:13px;box-shadow:0 2px 8px #18335b08}
    .ref-kpi{height:122px;margin-bottom:14px;background:#fff;border:1px solid #DCE5F2;border-radius:14px;padding:18px 18px 12px;position:relative;box-sizing:border-box;display:flex;flex-direction:column;gap:7px;color:#192841}
    .ref-kpi-icon{background:var(--tone);border-radius:9px;color:#fff;width:31px;height:31px;display:grid;place-items:center;font-size:15px;margin-bottom:5px}
    .ref-kpi small{font-size:12px;color:#71809D}.ref-kpi strong{font-size:29px;line-height:1;font-weight:750}.ref-kpi em{position:absolute;right:16px;bottom:20px;color:#16A879;font-size:11px;font-style:normal;font-weight:700}
    .activity-ref{position:relative;min-height:48px;padding:0 66px 8px 2px;margin-bottom:4px;border-bottom:1px solid #edf1f7;color:#192841}
    .activity-ref b{font-size:12px}.activity-ref span{position:absolute;right:0;top:1px;background:#fff0f2;border-radius:9px;padding:7px 13px;font-size:11px;font-weight:700}
    .activity-ref small{display:block;color:#71809D;font-size:10px;padding-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .sla-label{display:flex;align-items:center;justify-content:space-between;padding:0 10px 8px 0;font-size:14px}
    .sla-label strong{font-size:19px}
    .period-filter{float:right;background:#EDF2FF;color:#365BFF;padding:8px 15px;border-radius:10px;font-size:12px;font-weight:700;text-align:center;white-space:nowrap}
    div[data-testid="stVerticalBlockBorderWrapper"]{border-color:#DCE5F2!important;border-radius:14px!important;background:#FFFFFF!important}
    .stMain .block-container [data-testid="stVerticalBlock"][class*="zjh3i6"]{background:#FFFFFF!important;border-color:#DCE5F2!important;border-radius:14px!important}
    .inbox-row{display:flex;align-items:center;gap:12px;min-height:56px;border-bottom:1px solid #E8EDF5;padding:7px 3px;color:#1A2943}
    .inbox-row>span{flex:0 0 31px;width:31px;height:31px;border-radius:50%;color:white;display:grid;place-items:center;font-weight:700}
    .inbox-row>div{min-width:0;flex:1}.inbox-row b,.conversation-list-row b{font-size:14px}.inbox-row small,.conversation-list-row small{display:block;color:#71809D;font-size:11px;padding-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.inbox-row em{font-size:10px;font-style:normal;background:#EDF2FF;color:#365BFF;padding:7px 10px;border-radius:8px}
    .stMain .block-container [class*="st-key-inbox_select"] button{text-align:left!important;justify-content:flex-start!important;background:#fff!important;border-color:transparent!important;color:#1A2943!important;font-weight:650!important}
    .stMain .block-container [class*="st-key-inbox_select"] button>div,
    .stMain .block-container [class*="st-key-inbox_select"] button>div>span,
    .stMain .block-container [class*="st-key-inbox_select"] button>div>span>div,
    .stMain .block-container [class*="st-key-inbox_select"] button>div>span>div>p{width:100%!important;justify-content:flex-start!important;text-align:left!important}
    .stMain .block-container [class*="st-key-inbox_select"] button:hover{background:#F4F7FC!important;border-color:#DCE5F2!important}
    .customer-message,.agent-message{border-radius:16px;padding:14px 16px;margin:10px 0;background:#F0F3F9;max-width:92%;font-size:13px;line-height:1.55;color:#182841}
    .customer-message b,.agent-message b{display:block;color:#365BFF;font-size:12px;margin-bottom:6px}.agent-message{margin-left:auto;background:#EEF2FF}.customer-message small,.agent-message small{display:block;color:#71809D;margin-top:6px;font-size:10px}
    .conversation-list-row{padding:10px 4px 2px;border-bottom:1px solid #e8edf5}
    .document-row{display:flex;align-items:center;gap:12px;min-height:64px;padding:7px 4px;border-bottom:1px solid #E8EDF5;color:#192841}
    .document-row>span{width:31px;height:31px;border-radius:50%;color:white;display:grid;place-items:center;font-weight:700}.document-row>div{flex:1;min-width:0}.document-row b{font-size:14px}.document-row small{display:block;color:#71809D;font-size:11px;padding-top:4px}.document-row em{font-style:normal;color:#16A879;background:#E8F8F2;border-radius:8px;padding:7px 10px;font-size:11px}
    .brief-action{display:flex;align-items:center;gap:13px;padding:9px 4px;color:#1A2943;font-size:12px}.brief-action b{background:#EDF2FF;color:#365BFF;padding:7px 10px;border-radius:8px;font-size:10px}
    .footer-brand{display:flex;align-items:center;gap:12px;margin:0 0 12px;color:#11264b}.footer-brand>span{font-size:32px;color:#3157ff;line-height:1}.footer-brand b{display:block;font-size:18px}.footer-brand small{display:block;font-size:11px;color:#617595;margin-top:2px}
    .st-key-footer-columns [data-testid="stHorizontalBlock"] [data-testid="column"]:nth-child(n+2){border-left:1px solid #DCE5F2;padding-left:18px}
    h1,h2,h3{color:#102449}button[kind="primary"]{background:#245BEE;border-color:#245BEE}
    [data-testid="stPopover"] button{border-radius:9px}
    @media(max-width:1050px){section[data-testid="stSidebar"]{width:220px!important;min-width:220px!important}section[data-testid="stSidebar"]>div{width:220px!important}.block-container{padding-left:26px;padding-right:22px}.st-key-global_header{padding-left:26px}.creator-hero{grid-template-columns:150px minmax(0,1fr) 190px;gap:16px;padding:18px}.creator-photo-wrap img{width:150px;height:175px}.creator-project{padding:11px}.creator-project-title{gap:7px}}
    @media(max-width:700px){.st-key-global_header{display:none!important}.block-container{padding:14px!important}.ref-kpi{height:100px;padding:10px}.ref-kpi-icon{width:24px;height:24px}.ref-kpi strong{font-size:22px}.creator-hero{grid-template-columns:1fr;text-align:center}.creator-photo-wrap img{width:160px;height:180px}.creator-links{justify-content:center}.creator-focus-grid,.creator-project-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.creator-connect{grid-template-columns:1fr}.creator-section-heading{align-items:flex-start;gap:8px;flex-direction:column}.st-key-footer-columns [data-testid="stHorizontalBlock"] [data-testid="column"]{border-left:0!important;padding-left:0!important}}
    </style>
    """,unsafe_allow_html=True)
    if "page_nav" not in st.session_state: st.session_state.page_nav="Dashboard"
    with st.sidebar:
        st.markdown("## ◈ SupportIQ AI")
        st.caption("Intelligent Support Operations")
        st.markdown("---")
        page=st.session_state.page_nav
        for group,pages in NAV.items():
            for nav_page in pages:
                icon={"Dashboard":"⌂","Inbox":"▣","Tickets":"▤","Ticket Workspace":"▣","Customers":"♙","Conversations":"◉","AI Analysis":"✧","Knowledge Base":"▧","Analytics":"▥","AI Quality":"◉","Bulk Analysis":"▦","Daily Brief":"▤","Settings":"⚙","MCP Tools":"⌘","About / Creator":"♙","Feedback & Review":"✉"}.get(nav_page,"•")
                st.button(f"{icon}  {nav_page}",key=f"nav_{nav_page}",type="primary" if page==nav_page else "secondary",on_click=_navigate_to_page,args=(nav_page,),width="stretch")
        st.markdown("---")
    with st.container(key="global_header"):
        search_col,spacer,profile=st.columns([3.4,5.6,1.9],vertical_alignment="center")
        search_col.text_input("Search tickets, customers, or knowledge",key="global_search",placeholder="⌕  Search tickets, customers, or knowledge…",label_visibility="collapsed",on_change=_search_tickets)
        with profile:
            profile_photo,profile_menu=st.columns([.4,1],vertical_alignment="center")
            with profile_photo: _creator_avatar(30)
            with profile_menu:
                with st.popover("Gaurav  ▾",width="stretch"):
                    _creator_avatar(60)
                    st.markdown("**Gaurav**  \nAI Engineer")
                    st.caption("Building AI systems with LLMs, Agentic AI, RAG, LangGraph, MCP and machine learning.")
                    st.markdown("\n".join(f"[{name}]({url})" for name,url in CREATOR_LINKS.items()))
                    st.button("View Profile",key="top_view_profile",on_click=_navigate_to_creator,width="stretch")
                    st.button("Give Feedback",key="top_give_feedback",on_click=_navigate_to_feedback,width="stretch")
    renderers={"Dashboard":_dashboard,"Inbox":_inbox,"Tickets":_tickets,"Ticket Workspace":_workspace,"Customers":_customers,"Conversations":_conversations,"AI Analysis":_analysis,"Knowledge Base":_knowledge,"Analytics":_analytics,"AI Quality":_quality,"Bulk Analysis":_bulk,"Daily Brief":_brief,"Settings":_settings,"MCP Tools":_mcp,"About / Creator":_creator_gate,"Feedback & Review":_feedback}
    renderers[st.session_state.page_nav](settings)
    _footer()

def _search_tickets():
    st.session_state.page_nav="Tickets"
    st.session_state.ticket_search=st.session_state.get("global_search","")
