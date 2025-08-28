# app/emailer.py
import os, smtplib, logging
from email.mime.text import MIMEText

log = logging.getLogger(__name__)

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USERNAME")
SMTP_PASS = os.getenv("SMTP_PASSWORD")
SMTP_FROM = os.getenv("SMTP_FROM")
SMTP_STARTTLS = os.getenv("SMTP_STARTTLS", "true").lower() in ("1", "true", "yes")

def send_email(to_addr: str, subject: str, body: str) -> bool:
	# Basic validation
	missing = [k for k,v in {
		"SMTP_HOST": SMTP_HOST, "SMTP_USER": SMTP_USER,
		"SMTP_PASS": SMTP_PASS, "SMTP_FROM": SMTP_FROM
	}.items() if not v]
	if missing:
		log.warning("Email disabled; missing env: %s", ", ".join(missing))
		return False

	try:
		msg = MIMEText(body, "plain", "utf-8")
		msg["Subject"] = subject
		msg["From"] = SMTP_FROM
		msg["To"] = to_addr

		with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as s:
			if SMTP_STARTTLS:
				s.starttls()
			s.login(SMTP_USER, SMTP_PASS)
			s.send_message(msg)
		return True
	except Exception as e:
		log.error("Email send failed: %s", e)
		return False
