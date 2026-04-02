#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.parse
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
        "Geography: Europe, North America, Latin America (LATAM), APJ (Asia Pacific Japan)\n"
        "Scope: partner-focused business updates (see categories)\n\n"
        "Return EXACTLY this format:\n"
        f"Partner: {partner}\n"
        "- [Category] (Region, YYYY-MM-DD) Bullet summary. Source: <url>\n"
        "- ... (3–8 bullets max)\n\n"
        "Category must be one of:\n"
        "- NewJoiners (ONLY for Account Executives and CSMs/CSPs)\n"
        "- Acquisitions\n"
        "- ProductNews\n"
        "- FinancialNews\n"
        "- StrategyUpdates\n"
        "- PartnershipUpdates\n"
        "- NewClientWins\n"
        "- Other\n\n"
        "Region must be one of: Europe | North America | LATAM | APJ | Global | Unknown\n\n"
        "ProductNews focus areas (call out explicitly when relevant): career site features, career site builder, screening, scheduling, employee referrals, candidate relationship management (CRM).\n\n"
        "Rules:\n"
        "- Only include items clearly within the last 7 days; if unsure, exclude.\n"
        "- Prefer primary sources (company newsroom/blog) or credible outlets.\n"
        "- If you mention a new hire, ONLY include if the role is Account Executive or CSM/CSP.\n"
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


def _extract_domain(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return ""


def render_html(briefs: list[PartnerBrief], *, title: str) -> str:
    now = _utc_now()
    period_end = now.date().isoformat()
    period_start = (now.date() - dt.timedelta(days=7)).isoformat()

    briefs_sorted = sorted(briefs, key=lambda x: x.partner.lower())

    # Top highlights = first 3 bullets per partner (excluding "No significant updates..." filler)
    top_blocks: list[str] = []
    for b in briefs_sorted:
        bullets = [bl for bl in b.bullets if "No significant updates found" not in (bl.summary or "")]
        top = bullets[:3]
        if not top:
            top_blocks.append(
                f"<div class=\"highlight-card\">"
                f"<div class=\"partner\">{_esc(b.partner)}</div>"
                f"<div class=\"muted\">No significant updates found in the last 7 days.</div>"
                f"</div>"
            )
            continue

        lis = "".join(
            (
                "<li>"
                f"<span class=\"pill\">{_esc(bl.category or 'Other')}</span>"
                f"<span class=\"pill pill-quiet\">{_esc(bl.region or 'Unknown')}</span>"
                f"<span class=\"pill pill-quiet\">{_esc(bl.date or '')}</span>"
                + (
                    f"<a class=\"item\" href=\"{_esc(bl.source_url)}\" target=\"_blank\" rel=\"noopener noreferrer\">{_esc(bl.summary)}</a>"
                    if bl.source_url
                    else f"<span class=\"item\">{_esc(bl.summary)}</span>"
                )
                + "</li>"
            )
            for bl in top
        )
        top_blocks.append(
            f"<div class=\"highlight-card\">"
            f"<div class=\"partner\">{_esc(b.partner)}</div>"
            f"<ul class=\"highlights\">{lis}</ul>"
            f"</div>"
        )

    # Full table rows
    rows: list[str] = []
    for b in briefs_sorted:
        if not b.bullets:
            rows.append(
                "<tr>"
                f"<td>{_esc(b.partner)}</td>"
                "<td colspan=\"4\" class=\"muted\">No updates.</td>"
                "</tr>"
            )
            continue

        for bl in b.bullets:
            if "No significant updates found" in (bl.summary or "") and not bl.source_url:
                rows.append(
                    "<tr>"
                    f"<td>{_esc(b.partner)}</td>"
                    f"<td>{_esc(bl.category or 'Other')}</td>"
                    f"<td>{_esc(bl.region or 'Unknown')}</td>"
                    f"<td>{_esc(bl.date or '')}</td>"
                    f"<td colspan=\"2\" class=\"muted\">{_esc(bl.summary)}</td>"
                    "</tr>"
                )
                continue

            item_html = (
                f"<a href=\"{_esc(bl.source_url)}\" target=\"_blank\" rel=\"noopener noreferrer\">{_esc(bl.summary)}</a>"
                if bl.source_url
                else _esc(bl.summary)
            )
            source_html = _esc(_extract_domain(bl.source_url)) if bl.source_url else ""
            rows.append(
                "<tr>"
                f"<td>{_esc(b.partner)}</td>"
                f"<td>{_esc(bl.category or 'Other')}</td>"
                f"<td>{_esc(bl.region or 'Unknown')}</td>"
                f"<td class=\"mono\">{_esc(bl.date or '')}</td>"
                f"<td>{item_html}</td>"
                f"<td class=\"muted\">{source_html}</td>"
                "</tr>"
            )

    rows_html = "\n".join(rows) if rows else "<tr><td colspan=\"6\" class=\"muted\">No partners configured.</td></tr>"

    # Citations per partner (only if partner has real updates)
    citations_sections: list[str] = []
    for b in briefs_sorted:
        has_real_updates = any(
            (bl.source_url or "").strip() and "No significant updates found" not in (bl.summary or "")
            for bl in (b.bullets or [])
        )
        if not b.citations or not has_real_updates:
            continue
        c = "".join(
            f"<li><a href=\"{_esc(u)}\" target=\"_blank\" rel=\"noopener noreferrer\">{_esc(u)}</a></li>"
            for u in b.citations[:15]
        )
        citations_sections.append(
            "<details class=\"citations\">"
            f"<summary><span class=\"partner\">{_esc(b.partner)}</span> citations</summary>"
            f"<ul>{c}</ul>"
            "</details>"
        )

    return f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{_esc(title)}</title>
    <style>
      :root {{
        --bg: #f6f7fb;
        --card: #ffffff;
        --border: #e5e7eb;
        --text: #111827;
        --muted: #6b7280;
        --muted2: #374151;
        --pill: #eef2ff;
        --pillText: #3730a3;
      }}
      * {{ box-sizing: border-box; }}
      body {{ margin: 0; padding: 0; background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, Helvetica, sans-serif; }}
      a {{ color: #111827; text-decoration: underline; text-underline-offset: 2px; }}
      a:hover {{ color: #1f2937; }}
      .wrap {{ max-width: 1080px; margin: 0 auto; padding: 24px; }}
      .panel {{ background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 20px; }}
      .header {{ display:flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }}
      .title {{ margin:0; font-size: 20px; line-height: 1.2; }}
      .range {{ font-size: 12px; color: var(--muted); }}
      .sub {{ margin: 10px 0 0; font-size: 13px; line-height: 1.5; color: var(--muted2); }}
      .section-title {{ margin: 18px 0 10px; font-size: 15px; }}
      .grid {{ display:grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }}
      @media (max-width: 900px) {{ .grid {{ grid-template-columns: 1fr; }} }}
      .highlight-card {{ border:1px solid var(--border); border-radius: 12px; padding: 12px; background:#fff; }}
      .partner {{ font-weight: 700; }}
      .muted {{ color: var(--muted); }}
      .highlights {{ margin: 10px 0 0 18px; padding: 0; font-size: 13px; line-height: 1.45; }}
      .highlights li {{ margin: 6px 0; }}
      .pill {{ display:inline-block; padding: 2px 8px; border-radius: 999px; background: var(--pill); color: var(--pillText); font-size: 11px; margin-right: 6px; vertical-align: 1px; }}
      .pill-quiet {{ background: #f3f4f6; color: #374151; }}
      .item {{ margin-left: 2px; }}
      .table-wrap {{ margin-top: 10px; overflow:auto; border: 1px solid var(--border); border-radius: 12px; }}
      table {{ border-collapse: collapse; width: 100%; min-width: 980px; font-size: 12px; }}
      thead th {{ position: sticky; top: 0; background: #f3f4f6; color: #111827; text-align:left; padding: 10px; border-bottom: 1px solid var(--border); }}
      tbody td {{ padding: 10px; border-bottom: 1px solid #f1f5f9; vertical-align: top; }}
      tbody tr:hover td {{ background: #fafafa; }}
      .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; white-space: nowrap; }}
      details.citations {{ margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--border); }}
      details.citations summary {{ cursor: pointer; color: var(--muted2); font-size: 12px; }}
      details.citations ul {{ margin: 8px 0 0 18px; padding: 0; font-size: 12px; color: var(--muted2); }}
      .footer {{ margin-top: 14px; font-size: 11px; color: var(--muted); }}
    </style>
  </head>
  <body>
    <div class="wrap">
      <div class="panel">
        <div class="header">
          <h1 class="title">{_esc(title)}</h1>
          <div class="range">{_esc(period_start)} → {_esc(period_end)} (UTC)</div>
        </div>
        <p class="sub">Weekly partner intelligence generated from Perplexity Sonar (best-effort; always confirm via sources).</p>

        <div class="section-title">Top highlights (per partner)</div>
        <div class="grid">
          {''.join(top_blocks)}
        </div>

        <div class="section-title">All items</div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th style="width: 180px;">Partner</th>
                <th style="width: 140px;">Category</th>
                <th style="width: 90px;">Region</th>
                <th style="width: 110px;">Date</th>
                <th>Item</th>
                <th style="width: 220px;">Source</th>
              </tr>
            </thead>
            <tbody>
              {rows_html}
            </tbody>
          </table>
        </div>

        {''.join(citations_sections) if citations_sections else ''}

        <div class="footer">Generated at {_esc(now.strftime('%Y-%m-%d %H:%M UTC'))}.</div>
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

    title = _env_opt("BRIEF_TITLE", "Executive Partner Brief")
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

