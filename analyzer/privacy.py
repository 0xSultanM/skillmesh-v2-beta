"""Redact contact details before any text is sent to an external LLM."""
import re

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)")
_PROFILE_URL = re.compile(
    r"(?:https?://)?(?:www\.)?(?:linkedin\.com|github\.com|twitter\.com|x\.com|facebook\.com|instagram\.com)/[^\s,;)]+",
    re.IGNORECASE,
)
_STREET = re.compile(
    r"\b\d{1,5}\s+(?:[A-Za-z]+\s){0,3}(?:street|st\.|avenue|ave\.|road|rd\.|lane|ln\.|boulevard|blvd\.?|drive|dr\.)\b",
    re.IGNORECASE,
)
_YEAR_RANGE = re.compile(r"^\s*(19|20)\d{2}\s*[-–]\s*(19|20)\d{2}\s*$")


def redact_pii(text: str) -> str:
    text = _EMAIL.sub("[email]", text)
    text = _PROFILE_URL.sub("[profile-link]", text)
    text = _STREET.sub("[address]", text)

    def phone(m: re.Match) -> str:
        s = m.group(0)
        digits = re.sub(r"\D", "", s)
        # Keep things that are clearly date ranges like "2019 - 2023".
        if len(digits) < 9 or _YEAR_RANGE.match(s):
            return s
        return "[phone]"

    return _PHONE.sub(phone, text)
