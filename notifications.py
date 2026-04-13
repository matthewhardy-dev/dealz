"""Notification system for Deal Finder Pro — Email and SMS alerts."""
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ---- Configuration (set these as environment variables) ----
# Email: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM
# SMS: TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM

def send_email_alert(to_email, subject, deal):
    """Send email notification about a deal."""
    smtp_host = os.environ.get("SMTP_HOST", "")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")
    smtp_from = os.environ.get("SMTP_FROM", smtp_user)

    if not smtp_host or not smtp_user:
        print(f"[ALERT] Email not configured. Would send to {to_email}: {subject}")
        return False

    price_str = f"${deal.get('price', 0):,.2f}" if deal.get("price") else "N/A"
    discount = f"{deal.get('discount_pct', 0)}% OFF" if deal.get("discount_pct") else ""
    store = deal.get("store", "Unknown")

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;background:#111;color:#e2e2ea;padding:24px;border-radius:12px">
        <h2 style="color:#f59e0b;margin:0 0 16px">🏷️ Deal Alert!</h2>
        <div style="background:#1a1a24;padding:16px;border-radius:8px;border:1px solid #2a2a3a">
            <h3 style="margin:0 0 8px;color:#e2e2ea">{deal.get('name', 'Unknown')}</h3>
            <p style="margin:4px 0">
                <span style="font-size:24px;font-weight:800;color:#22c55e">{price_str}</span>
                {f'<span style="text-decoration:line-through;color:#6b6b80;margin-left:8px">${deal.get("original_price", 0):,.2f}</span>' if deal.get("original_price") else ''}
                {f'<span style="background:#f59e0b;color:#000;padding:2px 8px;border-radius:4px;font-weight:700;margin-left:8px">{discount}</span>' if discount else ''}
            </p>
            <p style="color:#6b6b80;font-size:13px">from {store}</p>
            <a href="{deal.get('url', '#')}" style="display:inline-block;margin-top:12px;padding:10px 24px;background:#6366f1;color:#fff;text-decoration:none;border-radius:8px;font-weight:700">View Deal →</a>
        </div>
        <p style="color:#6b6b80;font-size:11px;margin-top:16px">You're receiving this because you set a price alert on Deal Finder Pro.</p>
    </div>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = to_email
    msg.attach(MIMEText(f"Deal Alert: {deal.get('name')} is now {price_str} ({discount}) at {store}. View: {deal.get('url', '')}", "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"[ALERT] Email failed: {e}")
        return False


def send_sms_alert(to_phone, deal):
    """Send SMS notification about a deal via Twilio."""
    sid = os.environ.get("TWILIO_SID", "")
    token = os.environ.get("TWILIO_TOKEN", "")
    from_phone = os.environ.get("TWILIO_FROM", "")

    if not sid or not token:
        print(f"[ALERT] SMS not configured. Would send to {to_phone}")
        return False

    try:
        from twilio.rest import Client
        client = Client(sid, token)
        price_str = f"${deal.get('price', 0):,.2f}" if deal.get("price") else "N/A"
        discount = f" ({deal.get('discount_pct')}% OFF)" if deal.get("discount_pct") else ""
        body = f"🏷️ Deal Alert! {deal.get('name', 'Item')[:80]} is now {price_str}{discount} at {deal.get('store', 'Unknown')}. {deal.get('url', '')}"
        message = client.messages.create(body=body, from_=from_phone, to=to_phone)
        return True
    except Exception as e:
        print(f"[ALERT] SMS failed: {e}")
        return False


def process_alerts(triggered_alerts):
    """Process all triggered alerts — send emails and SMS."""
    sent = 0
    for item in triggered_alerts:
        alert = item["alert"]
        deal = item["deal"]
        user = item["user"]

        price_str = f"${deal.get('price', 0):,.2f}" if deal.get("price") else ""
        subject = f"🏷️ Price Drop: {deal.get('name', 'Item')[:50]} now {price_str}"

        if alert.notify_email and user.email:
            if send_email_alert(user.email, subject, deal):
                sent += 1

        if alert.notify_sms and user.phone:
            if send_sms_alert(user.phone, deal):
                sent += 1

    return sent
