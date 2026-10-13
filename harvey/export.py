"""Prospect-list export — the list itself is the product.

Produces a CSV that drops straight into Instantly, Smartlead, or any
sequencer (email / first_name / last_name / company_name / personalization
columns), plus Harvey's own quality columns (score, email_status, signals)
so a human can eyeball what they're getting.
"""

import csv
import io
import logging
import re
from urllib.parse import quote

from harvey.state import StateManager

logger = logging.getLogger("harvey.export")

# Column order chosen so the file imports cleanly into cold-email tools
# without remapping; extra columns are simply available as custom variables.
CSV_COLUMNS = [
    "email", "first_name", "last_name", "company_name", "title",
    "website", "linkedin_url", "industry", "personalization",
    "email_status", "score", "seniority", "source", "status", "created_at",
]

DEFAULT_EMAIL_STATUSES = ["verified", "risky"]


async def collect_prospects(
    state: StateManager,
    email_statuses: list[str] | None = None,
    min_score: int = 0,
    statuses: list[str] | None = None,
    include_all: bool = False,
) -> list[dict]:
    """Fetch prospects matching the filters as export-ready dicts.

    Default view is the *deliverable* list (verified + risky emails).
    ``include_all`` disables every filter for a full dump.
    """
    import aiosqlite

    where = []
    params: list = []
    if not include_all:
        wanted = email_statuses if email_statuses is not None else DEFAULT_EMAIL_STATUSES
        if wanted:
            placeholders = ",".join("?" for _ in wanted)
            where.append(f"email != '' AND email_status IN ({placeholders})")
            params.extend(wanted)
        if min_score > 0:
            where.append("score >= ?")
            params.append(min_score)
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            where.append(f"status IN ({placeholders})")
            params.extend(statuses)

    sql = "SELECT * FROM prospects"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY score DESC, created_at DESC"

    async with aiosqlite.connect(state.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cursor:
            rows = [dict(r) for r in await cursor.fetchall()]

    out = []
    for r in rows:
        out.append({
            "email": r.get("email", ""),
            "first_name": r.get("first_name", ""),
            "last_name": r.get("last_name", ""),
            "company_name": r.get("company", ""),
            "title": r.get("title", ""),
            "website": r.get("source_url", ""),
            "linkedin_url": r.get("linkedin_url", ""),
            "industry": r.get("industry", ""),
            "personalization": r.get("personalization_notes", ""),
            "email_status": r.get("email_status", ""),
            "score": r.get("score", 0),
            "seniority": r.get("seniority", ""),
            "source": r.get("source", ""),
            "status": r.get("status", ""),
            "created_at": r.get("created_at", ""),
        })
    return out


# ── WhatsApp export ──────────────────────────────────────────────────────
# Harvey can't send WhatsApp, but it can hand you a ready-to-click list: each
# row carries a wa.me link with the message already filled in.

WHATSAPP_COLUMNS = [
    "company_name", "whatsapp_number", "number_source", "whatsapp_link",
    "website", "email", "email_status", "city", "score", "status", "message",
]

_WHATSAPP_TEMPLATE_PATH = "skills/whatsapp_message.md"
_DEFAULT_WHATSAPP_MESSAGE = (
    "Hi, I'm from GymFlow. How do you track member renewals and pending dues "
    "at {gym} right now?\n\nGymFlow sends renewal and due reminders on WhatsApp "
    "automatically (no per-message fees) and shows today's collection and "
    "outstanding dues on one screen.\n\n14-day free trial, no card needed: "
    "https://app.gymflow.sbs"
)
_INDIA_MOBILE = re.compile(r"^\+91[6-9]\d{9}$")


def load_whatsapp_template() -> str:
    """The message below the '---' line of skills/whatsapp_message.md."""
    from harvey.paths import PROJECT_ROOT

    path = PROJECT_ROOT / _WHATSAPP_TEMPLATE_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return _DEFAULT_WHATSAPP_MESSAGE
    _, sep, body = text.partition("\n---\n")
    body = body.strip() if sep else ""
    return body or _DEFAULT_WHATSAPP_MESSAGE


def _whatsapp_row(r: dict, company: dict, template: str) -> dict | None:
    number = (r.get("phone") or "").strip()
    confirmed = bool(r.get("phone_verified"))
    if not number or not (confirmed or _INDIA_MOBILE.match(number)):
        return None  # no number, or a landline WhatsApp can't reach
    name = r.get("company") or company.get("name") or ""
    message = template.replace("{gym}", name or "your gym")
    digits = re.sub(r"\D", "", number)
    return {
        "company_name": name,
        "whatsapp_number": number,
        "number_source": "wa.me link on site" if confirmed else "mobile number (unconfirmed)",
        "whatsapp_link": f"https://wa.me/{digits}?text={quote(message)}",
        "website": r.get("source_url", ""),
        "email": r.get("email", ""),
        "email_status": r.get("email_status", ""),
        "city": company.get("location", ""),
        "score": r.get("score", 0),
        "status": r.get("status", ""),
        "message": message,
    }


async def collect_whatsapp(state: StateManager) -> tuple[list[dict], int]:
    """Prospects with a usable WhatsApp number. Returns (rows, skipped_landlines)."""
    import aiosqlite

    async with aiosqlite.connect(state.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM prospects WHERE phone != '' ORDER BY phone_verified DESC, "
            "score DESC, created_at DESC"
        ) as cur:
            prospects = [dict(r) for r in await cur.fetchall()]
        async with db.execute("SELECT id, name, location FROM companies") as cur:
            companies = {r["id"]: dict(r) for r in await cur.fetchall()}

    template = load_whatsapp_template()
    rows, seen, skipped = [], set(), 0
    for p in prospects:
        row = _whatsapp_row(p, companies.get(p.get("company_id", ""), {}), template)
        if row is None:
            skipped += 1
            continue
        if row["whatsapp_number"] in seen:
            continue  # two prospects, same shared gym number
        seen.add(row["whatsapp_number"])
        rows.append(row)
    return rows, skipped


def whatsapp_to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=WHATSAPP_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


async def export_whatsapp_csv(
    state: StateManager, out_path: str | None = None
) -> tuple[int, int, str]:
    """Export WhatsApp-ready contacts. Returns (rows, skipped_landlines, csv_text).

    Written with a UTF-8 BOM so Excel on Windows shows ₹ and Indic text correctly.
    """
    rows, skipped = await collect_whatsapp(state)
    text = whatsapp_to_csv(rows)
    if out_path:
        with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
            f.write(text)
        logger.info(f"Exported {len(rows)} WhatsApp contacts to {out_path}")
    return len(rows), skipped, text


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


async def export_prospects_csv(
    state: StateManager,
    out_path: str | None = None,
    email_statuses: list[str] | None = None,
    min_score: int = 0,
    statuses: list[str] | None = None,
    include_all: bool = False,
) -> tuple[int, str]:
    """Export prospects to CSV. Returns (row_count, csv_text).

    Writes to ``out_path`` when given; caller handles printing/serving.
    """
    rows = await collect_prospects(
        state,
        email_statuses=email_statuses,
        min_score=min_score,
        statuses=statuses,
        include_all=include_all,
    )
    text = to_csv(rows)
    if out_path:
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            f.write(text)
        logger.info(f"Exported {len(rows)} prospects to {out_path}")
    return len(rows), text
