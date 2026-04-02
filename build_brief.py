#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional


PPLX_API_URL = os.environ.get("PPLX_API_URL", "https://api.perplexity.ai/v1/sonar").strip() or "https://api.perplexity.ai/v1/sonar"


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _env_opt(name: str, default: str = "") -> str:
    return (os.environ.get(name, default) or "").strip()


def _env_req(name: str) -> str:
    v = _env_opt(name)
    if not v:
        raise SystemExit(f"Missing required env var: {name}")
    return v


def _http_post_json(url: str, *, headers: dict[str, str], payload: dict[str, Any], timeout_s: int = 60) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "ExecutivePartnerBrief/2.0 (+perplexity)",
            **headers,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        if resp.status < 200 or resp.status >= 300:
            raise SystemExit(f"Perplexity HTTP {resp.status}: {body}")
        try:
            return json.loads(body)
        except Exception as e:
            raise SystemExit(f"Failed to parse Perplexity JSON: {e}\n{body[:5000]}")


def build_prompt(partner: str) -> str:
    return (
        "You are preparing an executive partner brief.\n\n"
        f"Partner: {partner}\n"
        "Time window: last 7 days only\n"
        "Geography: Europe and the US\n"
        "Scope: major news, leadership changes, product launches, M&A/funding, major partnerships\n\n"
        "Return EXACTLY this format:\n"
        f"Partner: {partner}\n"
        "- [Category] (Region, YYYY-MM-DD) Bullet summary. Source: <url>\n"
        "- ... (3–8 bullets max)\n\n"
        "Category must be one of: LeadershipChange | ProductLaunch | FundingOrMA | MajorNews | Other\n"
        "Region must be one of: EU | US | Global | Unknown\n\n"
        "Rules:\n"
        "- Only include items clearly within the last 7 days; if unsure, exclude.\n"
        "- Prefer primary sources (company newsroom/blog) or credible outlets.\n"
        "- If nothing found, return:\n"
        f"Partner: {partner}\n"
        "- No significant updates found in the last 7 days.\n"
    )


@dataclass(frozen=True)
class Bullet:
    category: str
    region: str
    date: str
    summary: str
    source_url: str
    raw: str


@dataclass(frozen=True)
class PartnerBrief:
    partner: str
    bullets: list[Bullet]
    raw_text: str
    citations: list[str]


_BULLET_RE = re.compile(
    r"^\s*-\s*\[(?P<cat>[^\]]+)\]\s*\(\s*(?P<region>[^,]+)\s*,\s*(?P<date>\d{4}-\d{2}-\d{2})\s*\)\s*(?P<summary>.*?)\s*Source:\s*(?P<url>\S+)\s*$"
)


def _parse_bullets(text: str) -> list[Bullet]:
    bullets: list[Bullet] = []
    for line in (text or "").splitlines():
        line = line.rstrip()
        if not line.strip().startswith("-"):
            continue
        if "No significant updates found" in line:
            bullets.append(
                Bullet(
                    category="Other",
                    region="Unknown",
                    date="",
                    summary="No significant updates found in the last 7 days.",
                    source_url="",
                    raw=line.strip(),
                )
            )
            continue

        m = _BULLET_RE.match(line)
        if not m:
            bullets.append(
                Bullet(
                    category="Other",
                    region="Unknown",
                    date="",
                    summary=line.strip().lstrip("-").strip(),
                    source_url="",
                    raw=line.strip(),
                )
            )
            continue

        bullets.append(
            Bullet(
                category=m.group("cat").strip(),
                region=m.group("region").strip(),
                date=m.group("date").strip(),
                summary=m.group("summary").strip().rstrip("."),
                source_url=m.group("url").strip().rstrip(").,"),
                raw=line.strip(),
            )
        )
    return bullets


def call_perplexity(partner: str, *, api_key: str, model: str, temperature: float, max_tokens: int, recency: str) -> PartnerBrief:
    prompt = build_prompt(partner)
    payload: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
        "search_recency_filter": recency,
    }
    data = _http_post_json(
        PPLX_API_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        payload=payload,
        timeout_s=90,
    )
    content = (
        (((data.get("choices") or [{}])[0] or {}).get("message") or {}).get("content")  # type: ignore[union-attr]
        or ""
    )
    citations = data.get("citations") or []
    if not isinstance(citations, list):
        citations = []

    return PartnerBrief(
        partner=partner,
        bullets=_parse_bullets(content),
        raw_text=content,
        citations=[str(x) for x in citations if str(x).strip()],
    )


def _esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def render_html(briefs: list[PartnerBrief], *, title: str) -> str:
    now = _utc_now()
    period_end = now.date().isoformat()
    period_start = (now.date() - dt.timedelta(days=7)).isoformat()

    sections: list[str] = []
    for b in briefs:
        lis: list[str] = []
        for blt in b.bullets:
            meta = []
            if blt.category:
                meta.append(_esc(blt.category))
            if blt.region:
                meta.append(_esc(blt.region))
            if blt.date:
                meta.append(_esc(blt.date))
            meta_s = " · ".join(meta)
            if blt.source_url:
                li = (
                    f"<li style=\"margin:6px 0;\">"
                    f"<div style=\"color:#111827;\">{_esc(blt.summary)}</div>"
                    f"<div style=\"color:#6b7280;font-size:12px;\">{meta_s} · "
                    f"<a href=\"{_esc(blt.source_url)}\" target=\"_blank\" rel=\"noopener noreferrer\">source</a>"
                    f"</div>"
                    f"</li>"
                )
            else:
                li = (
                    f"<li style=\"margin:6px 0;\">"
                    f"<div style=\"color:#111827;\">{_esc(blt.summary)}</div>"
                    f"<div style=\"color:#6b7280;font-size:12px;\">{meta_s}</div>"
                    f"</li>"
                )
            lis.append(li)

        citations_html = ""
        if b.citations:
            c = "".join(
                f"<li style=\"margin:4px 0;\"><a href=\"{_esc(u)}\" target=\"_blank\" rel=\"noopener noreferrer\">{_esc(u)}</a></li>"
                for u in b.citations[:10]
            )
            citations_html = (
                "<details style=\"margin-top:10px;\">"
                "<summary style=\"cursor:pointer;color:#374151;font-size:12px;\">Citations</summary>"
                f"<ul style=\"margin:8px 0 0 18px;padding:0;color:#374151;font-size:12px;\">{c}</ul>"
                "</details>"
            )

        sections.append(
            f"<section style=\"margin:18px 0;padding:14px 14px 10px;border:1px solid #e5e7eb;border-radius:12px;\">"
            f"<h2 style=\"margin:0 0 8px;font-size:16px;color:#111827;\">{_esc(b.partner)}</h2>"
            f"<ul style=\"margin:0 0 0 18px;padding:0;color:#111827;font-size:13px;line-height:1.4;\">{''.join(lis) if lis else '<li>No updates.</li>'}</ul>"
            f"{citations_html}"
            "</section>"
        )

    return f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{_esc(title)}</title>
  </head>
  <body style="margin:0;padding:0;background:#f6f7fb;font-family:Arial,Helvetica,sans-serif;">
    <div style="max-width:980px;margin:0 auto;padding:24px;">
      <div style="background:#ffffff;border:1px solid #e5e7eb;border-radius:12px;padding:20px;">
        <div style="display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;">
          <h1 style="margin:0;font-size:20px;line-height:1.2;">{_esc(title)}</h1>
          <div style="color:#6b7280;font-size:12px;">{_esc(period_start)} → {_esc(period_end)} (UTC)</div>
        </div>
        <p style="margin:12px 0 0;color:#374151;font-size:13px;line-height:1.4;">
          Generated from Perplexity Sonar (best-effort; always confirm via sources).
        </p>
        {''.join(sections) if sections else '<p style="margin:16px 0 0;color:#6b7280;">No partners configured.</p>'}
        <p style="margin:16px 0 0;color:#6b7280;font-size:11px;line-height:1.4;">
          Generated at {_esc(now.strftime('%Y-%m-%d %H:%M UTC'))}.
        </p>
      </div>
    </div>
  </body>
</html>
"""


def main(argv: list[str]) -> int:
    root = os.path.dirname(os.path.abspath(__file__))
    partners_path = os.path.join(root, "partners.json")

    dry_run = _env_opt("PPLX_DRY_RUN", "").lower() in {"1", "true", "yes", "y"}
    api_key = _env_opt("PERPLEXITY_API_KEY")
    if not api_key and not dry_run:
        raise SystemExit("Missing required env var: PERPLEXITY_API_KEY (or set PPLX_DRY_RUN=1)")
    model = _env_opt("PPLX_MODEL", "sonar")
    temperature = float(_env_opt("PPLX_TEMPERATURE", "0.2"))
    max_tokens = int(_env_opt("PPLX_MAX_TOKENS", "900"))
    recency = _env_opt("PPLX_RECENCY", "week")
    polite_sleep_s = float(_env_opt("PPLX_SLEEP_S", "0.8"))

    title = _env_opt("BRIEF_TITLE", "Executive Partner Brief (v2)")
    out_html_path = _env_opt("BRIEF_OUT", os.path.join(root, "..", "out", "executive_partner_brief_v2.html"))
    debug_json_path = _env_opt("BRIEF_DEBUG_JSON", os.path.join(root, "..", "out", "executive_partner_brief_v2.raw.json"))

    cfg = _read_json(partners_path)
    partners = cfg.get("partners") or []
    if not isinstance(partners, list):
        raise SystemExit("partners.json must contain a top-level 'partners' array")

    briefs: list[PartnerBrief] = []
    for i, partner in enumerate([str(x).strip() for x in partners if str(x).strip()]):
        if i:
            time.sleep(polite_sleep_s)
        if dry_run:
            briefs.append(
                PartnerBrief(
                    partner=partner,
                    bullets=[
                        Bullet(
                            category="MajorNews",
                            region="Global",
                            date=_utc_now().date().isoformat(),
                            summary="(dry-run) Example update placeholder to validate formatting.",
                            source_url="https://example.com",
                            raw="- [MajorNews] (Global, YYYY-MM-DD) ...",
                        )
                    ],
                    raw_text="(dry-run) No Perplexity call made.",
                    citations=[],
                )
            )
        else:
            briefs.append(
                call_perplexity(
                    partner,
                    api_key=api_key,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    recency=recency,
                )
            )

    html_doc = render_html(briefs, title=title)
    os.makedirs(os.path.dirname(out_html_path), exist_ok=True)
    with open(out_html_path, "w", encoding="utf-8") as f:
        f.write(html_doc)

    os.makedirs(os.path.dirname(debug_json_path), exist_ok=True)
    with open(debug_json_path, "w", encoding="utf-8") as f:
        json.dump(
            [
                {
                    "partner": b.partner,
                    "bullets": [
                        {
                            "category": bl.category,
                            "region": bl.region,
                            "date": bl.date,
                            "summary": bl.summary,
                            "source_url": bl.source_url,
                            "raw": bl.raw,
                        }
                        for bl in b.bullets
                    ],
                    "citations": b.citations,
                    "raw_text": b.raw_text,
                }
                for b in briefs
            ],
            f,
            indent=2,
        )

    print(out_html_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

