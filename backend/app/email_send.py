"""Outbound email — password reset + user notifications.

Sends via SMTP when `SMTP_HOST`/`SMTP_USER` are configured (set them in Zeabur env);
otherwise LOGS the message at INFO (so an unconfigured system still records exactly what
WOULD be sent, including reset links — retrievable from the runtime log by an admin, and
lets the forgot/reset flow work end-to-end before SMTP creds land). Never raises to the
caller — returns the delivery method used: "smtp" | "log" | "error".

(A Microsoft Graph sendMail path can be added later reusing the MS_* app creds, once the
Graph app is granted Mail.Send — the ingest path today is read-only.)
"""
import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import settings

log = logging.getLogger("ft.email_send")


def _sender() -> str:
    return settings.smtp_from or settings.smtp_user or "no-reply@fasttrackgroup.us"


def is_configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_user)


def send_email(to: str, subject: str, text_body: str, html_body: str | None = None) -> str:
    if is_configured():
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = _sender()
            msg["To"] = to
            msg.attach(MIMEText(text_body, "plain"))
            if html_body:
                msg.attach(MIMEText(html_body, "html"))
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as s:
                if settings.smtp_use_tls:
                    s.starttls(context=ssl.create_default_context())
                if settings.smtp_password:
                    s.login(settings.smtp_user, settings.smtp_password)
                s.sendmail(_sender(), [to], msg.as_string())
            log.info("email_sent_smtp", extra={"to": to, "subject": subject})
            return "smtp"
        except Exception as e:
            log.warning("email_smtp_failed to=%s err=%s", to, e)
            return "error"
    # Unconfigured — log what would be sent (incl. reset link) so the flow is usable now.
    log.info("email_not_configured_would_send", extra={"to": to, "subject": subject, "body": text_body})
    return "log"
