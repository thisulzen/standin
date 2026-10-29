"""Guess a rule for each column, as a starting point for a rules file.

The guesses come from the header and the first rows of values, and each one is
written with its reason beside it, because the file is meant to be read and
corrected before anyone relies on it. A column it can't place is blanked, so if
someone uses the file unread, the worst that happens is an empty column.
"""

import json
import re
from datetime import datetime
from pathlib import Path

from .csvfile import Format, table
from .rules import luhn_digit

SAMPLE_ROWS = 1000
MOSTLY = 0.9  # share of non-empty sample values a pattern must match

# ASCII, so \d means 0 to 9 here as it does in the card and token rules.
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+", re.ASCII)
CARD = re.compile(r"\b(?:\d[ -]?){11,18}\d\b", re.ASCII)
PHONE = re.compile(r"\+?[\d ()-]{8,}", re.ASCII)
NUMBER = re.compile(r"[-+]?\d[\d,]*(\.\d+)?", re.ASCII)
DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"]

BIRTH = {"dob", "birth", "birthday", "birthdate", "born"}
NAME = {"name", "names", "firstname", "lastname", "surname", "fullname", "forename", "given", "middle"}
NOT_A_PERSON = {"product", "company", "business", "merchant", "file", "bank", "branch", "brand", "category", "plan", "user", "account"}
FREE_TEXT = {"note", "notes", "comment", "comments", "description", "desc", "memo", "narrative", "remarks", "message", "reason", "details"}
IDENTIFIER = {
    "id", "ids", "number", "no", "num", "nbr", "account", "acct", "acc", "customer", "member", "client", "ref",
    "reference", "iban", "bsb", "tfn", "ssn", "abn", "acn", "passport", "licence", "license", "medicare",
    "postcode", "zip", "ip", "username", "login", "user", "device", "imei", "card", "pan",
    "email", "mail", "phone", "mobile", "tel", "telephone", "fax", "cell",
    "address", "addr", "street", "suburb", "city", "town", "line1", "line2", "unit",
}
AMOUNT = {"amount", "amt", "balance", "total", "price", "value", "qty", "quantity", "count", "fee", "rate", "limit"}

STRICTNESS = {"keep": 0, "date": 1, "token": 1, "card": 1, "name": 1, "blank": 2}


def words(header: str) -> set[str]:
    """Split "customerID", "first_name" or "Date of Birth" into lower-case words."""
    return {w.lower() for w in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", header)}


def is_card(value: str) -> bool:
    digits = "".join(c for c in value if c.isdigit())
    return CARD.fullmatch(value) is not None and luhn_digit(digits[:-1]) == digits[-1]


def date_format(values: list[str]) -> str | None:
    for fmt in DATE_FORMATS:
        try:
            for v in values:
                datetime.strptime(v, fmt)
        except ValueError:
            continue
        return fmt
    return None


def guess(header: str, values: list[str]) -> tuple[str | dict, str]:
    """Return a rule for one column and the reason for it."""
    w = words(header)
    values = [v for v in values if v.strip()]
    if not values:
        return "blank", "empty in the sample, so blanked to be safe"

    def mostly(test) -> bool:
        return sum(1 for v in values if test(v)) >= MOSTLY * len(values)

    def token(reason: str) -> tuple[str, str]:
        if any(not c.isascii() and c.isalnum() for v in values for c in v):
            return "blank", reason + ", but some have letters token can't replace, so blanked"
        return "token", reason

    if mostly(is_card):
        return "card", "values look like card numbers (they pass the Luhn check)"
    if mostly(EMAIL.fullmatch):
        return token("values look like email addresses")
    if w & BIRTH and (fmt := date_format(values)):
        return {"rule": "date", "format": fmt}, "header looks like a date of birth"
    if w & NAME and not w & NOT_A_PERSON:
        return "name", "header looks like a person's name"
    if w & FREE_TEXT:
        return "blank", "header looks like free text, which can hold anything"
    if w & IDENTIFIER:
        return token("header looks like an identifier, contact detail or address")
    if w & AMOUNT:
        return "keep", "header looks like an amount"
    # Dates before phone numbers, because 2025-07-01 is eight digits and dashes.
    if fmt := date_format(values):
        return "keep", f"values are dates ({fmt}). Use date instead if when something happened could identify someone"
    if mostly(lambda v: PHONE.fullmatch(v) and sum(c.isdigit() for c in v) >= 8):
        return token("values look like phone numbers")
    if mostly(lambda v: v.isdigit() and len(v) >= 6):
        return token("values are long numbers, which are usually identifiers")
    if mostly(NUMBER.fullmatch):
        return "keep", "values are numbers"
    if len(set(values)) <= min(20, len(values) // 5):
        return "keep", "only a few distinct values, so probably a category or code"
    return "blank", "nothing recognised, so blanked to be safe. Change it to keep if it isn't personal"


def scan(paths: list[Path], formats: list[Format], rows: int = SAMPLE_ROWS) -> str:
    """Return a suggested rules file covering every column of every file.

    When files share a column name and the guesses differ, the stricter one
    wins, since one rule has to cover that column in every file.
    """
    guesses: dict[str, tuple[str | dict, str]] = {}
    for path, fmt in zip(paths, formats):
        header, records = table(path, fmt)
        sample = [row for _, (_, row) in zip(range(rows), records)]
        for i, column in enumerate(header):
            rule, reason = guess(column, [row[i] for row in sample])
            old = guesses.get(column)
            if old is None or STRICTNESS[_name(rule)] > STRICTNESS[_name(old[0])]:
                guesses[column] = (rule, f"{reason}, in {path.name}" if len(paths) > 1 else reason)

    lines = [
        f"# Rules suggested by standin scan from {', '.join(p.name for p in paths)}, first {rows:,} rows of each.",
        "# These are guesses. Read every line and correct it before you rely on it.",
        "# Rules: keep, drop, blank, token, card, name, and date (options: by, days, format).",
        "",
    ]
    entries = [(f"{_key(column)} = {_value(rule)}", reason) for column, (rule, reason) in guesses.items()]
    width = max(len(left) for left, _ in entries)
    lines += [f"{left.ljust(width)}  # {reason}" for left, reason in entries]
    return "\n".join(lines) + "\n"


def _name(rule: str | dict) -> str:
    return rule["rule"] if isinstance(rule, dict) else rule


def _key(column: str) -> str:
    return column if re.fullmatch(r"[A-Za-z0-9_-]+", column) else _string(column)


def _string(s: str) -> str:
    # A JSON string is a TOML basic string, except that TOML also wants DEL escaped.
    return json.dumps(s, ensure_ascii=False).replace("\x7f", "\\u007f")


def _value(rule: str | dict) -> str:
    if isinstance(rule, str):
        return _string(rule)
    return "{ " + ", ".join(f"{k} = {_string(v)}" for k, v in rule.items()) + " }"
