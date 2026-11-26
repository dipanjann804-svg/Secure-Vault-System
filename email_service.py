import os
import ssl
import json
import logging
import smtplib
import socket
import urllib.request
import urllib.error
import html
from dotenv import load_dotenv
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

load_dotenv()
logger = logging.getLogger(__name__)


class EmailResult:
    """Wrapper that acts as a boolean and a tuple (success, message)."""
    def __init__(self, success: bool, message: str = ""):
        self.success = bool(success)
        self.message = str(message)

    def __bool__(self):
        return self.success

    def __iter__(self):
        return iter((self.success, self.message))

    def __repr__(self):
        return f"EmailResult(success={self.success}, message={self.message!r})"


def _send_via_resend(api_key: str, to_email: str, subject: str, html_content: str, text_content: str, sender: str) -> tuple[bool, str]:
    """Sends email via Resend HTTP API (Port 443 - works on Render Free Tier)."""
    url = "https://api.resend.com/emails"
    from_email = sender if ("@" in sender and not sender.endswith(".local") and "gmail.com" not in sender) else "onboarding@resend.dev"
    payload = {
        "from": from_email,
        "to": [to_email],
        "subject": subject,
        "html": html_content,
        "text": text_content,
    }
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
        "User-Agent": "SecureVault/1.0"
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status in (200, 201):
                return True, "Email sent successfully via Resend API"
            return False, f"Resend API returned HTTP {response.status}"
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        if "not allowed by policy" in err_body or e.code == 403:
            return False, f"Resend API 403: Key lacks sending permissions or unverified sending domain ({err_body})"
        return False, f"Resend API error ({e.code}): {err_body}"
    except Exception as e:
        return False, f"Resend request failed: {e}"


def _send_via_brevo(api_key: str, to_email: str, subject: str, html_content: str, text_content: str, sender: str) -> tuple[bool, str]:
    """Sends email via Brevo HTTP API (Port 443 - works on Render Free Tier)."""
    url = "https://api.brevo.com/v3/smtp/email"
    sender_email = sender if ("@" in sender and not sender.endswith(".local")) else "admin@securevault.com"
    payload = {
        "sender": {"name": "Secure Vault System", "email": sender_email},
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": html_content,
        "textContent": text_content,
    }
    headers = {
        "api-key": api_key.strip(),
        "Content-Type": "application/json",
        "accept": "application/json",
        "User-Agent": "SecureVault/1.0"
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status in (200, 201):
                return True, "Email sent successfully via Brevo API"
            return False, f"Brevo API returned HTTP {response.status}"
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        return False, f"Brevo API error ({e.code}): {err_body}"
    except Exception as e:
        return False, f"Brevo request failed: {e}"


def _send_via_sendgrid(api_key: str, to_email: str, subject: str, html_content: str, text_content: str, sender: str) -> tuple[bool, str]:
    """Sends email via SendGrid HTTP API (Port 443 - works on Render Free Tier)."""
    url = "https://api.sendgrid.com/v3/mail/send"
    sender_email = sender if ("@" in sender and not sender.endswith(".local")) else "no-reply@securevault.com"
    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"name": "Secure Vault System", "email": sender_email},
        "subject": subject,
        "content": [
            {"type": "text/plain", "value": text_content},
            {"type": "text/html", "value": html_content}
        ]
    }
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
        "User-Agent": "SecureVault/1.0"
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status in (200, 201, 202):
                return True, "Email sent successfully via SendGrid API"
            return False, f"SendGrid API returned HTTP {response.status}"
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        return False, f"SendGrid API error ({e.code}): {err_body}"
    except Exception as e:
        return False, f"SendGrid request failed: {e}"


def _deliver_email(to_email: str, subject: str, html_content: str, text_content: str) -> EmailResult:
    """Send through the configured provider chain (HTTP APIs first, then SMTP)."""
    to_email = (to_email or "").strip()

    # 1. Try Resend HTTP API if configured (Port 443, works on Render Free Tier)
    resend_key = os.environ.get("RESEND_API_KEY", "").strip()
    if resend_key:
        sender = os.environ.get("MAIL_DEFAULT_SENDER", "").strip()
        success, msg = _send_via_resend(resend_key, to_email, subject, html_content, text_content, sender)
        if success:
            print(f"[+] Email sent via Resend API to {to_email}")
            return EmailResult(True, msg)
        print(f"[!] Resend API failed: {msg}. Falling back to SMTP if configured...")

    # 2. Try Brevo HTTP API if configured
    brevo_key = (os.environ.get("BREVO_API_KEY") or os.environ.get("SENDINBLUE_API_KEY") or "").strip()
    if brevo_key:
        sender = os.environ.get("MAIL_DEFAULT_SENDER", "").strip()
        success, msg = _send_via_brevo(brevo_key, to_email, subject, html_content, text_content, sender)
        if success:
            print(f"[+] Email sent via Brevo API to {to_email}")
            return EmailResult(True, msg)
        print(f"[!] Brevo API failed: {msg}. Falling back to SMTP if configured...")

    # 3. Try SendGrid HTTP API if configured
    sendgrid_key = os.environ.get("SENDGRID_API_KEY", "").strip()
    if sendgrid_key:
        sender = os.environ.get("MAIL_DEFAULT_SENDER", "").strip()
        success, msg = _send_via_sendgrid(sendgrid_key, to_email, subject, html_content, text_content, sender)
        if success:
            print(f"[+] Email sent via SendGrid API to {to_email}")
            return EmailResult(True, msg)
        print(f"[!] SendGrid API failed: {msg}. Falling back to SMTP if configured...")

    # 4. SMTP configuration (Gmail or custom SMTP)
    mail_server = (os.environ.get("MAIL_SERVER") or "smtp.gmail.com").strip()
    raw_port = (os.environ.get("MAIL_PORT") or "").strip()
    mail_port = int(raw_port) if raw_port.isdigit() else 587
    mail_username = (os.environ.get("MAIL_USERNAME") or "").strip()
    mail_password = (os.environ.get("MAIL_PASSWORD") or "").replace(" ", "").strip()

    # For Gmail, the sender MUST match the authenticated Gmail username to prevent spam flagging
    mail_sender = (os.environ.get("MAIL_DEFAULT_SENDER") or "").strip()
    if not mail_sender or "noreply@securevault.local" in mail_sender or "gmail.com" in mail_server.lower():
        mail_sender = mail_username or "noreply@securevault.local"

    if not mail_username or not mail_password:
        err_msg = (
            "MAIL_USERNAME or MAIL_PASSWORD not configured. "
            "Render Free Tier blocks outbound SMTP ports (25, 465, 587). "
            "To send emails via HTTP without port restrictions, set RESEND_API_KEY or BREVO_API_KEY in Render Environment Variables."
        )
        print("\n" + "=" * 70)
        print("  [!] EMAIL CONFIGURATION ERROR")
        print("=" * 70)
        print("  " + err_msg)
        print("=" * 70 + "\n", flush=True)
        logger.warning(err_msg)
        return EmailResult(False, err_msg)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Secure Vault System <{mail_sender}>"
    msg["To"] = to_email
    msg.attach(MIMEText(text_content, "plain"))
    msg.attach(MIMEText(html_content, "html"))

    ports_to_try = [mail_port]
    fallback_port = 465 if mail_port == 587 else 587
    if fallback_port not in ports_to_try:
        ports_to_try.append(fallback_port)

    ssl_context = ssl.create_default_context()
    last_error = None

    for port in ports_to_try:
        try:
            print(f"[*] Attempting SMTP delivery to {mail_server}:{port}...")
            if port == 465:
                with smtplib.SMTP_SSL(mail_server, port, timeout=4, context=ssl_context) as server:
                    server.ehlo()
                    server.login(mail_username, mail_password)
                    server.sendmail(mail_sender, [to_email], msg.as_string())
            else:
                with smtplib.SMTP(mail_server, port, timeout=4) as server:
                    server.ehlo()
                    server.starttls(context=ssl_context)
                    server.ehlo()
                    server.login(mail_username, mail_password)
                    server.sendmail(mail_sender, [to_email], msg.as_string())

            print(f"[+] Email successfully sent to {to_email} via {mail_server}:{port}!")
            return EmailResult(True, f"Email sent via {mail_server}:{port}")

        except smtplib.SMTPAuthenticationError:
            err_msg = (
                "Gmail rejected your credentials (535 Authentication Failed). "
                "Ensure you are using a 16-character Google App Password (not your regular Gmail password) "
                "and 2-Step Verification is active on your Google account. "
                "Visit: https://myaccount.google.com/apppasswords"
            )
            print("\n" + "=" * 70)
            print("  [!] GMAIL AUTHENTICATION ERROR")
            print("=" * 70)
            print("  " + err_msg)
            print("=" * 70 + "\n", flush=True)
            return EmailResult(False, err_msg)

        except (socket.timeout, TimeoutError, smtplib.SMTPConnectError, OSError) as e:
            last_error = e
            print(f"[!] SMTP connection on port {port} failed: {e}")
            err_str = str(e)
            if "101" in err_str or "unreachable" in err_str.lower() or getattr(e, 'errno', None) == 101:
                print("[!] Host firewall blocked outbound SMTP (network unreachable). Fast-failing remaining ports.")
                break

        except Exception as e:
            last_error = e
            print(f"[!] SMTP error on port {port}: {e}")

    diagnostic_msg = (
        f"SMTP connection to {mail_server} timed out or failed ({last_error}). "
        "Render Free Tier blocks outbound SMTP ports (25, 465, 587). "
        "To resolve this, set RESEND_API_KEY or BREVO_API_KEY in Render Environment Variables (free at https://resend.com, works over HTTP 443), "
        "or upgrade Render to a paid plan."
    )

    print("\n" + "=" * 70)
    print("  [!] SMTP DELIVERY FAILED (RENDER FIREWALL)")
    print("=" * 70)
    print("  " + diagnostic_msg)
    print("=" * 70 + "\n", flush=True)

    return EmailResult(False, diagnostic_msg)


def send_password_reset_email(to_email: str, reset_url: str) -> EmailResult:
    """
    Sends a secure password reset link to the user's email address.
    Supports:
      1. HTTP-based APIs (Resend, Brevo) over Port 443 (Recommended for Render Free Tier).
      2. Gmail / SMTP over Port 587 (STARTTLS) or Port 465 (SSL).
    """
    to_email = (to_email or "").strip()
    subject = "Reset Your Password · Secure Vault System"

    # Plain text fallback
    text_content = f"""Hello,

We received a request to reset your password for your Secure Vault account.

To reset your password, please click the following link (valid for 1 hour):
{reset_url}

If you did not request a password reset, please ignore this email or contact support. Your password will remain unchanged.

Best regards,
Secure Vault Security Team
"""

    # HTML formatted email
    html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #0f172a; color: #f8fafc; margin: 0; padding: 24px; }}
    .container {{ max-width: 540px; margin: 0 auto; background-color: #1e293b; border-radius: 16px; border: 1px solid #334155; padding: 32px; }}
    .logo {{ font-size: 20px; font-weight: bold; color: #ffffff; margin-bottom: 24px; }}
    h1 {{ font-size: 22px; color: #ffffff; margin-top: 0; }}
    p {{ font-size: 14px; line-height: 1.6; color: #cbd5e1; }}
    .btn-container {{ text-align: center; margin: 28px 0; }}
    .btn {{ display: inline-block; background-color: #6366f1; color: #ffffff !important; text-decoration: none; font-weight: 600; font-size: 15px; padding: 12px 28px; border-radius: 10px; }}
    .link-alt {{ font-size: 12px; color: #94a3b8; word-break: break-all; margin-top: 20px; }}
    .footer {{ font-size: 12px; color: #64748b; margin-top: 32px; border-top: 1px solid #334155; padding-top: 16px; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="logo">🔒 Secure Vault System</div>
    <h1>Password Reset Request</h1>
    <p>Hello,</p>
    <p>We received a request to reset the password associated with your email address (<strong>{to_email}</strong>).</p>

    <div class="btn-container">
      <a href="{reset_url}" class="btn" target="_blank">Reset My Password</a>
    </div>

    <p>This password reset link is valid for <strong>1 hour</strong>. If you did not make this request, you can safely ignore this email; your account remains secure.</p>

    <div class="link-alt">
      If the button above does not work, copy and paste this URL into your web browser:<br>
      <a href="{reset_url}" style="color: #818cf8;">{reset_url}</a>
    </div>

    <div class="footer">
      &copy; Secure Vault System. All rights reserved.
    </div>
  </div>
</body>
</html>
"""

    return _deliver_email(to_email, subject, html_content, text_content)


def send_share_email(
    to_email: str,
    recipient_name: str,
    owner_name: str,
    share_url: str,
    expires_in: str,
    item_label: str,
    item_count: int,
    is_folder: bool = False,
    has_password: bool = False,
) -> EmailResult:
    """Send a share link. Never includes a link password."""
    recipient = (recipient_name or "there").strip()
    owner = (owner_name or "A Secure Vault user").strip()
    label = (item_label or "a file").strip()
    # A name or filename must never inject a new email header.
    subject = " ".join(
        f"{owner} shared {label} with you · Secure Vault".splitlines()
    )[:180]

    safe_recipient = html.escape(recipient)
    safe_owner = html.escape(owner)
    safe_label = html.escape(label)
    safe_url = html.escape(share_url)
    kind_label = "folder bundle" if is_folder else "file"
    count_line = f"{item_count} files" if item_count != 1 else "1 file"

    text_content = f"""Hello {recipient},

{owner} shared a {kind_label} with you from Secure Vault.

{label} ({count_line})
This link expires in {expires_in}.

Open it here:
{share_url}

{'A password is required. Ask the sender for it separately; it is not included in this email.' if has_password else 'No password is required for this link.'}

If you were not expecting this file, do not open the link.
"""

    html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #0f172a; color: #f8fafc; margin: 0; padding: 24px; }}
    .container {{ max-width: 540px; margin: 0 auto; background-color: #1e293b; border-radius: 16px; border: 1px solid #334155; padding: 32px; }}
    .logo {{ font-size: 20px; font-weight: bold; color: #ffffff; margin-bottom: 24px; }}
    h1 {{ font-size: 22px; color: #ffffff; margin-top: 0; }}
    p {{ font-size: 14px; line-height: 1.6; color: #cbd5e1; }}
    .btn-container {{ text-align: center; margin: 28px 0; }}
    .btn {{ display: inline-block; background-color: #6366f1; color: #ffffff !important; text-decoration: none; font-weight: 600; font-size: 15px; padding: 12px 28px; border-radius: 10px; }}
    .meta {{ font-size: 13px; color: #94a3b8; }}
    .warning {{ font-size: 12px; color: #fbbf24; }}
    .link-alt {{ font-size: 12px; color: #94a3b8; word-break: break-all; margin-top: 20px; }}
    .footer {{ font-size: 12px; color: #64748b; margin-top: 32px; border-top: 1px solid #334155; padding-top: 16px; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="logo">🔒 Secure Vault System</div>
    <h1>You received a shared {kind_label}</h1>
    <p>Hello {safe_recipient},</p>
    <p><strong>{safe_owner}</strong> shared <strong>{safe_label}</strong> with you.</p>
    <p class="meta">{count_line} · Expires in {html.escape(expires_in)}</p>
    <div class="btn-container">
      <a href="{safe_url}" class="btn" target="_blank">Open shared file</a>
    </div>
    <p class="{'warning' if has_password else 'meta'}">
      {'A password is required. Ask the sender for it separately; it is not included in this email.' if has_password else 'No password is required for this link.'}
    </p>
    <div class="link-alt">
      If the button does not work, copy this URL:<br>
      <a href="{safe_url}" style="color: #818cf8;">{safe_url}</a>
    </div>
    <div class="footer">
      &copy; Secure Vault System. If you were not expecting this file, do not open the link.
    </div>
  </div>
</body>
</html>
"""

    return _deliver_email(to_email, subject, html_content, text_content)
