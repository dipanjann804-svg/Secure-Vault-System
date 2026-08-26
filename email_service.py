import os
import smtplib
import logging
from dotenv import load_dotenv
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

load_dotenv()
logger = logging.getLogger(__name__)

def send_password_reset_email(to_email: str, reset_url: str) -> bool:
    """
    Sends a secure password reset link to the user's email address via SMTP (e.g. Gmail).
    Includes automatic TLS/SSL fallback for maximum reliability across cloud hosting environments.
    """
    to_email = (to_email or "").strip()
    mail_server = os.environ.get("MAIL_SERVER", "smtp.gmail.com").strip()
    mail_port = int(os.environ.get("MAIL_PORT", 587))
    mail_username = (os.environ.get("MAIL_USERNAME") or "").strip()
    mail_password = (os.environ.get("MAIL_PASSWORD") or "").replace(" ", "").strip()
    mail_sender = os.environ.get("MAIL_DEFAULT_SENDER", mail_username or "noreply@securevault.local").strip()

    if not mail_username or not mail_password:
        print("[!] EMAIL ERROR: MAIL_USERNAME or MAIL_PASSWORD not configured. Please set them in Render Environment Variables or .env")
        logger.warning("MAIL_USERNAME or MAIL_PASSWORD not configured. Skipping email dispatch.")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Reset Your Password · Secure Vault System"
    msg["From"] = f"Secure Vault System <{mail_sender}>"
    msg["To"] = to_email

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

    msg.attach(MIMEText(text_content, "plain"))
    msg.attach(MIMEText(html_content, "html"))

    # Primary attempt
    ports_to_try = [mail_port]
    # Add fallback port
    fallback_port = 465 if mail_port == 587 else 587
    ports_to_try.append(fallback_port)

    last_error = None
    for port in ports_to_try:
        try:
            if port == 465:
                with smtplib.SMTP_SSL(mail_server, port, timeout=15) as server:
                    server.login(mail_username, mail_password)
                    server.sendmail(mail_sender, [to_email], msg.as_string())
            else:
                with smtplib.SMTP(mail_server, port, timeout=15) as server:
                    server.starttls()
                    server.login(mail_username, mail_password)
                    server.sendmail(mail_sender, [to_email], msg.as_string())

            print(f"[+] Password reset email sent successfully to {to_email} via port {port}")
            logger.info(f"Password reset email sent successfully to {to_email} via port {port}")
            return True
        except Exception as e:
            last_error = e
            print(f"[!] SMTP dispatch on port {port} failed: {e}. Trying fallback if available...")
            logger.warning(f"SMTP dispatch on port {port} failed: {e}")

    print(f"[!] EMAIL ERROR: All attempts to send password reset email to {to_email} failed. Last error: {last_error}")
    logger.error(f"Failed to send password reset email to {to_email}: {last_error}", exc_info=True)
    return False
