"""Temporary SMTP fallback for the 2026-09 shared Listmonk outage.

Confirmed same root cause as IBF_TYPHOON_DATA_PIPELINE and hitting this
pipeline too: the shared Listmonk instance times out / 500s on every
DB-backed endpoint (health, /campaigns, create_campaign), so every
scheduled run failed on send_monitoring_email from ~04:00 UTC
2026-09-24 onward.

This repo has no CHD_DS_* secrets (those are repo-level secrets on
IBF_TYPHOON_DATA_PIPELINE only). It does have DSCI_AWS_EMAIL_* org
secrets, which are the exact working AWS SMTP pattern already proven in
ds-hurricanes-monitoring (src/email/send_emails.py): plain
smtplib.SMTP_SSL on port 465, no Listmonk/ocha-relay involved.

Recipients are hardcoded because Listmonk's own subscriber list cannot
be queried while Listmonk itself is down; they mirror list 121 (live)
and 103 (test) membership as last verified 2026-09-07 - the same two
lists the IBF typhoon pipeline uses, since both products share the
Philippines typhoon distribution.

The email body is wrapped in a frozen static copy of OCHA's Listmonk
campaign template (id 8, the same instance/template IBF_TYPHOON_DATA_
PIPELINE uses), captured from a real rendered campaign on 2026-09-22,
so the fallback still carries the OCHA header bar, "automated message"
strip and Contact/logo footer. The per-recipient merge-tag footer
(unsubscribe / view-in-browser / tracking pixel) is dropped - it cannot
resolve outside a real Listmonk send.

Remove this module and the try/except in send_monitoring_email once
Listmonk is confirmed healthy again (a normal create_campaign call
succeeding is proof enough).
"""

from __future__ import annotations

import html
import logging
import os
import smtplib
import ssl
from email.headerregistry import Address
from email.message import EmailMessage

logger = logging.getLogger(__name__)

SMTP_PORT = 465

# Mirrors IBF_TYPHOON_DATA_PIPELINE's list 121 / 103 membership as of
# 2026-09-07. Update here if membership changes during the outage.
FALLBACK_RECIPIENTS = {
    True: ["pauline.ndirangu@un.org", "leonardo.milano@un.org"],  # list 121
    False: ["pauline.ndirangu@un.org"],  # list 103
}

# Static copy of OCHA's Listmonk campaign template (id 8) - see module
# docstring. A frozen snapshot, not a live template fetch; if OCHA
# redesigns the template while this stopgap is in use, this drifts.
_TEMPLATE_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta http-equiv="Content-Type" content="text/html; charset=us-ascii">
  <meta name="viewport" content="width=device-width">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <meta name="x-apple-disable-message-reformatting">
  <meta name="format-detection" content="telephone=no,address=no,email=no,date=no,url=no">
  <meta name="color-scheme" content="light">
  <meta name="supported-color-schemes" content="light">
  <title>__SUBJECT__</title>
  <style type="text/css">
     
    body, table, td, p, h1, h2, h3, a, span, div {
      font-family: "Source Sans Pro", Helvetica, Arial, sans-serif !important;
    }
    img { max-width: 100%; border: 0; display: block; }
    a { color: #0066cc; text-decoration: underline; }
    @media only screen and (max-width: 767px) {
      .container { width: 100% !important; }
    }
  </style>
</head>

<body style="margin:0;padding:0;background-color:#f6f6f6;font-family:Helvetica,Arial,sans-serif;color:#222222;font-size:16px;line-height:1.4;">

  <table width="85%" align="center" class="container" cellpadding="0" cellspacing="0" role="presentation" style="max-width:1000px;margin:0 auto;">
    <tr>
      <td style="padding:24px;">

        
        <table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background-color:#ffffff;">
          
          <tr>
            <td style="padding:0;background-color:#009EDB;">
              <div style="background-color:#009EDB;padding:12px 16px 8px 16px;">
                <h1 style="font-size:20px;margin:0;color:#ffffff;font-weight:600;font-family:Helvetica,Arial,sans-serif;line-height:1.3;">__SUBJECT__</h1>
              </div>
            </td>
          </tr>

          
          <tr>
            <td style="padding:12px;background-color:#ffffff;"><div style="font-size:14px;padding:6px 10px;background-color:#F0F0F0;color:#000000;font-style:italic;margin-bottom:12px;font-family:Helvetica,Arial,sans-serif;">This is an automated message produced by the OCHA Centre for Humanitarian Data.</div><div style="margin-top:16px;">"""
_TEMPLATE_TAIL = """
                <hr style="border:none;border-top:1px solid #dddddd;margin:24px 0;">
              </div>

              
              <div><h2 style="font-size:24px;margin:0 0 12px 0;font-weight:600;font-family:Helvetica,Arial,sans-serif;color:#222222;">Contact</h2>
                <p style="font-size:16px;margin:0 0 12px 0;font-family:Helvetica,Arial,sans-serif;color:#222222;">
                  Contact the OCHA Centre for Humanitarian Data at
                  <a href="mailto:ocha-datascience@un.org" style="color:#0066cc;text-decoration:underline;">ocha-datascience@un.org</a>
                  with any questions or feedback.
                </p><hr style="border:none;border-top:1px solid #dddddd;margin:24px 0;">

                <table width="100%" align="center" cellpadding="0" cellspacing="0" role="presentation">
                  <tr>
                    <td align="center" style="padding-bottom:12px;">
                      <img
                        src="https://cdn-assets-cloud.frontify.com/s3/frontify-cloud-files-us/eyJwYXRoIjoiZnJvbnRpZnlcL2ZpbGVcL3k0eGRBdGJ4eEpOZVlnNlBRNzk2LnN2ZyJ9:frontify:lVIqvVTFvNBjRkae8w5bPJvCHqUgYQO7p3PwWJj-l3Y"
                        alt="OCHA Logo"
                        width="200"
                        style="width:25%;max-width:200px;margin:0 auto;border:0;display:block;"
                      >
                    </td>
                  </tr>
                </table>

                <p style="text-align:center;margin:0 0 4px 0;font-size:16px;font-family:Helvetica,Arial,sans-serif;color:#222222;">
                  <strong>OCHA Centre for Humanitarian Data</strong>
                </p>
              </div>

            </td>
          </tr>
        </table>

        
        </td>
    </tr>
  </table>

  

</body>
</html>"""


def _wrap_in_template(subject, message_html):
    """Wrap a raw email body in the static Listmonk-template chrome."""
    head = _TEMPLATE_HEAD.replace("__SUBJECT__", html.escape(subject))
    return head + message_html + _TEMPLATE_TAIL


def _require_env(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Environment variable {name} is required but not set.")
    return value


def send_via_smtp(subject, message_html, live):
    """Send a plain HTML email over SMTP, bypassing Listmonk entirely."""
    smtp_host = _require_env("DSCI_AWS_EMAIL_HOST")
    username = _require_env("DSCI_AWS_EMAIL_USERNAME")
    password = _require_env("DSCI_AWS_EMAIL_PASSWORD")
    from_address = _require_env("DSCI_AWS_EMAIL_ADDRESS")
    to_addresses = FALLBACK_RECIPIENTS[live]

    message = EmailMessage()
    message["Subject"] = subject
    local_part, domain = from_address.split("@")
    message["From"] = Address("OCHA Centre for Humanitarian Data", local_part, domain)
    message["To"] = to_addresses
    message.add_alternative(_wrap_in_template(subject, message_html), subtype="html")

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(smtp_host, SMTP_PORT, context=context, timeout=30) as server:
        server.login(username, password)
        server.send_message(message)

    logger.info(f"Sent fallback SMTP email to {to_addresses}")
