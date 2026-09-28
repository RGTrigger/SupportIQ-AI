"""Configurable SMTP, Twilio, and safe local mock communication adapters."""
from __future__ import annotations

import base64
import json
import re
import smtplib
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage

from supportiq.config import Settings


@dataclass
class DeliveryResult:
    success: bool
    status: str
    detail: str


class DemoEmailAdapter:
    def send(self, recipient: str, subject: str, body: str) -> DeliveryResult:
        return DeliveryResult(True,"demo_sent","Recorded locally. No external email was sent.")


class DemoSMSAdapter:
    def send(self, recipient: str, body: str) -> DeliveryResult:
        return DeliveryResult(True,"demo_sent","Recorded locally. No external SMS was sent.")


class SMTPEmailAdapter:
    def __init__(self, settings: Settings): self.settings=settings

    def send(self, recipient: str, subject: str, body: str) -> DeliveryResult:
        s=self.settings
        if not (s.smtp_host and s.email_from and s.smtp_username and s.smtp_password and recipient): raise RuntimeError("SMTP host, sender, username, password, or customer email is incomplete.")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",recipient): raise ValueError("A valid customer email address is required for SMTP delivery.")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",s.email_from): raise ValueError("A valid configured sender email address is required for SMTP delivery.")
        message=EmailMessage(); message["From"]=s.email_from; message["To"]=recipient; message["Subject"]=subject[:200]; message.set_content(body)
        context=ssl.create_default_context()
        try:
            with smtplib.SMTP(s.smtp_host,s.smtp_port,timeout=20) as server:
                server.ehlo()
                if s.smtp_starttls: server.starttls(context=context); server.ehlo()
                server.login(s.smtp_username,s.smtp_password)
                refused=server.send_message(message)
                if refused: raise RuntimeError("SMTP recipient was refused.")
        except Exception as exc:
            raise RuntimeError("SMTP provider rejected or could not accept the message.") from None
        return DeliveryResult(True,"sent_to_provider","SMTP accepted the message for delivery.")


class TwilioSMSAdapter:
    endpoint="https://api.twilio.com/2010-04-01/Accounts/{account}/Messages.json"

    def __init__(self, settings: Settings): self.settings=settings

    def send(self, recipient: str, body: str) -> tuple[DeliveryResult,str | None]:
        s=self.settings
        if not (s.twilio_account_sid and s.twilio_auth_token and s.twilio_from and recipient): raise RuntimeError("Twilio settings or customer phone are incomplete.")
        if not re.fullmatch(r"\+[1-9]\d{7,14}",recipient): raise ValueError("A customer phone number in E.164 format is required for SMS.")
        if len(body)>1600: raise ValueError("SMS message exceeds the provider's 1,600-character limit.")
        payload=urllib.parse.urlencode({"To":recipient,"From":s.twilio_from,"Body":body}).encode("utf-8")
        request=urllib.request.Request(self.endpoint.format(account=s.twilio_account_sid),data=payload,headers={"Content-Type":"application/x-www-form-urlencoded","Authorization":"Basic "+base64.b64encode(f"{s.twilio_account_sid}:{s.twilio_auth_token}".encode()).decode()},method="POST")
        try:
            with urllib.request.urlopen(request,timeout=20) as response:
                result=json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"SMS provider rejected the request (HTTP {exc.code}).") from None
        except Exception:
            raise RuntimeError("SMS provider could not accept the request.") from None
        status=str(result.get("status") or "accepted")
        return DeliveryResult(True,f"provider_{status}","Twilio accepted the message; final delivery status may update asynchronously."),result.get("sid")


def send_customer_message(settings: Settings, recipient: str, subject: str, body: str, channel: str, force_demo: bool=False) -> tuple[DeliveryResult,str | None]:
    if force_demo: channel_provider="demo"
    else: channel_provider=settings.email_provider if channel=="Email" else settings.sms_provider
    if channel_provider in {"demo","mock",""}:
        adapter=DemoEmailAdapter() if channel=="Email" else DemoSMSAdapter()
        result=adapter.send(recipient,subject,body) if channel=="Email" else adapter.send(recipient,body)
        return result,None
    if channel=="Email" and channel_provider=="smtp":
        result=SMTPEmailAdapter(settings).send(recipient,subject,body); return result,None
    if channel=="SMS" and channel_provider=="twilio":
        return TwilioSMSAdapter(settings).send(recipient,body)
    raise RuntimeError("The configured communication provider is not supported for this channel.")
