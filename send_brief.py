#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import urllib.request


def _env(name: str) -> str:
    v = (os.environ.get(name) or "").strip()
    if not v:
        raise SystemExit(f"Missing required env var: {name}")
    return v


def main(argv: list[str]) -> int:
    url = _env("BRIEF_APPS_SCRIPT_URL")
    secret = _env("BRIEF_SHARED_SECRET")
    to = _env("BRIEF_EMAIL_TO")
    subject = (os.environ.get("BRIEF_EMAIL_SUBJECT") or "").strip() or "Executive Partner Brief (v2)"
    html_path = (os.environ.get("BRIEF_OUT") or "").strip()
    if not html_path:
        root = os.path.dirname(os.path.abspath(__file__))
        html_path = os.path.join(root, "..", "out", "executive_partner_brief_v2.html")

    with open(html_path, "r", encoding="utf-8") as f:
        html_body = f.read()

    payload = {"secret": secret, "to": to, "subject": subject, "html": html_body}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "ExecutivePartnerBrief/2.0 (+send)",
        },
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        if resp.status < 200 or resp.status >= 300:
            raise SystemExit(f"Apps Script error HTTP {resp.status}: {body}")
        print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

