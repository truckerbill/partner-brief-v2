#!/usr/bin/env python3
"""
Executive Partner Brief – v3
Generates a weekly HTML partnership intelligence email.

New in v3 vs v2:
- Partner vs Competitor column + visual separation
- Three dedicated prompt modes: standard | issues | golive
- New categories: GoLive, ClientIssue, NewJoiners (AE/CSM only for partner set)
- Issues section pulls Reddit / user-group / forum signals and tries to surface the company behind each issue
- GoLive section scans LinkedIn / user groups for go-live announcements
- NewJoiners restricted to AE / CSM roles, partners-only (SAP SuccessFactors, SmartRecruiters, Workday, iCIMS)
- Email-optimised HTML: one-file, inline-friendly, table-based skeleton + CSS for modern clients
- Competitor column tag: colour-coded differently from partner rows
"""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import sys
import time
import hashlib
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PPLX_API_URL = (
    os.environ.get("PPLX_API_URL", "https://api.perplexity.ai/v1/sonar").strip()
    or "https://api.perplexity.ai/v1/sonar"
)

# Partners where we also want NewJoiners (AE / CSM only)
JOINER_PARTNERS = {"sap successfactors", "smartrecruiters", "workday", "icims"}

# Which entities are competitors (vs partners)
COMPETITOR_SET = {"avature", "paradox", "phenom", "eightfold"}

ALL_CATEGORIES = [
    "NewClientWins",
    "GoLive",
    "ClientIssue",
    "NewJoiners",
    "ProductNews",
    "Acquisitions",
    "FinancialNews",
    "StrategyUpdates",
    "PartnershipUpdates",
    "Other",
]

REGIONS = ["Europe", "North America", "LATAM", "APJ", "Global", "Unknown"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _env_opt(name: str, default: str = "") -> str:
    return (os.environ.get(name, default) or "").strip()


def _http_post_json(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_s: int = 90,
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "ExecutivePartnerBrief/3.0 (+perplexity)",
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


# ---------------------------------------------------------------------------
# Prompt builders  (3 separate calls per partner for sharper signal)
# ---------------------------------------------------------------------------


def _base_format(partner: str) -> str:
    return (
        f"Return EXACTLY this bullet format (no extra text, no markdown headers):\n"
        f"Partner: {partner}\n"
        f"- [Category] (Region, YYYY-MM-DD) Bullet summary. Source: <url>\n\n"
        f"Category must be one of: {' | '.join(ALL_CATEGORIES)}\n"
        f"Region must be one of: {' | '.join(REGIONS)}\n"
        f"If nothing found, return:\n"
        f"Partner: {partner}\n"
        f"- No significant updates found in the last 7 days.\n"
    )


def build_prompt_standard(partner: str) -> str:
    """General intel: wins, product, financial, strategy, partnerships, acquisitions."""
    return (
        "You are preparing an executive partner intelligence brief.\n\n"
        f"Partner: {partner}\n"
        "Time window: last 7 days only. Exclude anything older.\n"
        "Audience: partnership manager at a recruiting-tech company.\n\n"
        "Find and return 3–6 of the most relevant items across these areas (ordered by importance):\n"
        "1. NewClientWins – press releases, case studies, reputable coverage, official LinkedIn posts announcing a new customer.\n"
        "2. ProductNews – new features/releases especially in: career site, career site builder, screening, scheduling, employee referrals, candidate relationship management (CRM), hiring events, programmatic adtech.\n"
        "3. Acquisitions – any M&A activity.\n"
        "4. FinancialNews – funding rounds, earnings, valuation changes.\n"
        "5. StrategyUpdates – GTM shifts, pricing/packaging changes, major executive hires (C-suite / VP+).\n"
        "6. PartnershipUpdates – new technology integrations, partner ecosystem moves, HRIS/ATS connector announcements.\n\n"
        "Source preference: company newsroom/blog > credible tech/business press > official LinkedIn company page.\n"
        "Exclude anything you cannot attribute to a dated, verifiable source within the last 7 days.\n\n"
        + _base_format(partner)
    )


def build_prompt_issues(partner: str) -> str:
    """Client-issue / complaint signals from Reddit, user groups, forums."""
    return (
        "You are a support-intelligence analyst scanning public forums for product issues.\n\n"
        f"Partner / vendor: {partner}\n"
        "Time window: last 7 days only.\n"
        "Audience: partnership manager who needs to know if clients are experiencing problems.\n\n"
        "Search Reddit (r/recruiting, r/humanresources, r/HRIS, r/ATS, r/WorkdayCommunity, etc.), "
        "vendor community forums, G2/Capterra reviews posted in the last 7 days, Trustpilot, and "
        "any public user groups for complaints, outages, or reported bugs related to this vendor.\n\n"
        "Focus areas (flag explicitly when relevant):\n"
        "- Career site / career site builder outages or bugs\n"
        "- Candidate Relationship Management (CRM) issues\n"
        "- Screening & scheduling failures\n"
        "- Hiring events platform problems\n"
        "- Programmatic adtech / job distribution errors\n"
        "- Employee referral module bugs\n"
        "- API / integration breakages affecting partner ecosystems\n\n"
        "For each issue: try to identify the affected client/company from context clues (username, post text, job title). "
        "If identifiable, include it in the summary as '(Client: CompanyName)'. If not, write '(Client: unknown)'.\n"
        "Severity hint: note if the thread has many upvotes/replies (indicates widespread impact).\n\n"
        "Use category ClientIssue for all items. Include 1–5 items maximum.\n\n"
        + _base_format(partner)
    )


def build_prompt_golive(partner: str) -> str:
    """GoLive announcements from LinkedIn / user communities."""
    return (
        "You are scanning LinkedIn and HR tech user communities for go-live announcements.\n\n"
        f"Vendor platform: {partner}\n"
        "Time window: last 7 days only.\n"
        "Audience: partnership manager tracking adoption momentum.\n\n"
        "Search LinkedIn (public posts), vendor user groups (e.g. Workday Community, SAP SuccessFactors Community, "
        "SmartRecruiters Community, iCIMS Connect), and HR tech forums for posts where a company announces "
        "they have gone live / launched / implemented this vendor's platform.\n\n"
        "Signals to look for:\n"
        "- 'We just went live on [vendor]'\n"
        "- 'Excited to announce our [vendor] implementation is complete'\n"
        "- '#GoLive' or '#Implementation' tags mentioning the vendor\n"
        "- User group posts sharing go-live milestones\n\n"
        "For each GoLive: include the company that went live, what module/product if mentioned, and the region.\n"
        "Use category GoLive for all items. Include 1–4 items maximum.\n\n"
        + _base_format(partner)
    )


def build_prompt_joiners(partner: str) -> str:
    """New AE / CSM hires – partners only."""
    return (
        "You are scanning LinkedIn and company newsrooms for new hires.\n\n"
        f"Company: {partner}\n"
        "Time window: last 7 days only.\n"
        "Audience: partnership manager tracking competitive sales/CS capacity.\n\n"
        "Find LinkedIn posts or announcements where someone announces they have just joined this company "
        "in a role that is EXACTLY one of: Account Executive (AE), Enterprise AE, Strategic AE, "
        "Customer Success Manager (CSM), Customer Success Partner (CSP), or equivalent frontline sales/CS role.\n\n"
        "EXCLUDE: directors, VPs, C-suite, recruiters, engineers, marketing, enablement, and any role "
        "that is not a quota-carrying AE or a CSM/CSP.\n\n"
        "For each hire: include their name (if public), exact role title, and region/territory if mentioned.\n"
        "Use category NewJoiners for all items. Include 1–5 items maximum.\n\n"
        + _base_format(partner)
    )


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Bullet:
    category: str
    region: str
    date: str
    summary: str
    source_url: str
    raw: str


@dataclass
class PartnerBrief:
    partner: str
    is_competitor: bool
    bullets: list[Bullet] = field(default_factory=list)
    raw_text: str = ""
    citations: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_BULLET_RE = re.compile(
    r"^\s*-\s*\[(?P<cat>[^\]]+)\]\s*\(\s*(?P<region>[^,\)]+)\s*,\s*(?P<date>\d{4}-\d{2}-\d{2})\s*\)\s*(?P<summary>.*?)\s*Source:\s*(?P<url>\S+)\s*$"
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


# ---------------------------------------------------------------------------
# Perplexity calls
# ---------------------------------------------------------------------------


def _call_pplx(
    prompt: str,
    *,
    api_key: str,
    model: str,
    temperature: float,
    max_tokens: int,
    recency: str,
) -> tuple[str, list[str]]:
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
    )
    content = (
        (((data.get("choices") or [{}])[0] or {}).get("message") or {}).get("content")
        or ""
    )
    citations = [str(x) for x in (data.get("citations") or []) if str(x).strip()]
    return content, citations


def call_all_prompts(
    partner: str,
    *,
    api_key: str,
    model: str,
    temperature: float,
    max_tokens: int,
    recency: str,
    sleep_s: float,
    include_joiners: bool,
) -> PartnerBrief:
    is_comp = partner.lower() in COMPETITOR_SET
    brief = PartnerBrief(partner=partner, is_competitor=is_comp)

    prompts = [
        ("standard", build_prompt_standard(partner)),
        ("issues", build_prompt_issues(partner)),
        ("golive", build_prompt_golive(partner)),
    ]
    if include_joiners and not is_comp:
        prompts.append(("joiners", build_prompt_joiners(partner)))

    for i, (label, prompt) in enumerate(prompts):
        if i:
            time.sleep(sleep_s)
        content, citations = _call_pplx(
            prompt,
            api_key=api_key,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            recency=recency,
        )
        brief.bullets.extend(_parse_bullets(content))
        brief.citations.extend(c for c in citations if c not in brief.citations)
        brief.raw_text += f"\n\n--- {label} ---\n{content}"

    return brief


# ---------------------------------------------------------------------------
# Category helpers
# ---------------------------------------------------------------------------

_CAT_NORM_MAP = {
    "newjoiners": "NewJoiners",
    "newjoiner": "NewJoiners",
    "leadershipchange": "NewJoiners",
    "acquisitions": "Acquisitions",
    "acquisition": "Acquisitions",
    "productnews": "ProductNews",
    "productlaunch": "ProductNews",
    "financialnews": "FinancialNews",
    "strategyupdates": "StrategyUpdates",
    "majornews": "StrategyUpdates",
    "partnershipupdates": "PartnershipUpdates",
    "partnershipupdate": "PartnershipUpdates",
    "newclientwins": "NewClientWins",
    "clientwin": "NewClientWins",
    "golive": "GoLive",
    "go-live": "GoLive",
    "clientissue": "ClientIssue",
    "clientproblem": "ClientIssue",
    "other": "Other",
}

_CAT_CSS = {
    "NewClientWins": ("cat-wins", "🏆"),
    "GoLive": ("cat-golive", "🚀"),
    "ClientIssue": ("cat-issue", "⚠️"),
    "NewJoiners": ("cat-newjoiners", "👤"),
    "ProductNews": ("cat-product", "🔧"),
    "Acquisitions": ("cat-acquisitions", "🤝"),
    "FinancialNews": ("cat-financial", "💰"),
    "StrategyUpdates": ("cat-strategy", "♟️"),
    "PartnershipUpdates": ("cat-partnership", "🔗"),
    "Other": ("cat-other", "•"),
}

# Priority order for sorting bullets within a partner block
_CAT_PRIORITY = {c: i for i, c in enumerate(ALL_CATEGORIES)}


def _norm_cat(cat: str) -> str:
    low = (cat or "").strip().lower().replace(" ", "").replace("-", "")
    return _CAT_NORM_MAP.get(low, cat.strip() if cat.strip() in _CAT_CSS else "Other")


def _cat_css(cat: str) -> tuple[str, str]:
    return _CAT_CSS.get(cat, ("cat-other", "•"))


def _esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def _domain(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


_NO_NEWS = [
    "Quiet week — either everything's stable or the big news is next week.",
    "Nothing surfaced — the ecosystem is practicing mindfulness.",
    "No significant moves detected this week.",
    "All quiet on the western front.",
]


def _no_news_msg(partner: str) -> str:
    h = hashlib.sha256((partner or "").encode()).hexdigest()
    return _NO_NEWS[int(h[:8], 16) % len(_NO_NEWS)]


# ---------------------------------------------------------------------------
# HTML renderer  (email-optimised)
# ---------------------------------------------------------------------------

# Category sort key: most actionable first
def _bullet_sort_key(bl: Bullet) -> int:
    return _CAT_PRIORITY.get(_norm_cat(bl.category), 99)


def render_html(briefs: list[PartnerBrief], *, title: str) -> str:  # noqa: C901
    now = _utc_now()
    period_end = now.date().isoformat()
    period_start = (now.date() - dt.timedelta(days=7)).isoformat()

    preferred_order = [
        "sap successfactors",
        "smartrecruiters",
        "workday",
        "icims",
        "avature",
        "paradox",
        "phenom",
        "eightfold",
    ]
    rank = {n: i for i, n in enumerate(preferred_order)}
    briefs_sorted = sorted(
        briefs,
        key=lambda b: (rank.get(b.partner.lower(), 999), b.is_competitor, b.partner.lower()),
    )

    # ---- summary bar counts ----
    total_wins = sum(
        1 for b in briefs for bl in b.bullets if _norm_cat(bl.category) == "NewClientWins"
    )
    total_issues = sum(
        1 for b in briefs for bl in b.bullets if _norm_cat(bl.category) == "ClientIssue"
    )
    total_golive = sum(
        1 for b in briefs for bl in b.bullets if _norm_cat(bl.category) == "GoLive"
    )
    total_joiners = sum(
        1 for b in briefs for bl in b.bullets if _norm_cat(bl.category) == "NewJoiners"
    )

    def _pill(cat: str, text: str = "") -> str:
        css, icon = _cat_css(cat)
        label = text or cat
        return f'<span class="pill {css}">{icon} {_esc(label)}</span>'

    def _row_type_badge(is_competitor: bool) -> str:
        if is_competitor:
            return '<span class="pill type-competitor">Competitor</span>'
        return '<span class="pill type-partner">Partner</span>'

    def _bullet_html(bl: Bullet, show_partner: bool = False, partner: str = "") -> str:
        cat = _norm_cat(bl.category)
        css, icon = _cat_css(cat)
        if "No significant updates found" in (bl.summary or ""):
            return ""

        item = (
            f'<a href="{_esc(bl.source_url)}" target="_blank" rel="noopener noreferrer">{_esc(bl.summary)}</a>'
            if bl.source_url
            else _esc(bl.summary)
        )
        src_domain = _domain(bl.source_url)
        return (
            f'<tr class="brow">'
            + (f'<td class="td-partner">{_esc(partner)}</td>' if show_partner else "")
            + f'<td><span class="pill {css}">{icon} {_esc(cat)}</span></td>'
            f'<td class="td-region"><span class="region-tag">{_esc(bl.region or "Unknown")}</span></td>'
            f'<td class="td-date mono">{_esc(bl.date or "")}</td>'
            f'<td class="td-item">{item}</td>'
            f'<td class="td-src muted">{_esc(src_domain)}</td>'
            f"</tr>"
        )

    # ---- partner card (highlight section) ----
    partner_cards: list[str] = []
    competitor_cards: list[str] = []

    for b in briefs_sorted:
        real_bullets = sorted(
            [bl for bl in b.bullets if "No significant updates found" not in (bl.summary or "")],
            key=_bullet_sort_key,
        )
        badge = _row_type_badge(b.is_competitor)

        if not real_bullets:
            card = (
                f'<div class="card {"card-competitor" if b.is_competitor else ""}">'
                f'<div class="card-header"><span class="card-name">{_esc(b.partner)}</span>{badge}</div>'
                f'<p class="muted no-news">{_esc(_no_news_msg(b.partner))}</p>'
                f"</div>"
            )
        else:
            top = real_bullets[:5]
            lis = "".join(
                f'<li>'
                f'{_pill(_norm_cat(bl.category))}'
                f'<span class="region-tag">{_esc(bl.region or "Unknown")}</span>'
                + (
                    f' <a href="{_esc(bl.source_url)}" target="_blank" rel="noopener noreferrer">{_esc(bl.summary)}</a>'
                    if bl.source_url
                    else f" {_esc(bl.summary)}"
                )
                + f"</li>"
                for bl in top
            )
            card = (
                f'<div class="card {"card-competitor" if b.is_competitor else ""}">'
                f'<div class="card-header"><span class="card-name">{_esc(b.partner)}</span>{badge}</div>'
                f"<ul class='card-list'>{lis}</ul>"
                f"</div>"
            )

        if b.is_competitor:
            competitor_cards.append(card)
        else:
            partner_cards.append(card)

    # ---- full detail table ----
    all_rows: list[str] = []
    prev_partner = None
    for b in briefs_sorted:
        real_bullets = sorted(
            [bl for bl in b.bullets if "No significant updates found" not in (bl.summary or "")],
            key=_bullet_sort_key,
        )
        type_badge = _row_type_badge(b.is_competitor)
        partner_cell = f'{_esc(b.partner)}&nbsp;{type_badge}'

        if not real_bullets:
            all_rows.append(
                f'<tr class="brow {"brow-competitor" if b.is_competitor else ""}">'
                f"<td>{partner_cell}</td>"
                f'<td colspan="5" class="muted">{_esc(_no_news_msg(b.partner))}</td>'
                f"</tr>"
            )
            continue

        for i, bl in enumerate(real_bullets):
            cat = _norm_cat(bl.category)
            css, icon = _cat_css(cat)
            item = (
                f'<a href="{_esc(bl.source_url)}" target="_blank" rel="noopener noreferrer">{_esc(bl.summary)}</a>'
                if bl.source_url
                else _esc(bl.summary)
            )
            src = _domain(bl.source_url)
            row_class = f'brow {"brow-competitor" if b.is_competitor else ""}'
            p_cell = partner_cell if i == 0 else ""
            all_rows.append(
                f'<tr class="{row_class}">'
                f"<td>{p_cell}</td>"
                f'<td><span class="pill {css}">{icon} {_esc(cat)}</span></td>'
                f'<td><span class="region-tag">{_esc(bl.region or "Unknown")}</span></td>'
                f'<td class="mono">{_esc(bl.date or "")}</td>'
                f"<td>{item}</td>"
                f'<td class="muted">{_esc(src)}</td>'
                f"</tr>"
            )

    rows_html = "\n".join(all_rows) or "<tr><td colspan='6' class='muted'>No data.</td></tr>"

    # ---- citations ----
    cite_html = ""
    for b in briefs_sorted:
        if not b.citations:
            continue
        c = "".join(
            f'<li><a href="{_esc(u)}" target="_blank" rel="noopener noreferrer">{_esc(u)}</a></li>'
            for u in b.citations[:12]
        )
        cite_html += (
            f"<details class='citations'>"
            f"<summary>{_esc(b.partner)} citations</summary>"
            f"<ul>{c}</ul>"
            f"</details>"
        )

    partner_grid = "".join(partner_cards)
    competitor_grid = "".join(competitor_cards)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{_esc(title)}</title>
<style>
:root{{
  --bg:#f0f2f7;
  --card:#ffffff;
  --border:#e2e6ef;
  --text:#0f172a;
  --muted:#64748b;
  --accent:#2563eb;
  --accent2:#7c3aed;
  --comp-bg:#fdf4ff;
  --comp-border:#e9d5ff;
  --header-bg:#1e293b;
  --header-text:#f8fafc;
}}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{background:var(--bg);color:var(--text);font-family:"Georgia",serif;font-size:14px;line-height:1.55;}}
a{{color:var(--accent);text-decoration:none;}}
a:hover{{text-decoration:underline;}}
.wrap{{max-width:1100px;margin:0 auto;padding:28px 20px;}}

/* ---- header ---- */
.header{{background:var(--header-bg);color:var(--header-text);border-radius:16px 16px 0 0;padding:28px 32px 22px;}}
.header h1{{font-size:22px;font-weight:700;letter-spacing:-.3px;}}
.header .range{{font-size:12px;color:#94a3b8;margin-top:4px;font-family:monospace;}}
.header .sub{{font-size:12px;color:#94a3b8;margin-top:8px;font-style:italic;}}

/* ---- summary bar ---- */
.summary-bar{{background:#1e3a5f;display:flex;gap:0;border-top:1px solid #334155;}}
.stat{{flex:1;text-align:center;padding:14px 8px;border-right:1px solid #334155;}}
.stat:last-child{{border-right:none;}}
.stat .n{{font-size:28px;font-weight:800;color:#fff;line-height:1;}}
.stat .lbl{{font-size:10px;color:#94a3b8;text-transform:uppercase;letter-spacing:.5px;margin-top:3px;}}

/* ---- main panel ---- */
.panel{{background:var(--card);border:1px solid var(--border);border-radius:0 0 16px 16px;padding:28px 32px;}}

/* ---- section titles ---- */
.section-title{{font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:var(--muted);margin:28px 0 14px;border-bottom:2px solid var(--border);padding-bottom:6px;}}
.section-title:first-child{{margin-top:0;}}

/* ---- cards grid ---- */
.grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:14px;}}
@media(max-width:760px){{.grid{{grid-template-columns:1fr;}}}}
.card{{border:1px solid var(--border);border-radius:12px;padding:16px;background:#fff;}}
.card-competitor{{background:var(--comp-bg);border-color:var(--comp-border);}}
.card-header{{display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap;}}
.card-name{{font-weight:700;font-size:15px;}}
.card-list{{margin:0 0 0 4px;padding:0;list-style:none;font-size:13px;}}
.card-list li{{padding:4px 0;border-bottom:1px solid #f1f5f9;display:flex;align-items:flex-start;gap:6px;flex-wrap:wrap;}}
.card-list li:last-child{{border-bottom:none;}}
.no-news{{font-size:13px;color:var(--muted);font-style:italic;}}

/* ---- pills ---- */
.pill{{display:inline-block;padding:2px 7px;border-radius:999px;font-size:10.5px;white-space:nowrap;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;vertical-align:1px;}}
.type-partner{{background:#dbeafe;color:#1e40af;font-weight:700;}}
.type-competitor{{background:#f3e8ff;color:#6b21a8;font-weight:700;}}
.cat-wins{{background:#fef9c3;color:#854d0e;}}
.cat-golive{{background:#dcfce7;color:#14532d;}}
.cat-issue{{background:#fee2e2;color:#991b1b;}}
.cat-newjoiners{{background:#cffafe;color:#155e75;}}
.cat-product{{background:#e0e7ff;color:#3730a3;}}
.cat-acquisitions{{background:#fff7ed;color:#9a3412;}}
.cat-financial{{background:#d1fae5;color:#064e3b;}}
.cat-strategy{{background:#fce7f3;color:#9d174d;}}
.cat-partnership{{background:#ede9fe;color:#5b21b6;}}
.cat-other{{background:#f3f4f6;color:#374151;}}
.region-tag{{display:inline-block;font-size:10px;padding:1px 6px;border-radius:4px;background:#f1f5f9;color:#475569;white-space:nowrap;font-family:monospace;}}

/* ---- detail table ---- */
.table-wrap{{overflow-x:auto;border:1px solid var(--border);border-radius:10px;margin-top:10px;}}
table{{border-collapse:collapse;width:100%;min-width:860px;font-size:12.5px;}}
thead th{{background:#f8fafc;color:#334155;text-align:left;padding:9px 12px;border-bottom:2px solid var(--border);font-size:11px;text-transform:uppercase;letter-spacing:.4px;position:sticky;top:0;}}
.brow td{{padding:9px 12px;border-bottom:1px solid #f1f5f9;vertical-align:top;}}
.brow:hover td{{background:#f8fafc;}}
.brow-competitor td{{background:#fdf4ff;}}
.brow-competitor:hover td{{background:#f5e8ff;}}
.muted{{color:var(--muted);font-size:12px;}}
.mono{{font-family:"Courier New",Courier,monospace;white-space:nowrap;font-size:11.5px;}}

/* ---- citations ---- */
details.citations{{margin-top:10px;font-size:12px;color:var(--muted);}}
details.citations summary{{cursor:pointer;padding:4px 0;}}
details.citations ul{{margin:6px 0 0 18px;}}

/* ---- footer ---- */
.footer{{margin-top:18px;font-size:11px;color:var(--muted);text-align:center;}}
.divider{{border:none;border-top:1px solid var(--border);margin:24px 0;}}
</style>
</head>
<body>
<div class="wrap">

  <!-- HEADER -->
  <div class="header">
    <h1>{_esc(title)}</h1>
    <div class="range">{_esc(period_start)} → {_esc(period_end)} (UTC)</div>
    <div class="sub">Weekly partner & competitive intelligence · Perplexity Sonar · Verify before acting on any item.</div>
  </div>

  <!-- SUMMARY BAR -->
  <div class="summary-bar">
    <div class="stat"><div class="n">{total_wins}</div><div class="lbl">🏆 Client Wins</div></div>
    <div class="stat"><div class="n">{total_golive}</div><div class="lbl">🚀 Go-Lives</div></div>
    <div class="stat"><div class="n">{total_issues}</div><div class="lbl">⚠️ Client Issues</div></div>
    <div class="stat"><div class="n">{total_joiners}</div><div class="lbl">👤 New AEs/CSMs</div></div>
  </div>

  <div class="panel">

    <!-- PARTNER HIGHLIGHTS -->
    <div class="section-title">Partners – Top Highlights</div>
    <div class="grid">{partner_grid}</div>

    <hr class="divider"/>

    <!-- COMPETITOR HIGHLIGHTS -->
    <div class="section-title">Competitors – Top Highlights</div>
    <div class="grid">{competitor_grid}</div>

    <hr class="divider"/>

    <!-- FULL DETAIL TABLE -->
    <div class="section-title">All Items – Full Detail</div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th style="width:160px">Partner / Competitor</th>
            <th style="width:150px">Category</th>
            <th style="width:90px">Region</th>
            <th style="width:100px">Date</th>
            <th>Item</th>
            <th style="width:160px">Source</th>
          </tr>
        </thead>
        <tbody>
          {rows_html}
        </tbody>
      </table>
    </div>

    {('<hr class="divider"/>' + cite_html) if cite_html else ''}

    <div class="footer">
      Generated {_esc(now.strftime('%Y-%m-%d %H:%M UTC'))} · Executive Partner Brief v3
    </div>

  </div>
</div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


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
    sleep_s = float(_env_opt("PPLX_SLEEP_S", "1.2"))

    title = _env_opt("BRIEF_TITLE", "Executive Partner Brief")
    out_html = _env_opt(
        "BRIEF_OUT", os.path.join(root, "..", "out", "executive_partner_brief_v3.html")
    )
    debug_json = _env_opt(
        "BRIEF_DEBUG_JSON",
        os.path.join(root, "..", "out", "executive_partner_brief_v3.raw.json"),
    )

    cfg = _read_json(partners_path)
    partners: list[str] = [str(x).strip() for x in (cfg.get("partners") or []) if str(x).strip()]
    if not partners:
        raise SystemExit("partners.json has no entries in 'partners' array")

    briefs: list[PartnerBrief] = []
    for i, partner in enumerate(partners):
        if i:
            time.sleep(sleep_s)
        is_joiner_partner = partner.lower() in JOINER_PARTNERS

        if dry_run:
            briefs.append(
                PartnerBrief(
                    partner=partner,
                    is_competitor=partner.lower() in COMPETITOR_SET,
                    bullets=[
                        Bullet("NewClientWins", "Europe", _utc_now().date().isoformat(),
                               "(dry-run) Example client win", "https://example.com", ""),
                        Bullet("GoLive", "North America", _utc_now().date().isoformat(),
                               "(dry-run) Acme Corp went live on module X", "https://example.com", ""),
                        Bullet("ClientIssue", "Europe", _utc_now().date().isoformat(),
                               "(dry-run) Users reporting career site outage (Client: unknown)", "https://reddit.com", ""),
                    ] + ([
                        Bullet("NewJoiners", "North America", _utc_now().date().isoformat(),
                               "(dry-run) Jane Doe joined as Enterprise AE, East", "https://linkedin.com", ""),
                    ] if is_joiner_partner else []),
                    raw_text="(dry-run)",
                    citations=[],
                )
            )
        else:
            briefs.append(
                call_all_prompts(
                    partner,
                    api_key=api_key,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    recency=recency,
                    sleep_s=sleep_s,
                    include_joiners=is_joiner_partner,
                )
            )

    html_doc = render_html(briefs, title=title)
    os.makedirs(os.path.dirname(out_html), exist_ok=True)
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html_doc)

    os.makedirs(os.path.dirname(debug_json), exist_ok=True)
    with open(debug_json, "w", encoding="utf-8") as f:
        json.dump(
            [
                {
                    "partner": b.partner,
                    "is_competitor": b.is_competitor,
                    "bullets": [
                        {
                            "category": bl.category,
                            "region": bl.region,
                            "date": bl.date,
                            "summary": bl.summary,
                            "source_url": bl.source_url,
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

    print(f"Brief written to: {out_html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
