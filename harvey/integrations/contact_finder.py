"""Business-level contact details scraped from a company's own pages.

Small independent businesses (gyms, clinics, salons) rarely publish a team page,
but almost all publish a WhatsApp link, a phone number, or a general inbox.
These helpers are pure functions over HTML so they are easy to test.
"""

import re
from html import unescape
from urllib.parse import unquote

# wa.me/9198..., api.whatsapp.com/send?phone=9198..., whatsapp://send?phone=...
_WA_PATTERNS = (
    re.compile(r"wa\.me/\+?(\d{8,15})", re.I),
    re.compile(r"(?:api|web)\.whatsapp\.com/send/?\?[^\"'\s>]*?phone=\+?(\d{8,15})", re.I),
    re.compile(r"whatsapp://send\?[^\"'\s>]*?phone=\+?(\d{8,15})", re.I),
)
_TEL = re.compile(r"""href\s*=\s*["']tel:([^"']+)["']""", re.I)
_MAILTO = re.compile(r"""href\s*=\s*["']mailto:([^"'?]+)""", re.I)
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")

_FREE_MAIL = {
    "gmail.com", "yahoo.com", "yahoo.in", "yahoo.co.in", "outlook.com",
    "hotmail.com", "rediffmail.com", "live.com", "icloud.com",
}
_JUNK_EMAIL_PARTS = (
    "sentry", "wixpress", "example.", "yourdomain", "domain.com", "email.com",
    "noreply", "no-reply", "donotreply", "u003e", "@2x", "godaddy", "schema.org",
)
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js")


def normalize_phone(raw: str, default_country: str = "91") -> str:
    """Return +<digits> for a plausible phone number, or '' if it isn't one."""
    digits = re.sub(r"\D", "", unquote(raw or ""))
    if not digits:
        return ""
    if len(digits) == 10 and digits[0] in "6789":
        digits = default_country + digits
    elif len(digits) == 11 and digits.startswith("0"):
        digits = default_country + digits[1:]
    elif len(digits) == 12 and digits.startswith(default_country):
        pass
    elif not 11 <= len(digits) <= 15:
        return ""
    return "+" + digits


def extract_whatsapp(html: str) -> list[str]:
    """WhatsApp numbers from click-to-chat links, most frequent first."""
    text = unescape(html or "")
    counts: dict[str, int] = {}
    for pattern in _WA_PATTERNS:
        for match in pattern.findall(text):
            number = normalize_phone(match)
            if number:
                counts[number] = counts.get(number, 0) + 1
    return sorted(counts, key=lambda n: -counts[n])


def extract_phones(html: str) -> list[str]:
    """Phone numbers from tel: links, de-duplicated in page order."""
    out: list[str] = []
    for raw in _TEL.findall(unescape(html or "")):
        number = normalize_phone(raw)
        if number and number not in out:
            out.append(number)
    return out


def _clean_email(addr: str) -> str:
    addr = unquote(addr or "").strip().strip(".,;:<>()[]\"'").lower()
    if addr.count("@") != 1 or any(addr.endswith(s) for s in _IMAGE_SUFFIXES):
        return ""
    if any(part in addr for part in _JUNK_EMAIL_PARTS):
        return ""
    return addr


def extract_emails(html: str, site_domain: str) -> list[str]:
    """Published business inboxes.

    On-domain addresses are accepted wherever they appear. Free-mail addresses
    (very common for small gyms) are accepted only from an explicit mailto: link,
    so a testimonial or a scraped footer doesn't become a "business inbox".
    """
    site_domain = (site_domain or "").lower().removeprefix("www.")
    text = unescape(html or "")
    out: list[str] = []

    def add(addr: str) -> None:
        if addr and addr not in out:
            out.append(addr)

    for raw in _MAILTO.findall(text):
        addr = _clean_email(raw)
        domain = addr.partition("@")[2]
        if domain == site_domain or domain.endswith("." + site_domain) or domain in _FREE_MAIL:
            add(addr)
    for raw in _EMAIL.findall(text[:60000]):
        addr = _clean_email(raw)
        domain = addr.partition("@")[2]
        if domain and (domain == site_domain or domain.endswith("." + site_domain)):
            add(addr)
    return out
