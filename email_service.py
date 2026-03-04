import os
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import aiosmtplib
from datetime import datetime

logger = logging.getLogger(__name__)

# ─── Configuration (from environment) ───────────────────────

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "Interviewed")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", SMTP_USER)
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")


def is_email_configured() -> bool:
    """Check if SMTP credentials are set."""
    return bool(SMTP_USER and SMTP_PASSWORD)


def _build_invite_html(candidate_email: str, job_title: str, company_name: str, invite_link: str, expires_at: datetime | None = None) -> str:
    """Generate a polished HTML email for the interview invite."""
    expiry_str = expires_at.strftime("%B %d, %Y at %I:%M %p UTC") if expires_at else None
    expiry_html = f"""
              <table width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 24px;">
                <tr>
                  <td style="padding:12px 16px;background-color:#1c1917;border-radius:8px;border:1px solid #44403c;">
                    <p style="margin:0;color:#fbbf24;font-size:13px;font-weight:600;">⏰ This invite expires on {expiry_str}</p>
                  </td>
                </tr>
              </table>
    """ if expiry_str else ""
    return f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background-color:#0a0a0a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#0a0a0a;padding:40px 20px;">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0" style="background-color:#171717;border-radius:16px;border:1px solid #262626;overflow:hidden;">
          <!-- Header -->
          <tr>
            <td style="background:linear-gradient(135deg,#059669 0%,#0d9488 100%);padding:32px 40px;text-align:center;">
              <h1 style="margin:0;color:#ffffff;font-size:24px;font-weight:700;letter-spacing:-0.5px;">
                You're Invited to Interview
              </h1>
            </td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="padding:40px;">
              <p style="margin:0 0 24px;color:#d4d4d4;font-size:16px;line-height:1.6;">
                Hi there,
              </p>
              <p style="margin:0 0 24px;color:#d4d4d4;font-size:16px;line-height:1.6;">
                <strong style="color:#ffffff;">{company_name}</strong> has invited you to complete an AI-moderated interview for the
                <strong style="color:#ffffff;">{job_title}</strong> position.
              </p>

              <!-- Info Card -->
              <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#0a0a0a;border-radius:12px;border:1px solid #262626;margin:0 0 32px;">
                <tr>
                  <td style="padding:20px 24px;">
                    <p style="margin:0 0 8px;color:#a3a3a3;font-size:13px;text-transform:uppercase;letter-spacing:1px;font-weight:600;">Position</p>
                    <p style="margin:0 0 16px;color:#ffffff;font-size:18px;font-weight:600;">{job_title}</p>
                    <p style="margin:0 0 8px;color:#a3a3a3;font-size:13px;text-transform:uppercase;letter-spacing:1px;font-weight:600;">Company</p>
                    <p style="margin:0;color:#ffffff;font-size:18px;font-weight:600;">{company_name}</p>
                  </td>
                </tr>
              </table>

              <p style="margin:0 0 32px;color:#d4d4d4;font-size:16px;line-height:1.6;">
                Click the button below to begin your interview. You'll be guided by an AI interviewer through a series of questions tailored to the role.
              </p>

              {expiry_html}

              <!-- CTA Button -->
              <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td align="center">
                    <a href="{invite_link}" style="display:inline-block;background-color:#ffffff;color:#000000;text-decoration:none;font-size:16px;font-weight:600;padding:14px 40px;border-radius:12px;letter-spacing:-0.2px;">
                      Start Your Interview →
                    </a>
                  </td>
                </tr>
              </table>

              <!-- Link Fallback -->
              <p style="margin:32px 0 0;color:#737373;font-size:13px;line-height:1.5;word-break:break-all;">
                Or copy this link: <a href="{invite_link}" style="color:#34d399;">{invite_link}</a>
              </p>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="padding:24px 40px;border-top:1px solid #262626;text-align:center;">
              <p style="margin:0;color:#525252;font-size:13px;">
                Powered by <strong style="color:#737373;">Interviewed</strong> · AI-Moderated Interviews
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def _build_invite_text(candidate_email: str, job_title: str, company_name: str, invite_link: str, expires_at: datetime | None = None) -> str:
    """Plain text fallback for the invite email."""
    expiry_line = f"\n⏰ This invite expires on {expires_at.strftime('%B %d, %Y at %I:%M %p UTC')}.\n" if expires_at else ""
    return f"""You're Invited to Interview!

Hi there,

{company_name} has invited you to complete an AI-moderated interview for the {job_title} position.

Start your interview here: {invite_link}
{expiry_line}
You'll be guided by an AI interviewer through a series of questions tailored to the role.

---
Powered by Interviewed · AI-Moderated Interviews
"""


async def send_invite_email(
    candidate_email: str,
    job_title: str,
    company_name: str,
    invite_token: str,
    expires_at: datetime | None = None,
) -> bool:
    """Send an interview invite email to a candidate.
    
    Returns True if sent successfully, False otherwise.
    """
    if not is_email_configured():
        logger.warning("SMTP not configured — skipping email send. Set SMTP_USER and SMTP_PASSWORD in .env")
        return False

    invite_link = f"{FRONTEND_URL}/invite/{invite_token}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"You're invited to interview for {job_title} at {company_name}"
    msg["From"] = f"{SMTP_FROM_NAME} <{SMTP_FROM_EMAIL}>"
    msg["To"] = candidate_email

    # Attach plain text + HTML parts (HTML preferred by email clients)
    msg.attach(MIMEText(_build_invite_text(candidate_email, job_title, company_name, invite_link, expires_at), "plain"))
    msg.attach(MIMEText(_build_invite_html(candidate_email, job_title, company_name, invite_link, expires_at), "html"))

    try:
        await aiosmtplib.send(
            msg,
            hostname=SMTP_HOST,
            port=SMTP_PORT,
            username=SMTP_USER,
            password=SMTP_PASSWORD,
            use_tls=False,
            start_tls=True,
        )
        logger.info(f"Invite email sent to {candidate_email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {candidate_email}: {e}")
        return False
