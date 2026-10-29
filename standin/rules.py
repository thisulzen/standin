"""The rules file, and what each rule does to a cell.

A rules file names every column and what to do with it. A column with no rule
stops the run instead of passing through. The dangerous mistake is the column
someone adds to an export next month, and this way the first run after it
appears fails. One rule covers a column name in every file, which is what
keeps a customer ID the same stand-in in customers.csv and transactions.csv.

Every rule leaves an empty cell empty. Every keyed rule is a pure function of
the key and the original values, so the same key gives the same output on every
run, on every machine.
"""

import codecs
import string
import tomllib
import warnings
from datetime import datetime, timedelta
from functools import lru_cache
from itertools import count
from pathlib import Path
from typing import Callable

from . import Problem
from .shape import permute, prf, restyle

OPTIONS = {
    "keep": set(),
    "drop": set(),
    "blank": set(),
    "token": set(),
    "card": set(),
    "name": set(),
    "date": {"by", "days", "format"},
}
DEFAULT_DATE_FORMAT = "%Y-%m-%d"
DEFAULT_DAYS = 30
CACHE_SIZE = 1 << 16  # repeated IDs (a customer on every transaction) are worked out once

Cell = Callable[[str, list[str]], str]


class BadCell(Exception):
    """A cell this rule can't handle. Like Problem, the message never quotes it."""


def load(path: Path) -> dict[str, dict]:
    """Read and check a rules file. Returns {column: {"rule": ..., options}}.

    Raises Problem for anything wrong with the file, before any data is read.
    """
    try:
        data = path.read_bytes()
        # Notepad may add a byte order mark, and PowerShell 5's > writes UTF-16.
        text = data.decode("utf-16") if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) else data.decode("utf-8-sig")
        raw = tomllib.loads(text)
    except OSError as e:
        raise Problem(f"can't read the rules file {path}: {e.strerror}") from None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise Problem(f"{path} isn't a valid rules file: {e}") from None

    rules = {}
    for column, spec in raw.items():
        if isinstance(spec, str):
            spec = {"rule": spec}
        where = f'{path}: column "{column}"'
        if not isinstance(spec, dict):
            raise Problem(f"{where} should be a rule name such as \"token\", or a table like {{ rule = \"date\", days = 30 }}")
        if "rule" not in spec:
            # Usually a dotted or [bracketed] name that TOML read as a table.
            raise Problem(f"{where} has no rule. A column name with dots, spaces or brackets needs quotes: \"{column}.x\" = \"token\"")
        rule = spec["rule"]
        if rule not in OPTIONS:
            raise Problem(f"{where} has the rule {rule!r}. The rules are {', '.join(OPTIONS)}")
        unknown = set(spec) - {"rule"} - OPTIONS[rule]
        if unknown:
            raise Problem(f"{where}: {rule} has no option {', '.join(sorted(unknown))}")
        if rule == "date":
            _check_date(spec, where)
        rules[column] = spec
    if not rules:
        raise Problem(f"{path} has no rules in it. standin scan writes a starting point")
    return rules


def _check_date(spec: dict, where: str) -> None:
    days = spec.get("days", DEFAULT_DAYS)
    if type(days) is not int or days < 1:
        raise Problem(f"{where}: days must be a whole number of at least 1")
    if not isinstance(spec.get("by", ""), str):
        raise Problem(f"{where}: by must be a column name in quotes")
    fmt = spec.get("format", DEFAULT_DATE_FORMAT)
    # A format that can't carry a date there and back would shift every date
    # into 1900 or lose the day, so it's refused here rather than in row one.
    probe = datetime(2001, 2, 3, 4, 5, 6)
    try:
        # Python 3.13 warns about parsing a day with no year, which is the very
        # format this is here to refuse. 3.15 raises or changes the year, and
        # either way the probe still fails.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            ok = isinstance(fmt, str) and datetime.strptime(probe.strftime(fmt), fmt).date() == probe.date()
    except ValueError:
        ok = False
    if not ok:
        raise Problem(f"{where}: the format must give the year, month and day, like \"%Y-%m-%d\" or \"%d/%m/%Y\"")


def plan(rules: dict[str, dict], header: list[str], key: bytes, source: str) -> tuple[list[str], Callable[[list[str]], list[str]]]:
    """Work out what happens to each column of one file.

    Returns the output header and a function that masks one row. Raises Problem
    if a column has no rule, a name is used twice, or a date rule's "by" column
    isn't in this file.
    """
    seen = set()
    for column in header:
        if column in seen:
            raise Problem(f'{source} has two columns called "{column}", so a rule for it would be ambiguous')
        seen.add(column)
    missing = [c for c in header if c not in rules]
    if missing:
        names = ", ".join(f'"{c}"' for c in missing)
        raise Problem(
            f"{source} has columns with no rule: {names}. standin stops rather than pass an unknown column "
            f"through. Add a rule for each to the rules file (standin scan suggests some)"
        )

    cells = []
    for i, column in enumerate(header):
        spec = rules[column]
        if spec["rule"] != "drop":
            cells.append((i, column, _cell_rule(spec, header, key, source, column)))

    def mask(row: list[str]) -> list[str]:
        out = []
        for i, column, rule in cells:
            try:
                out.append(rule(row[i], row))
            except BadCell as e:
                raise BadCell(f'column "{column}" {e}') from None
        return out

    return [column for _, column, _ in cells], mask


def _cell_rule(spec: dict, header: list[str], key: bytes, source: str, column: str) -> Cell:
    rule = spec["rule"]
    if rule == "keep":
        return lambda cell, row: cell
    if rule == "blank":
        return lambda cell, row: ""
    if rule == "token":
        return _skip_empty(_token(key))
    if rule == "card":
        return _skip_empty(_card(key))
    if rule == "name":
        return _skip_empty(_name(key))
    by = spec.get("by")
    if by is not None and by not in header:
        raise Problem(f'{source}: the date rule for "{column}" shifts by "{by}", which isn\'t a column in this file')
    return _skip_empty(_date(key, spec.get("format", DEFAULT_DATE_FORMAT), spec.get("days", DEFAULT_DAYS), header.index(by) if by else None))


def _skip_empty(rule: Cell) -> Cell:
    return lambda cell, row: rule(cell, row) if cell.strip() else cell


def _token(key: bytes) -> Cell:
    stand_in = lru_cache(CACHE_SIZE)(lambda value: restyle(value, key, b"token"))

    def token(cell: str, row: list[str]) -> str:
        if any(not c.isascii() and c.isalnum() for c in cell):
            # restyle would leave these characters where they are, so a name
            # in another script would come through untouched.
            raise BadCell("has letters or digits outside A-Z and 0-9, which token can't replace. Use name for people's names, or blank")
        return stand_in(cell)

    return token


def _card(key: bytes) -> Cell:
    def card(cell: str, row: list[str]) -> str:
        digits = "".join(c for c in cell if c in string.digits)
        if set(cell) - set(string.digits + " -") or not 12 <= len(digits) <= 19:
            raise BadCell("doesn't look like a card number (12 to 19 digits, with spaces or dashes at most). Use token if it holds something else")
        # The first six digits (the BIN) say which bank issued the card, not
        # whose it is, and test systems route on them, so they stay. The middle
        # is permuted and the check digit recomputed so the result passes Luhn.
        issuer, middle = digits[:6], digits[6:-1]
        tweak = b"card" + issuer.encode() + bytes([len(digits)])
        payload = issuer + str(permute(int(middle), 10 ** len(middle), key, tweak)).zfill(len(middle))
        new = iter(payload + luhn_digit(payload))
        return "".join(next(new) if c in string.digits else c for c in cell)

    return card


def luhn_digit(payload: str) -> str:
    """The check digit that makes payload + digit pass the Luhn check."""
    total = 0
    for i, c in enumerate(reversed(payload)):
        d = int(c)
        if i % 2 == 0:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return str(-total % 10)


def _date(key: bytes, fmt: str, days: int, by: int | None) -> Cell:
    def date(cell: str, row: list[str]) -> str:
        try:
            when = datetime.strptime(cell, fmt)
        except ValueError:
            raise BadCell(f"isn't a date in the format {fmt}. Set format for this column in the rules file") from None
        # Keyed on the "by" column when there is one, so all of one customer's
        # dates move together and the gaps between them are kept.
        seed = row[by] if by is not None else cell
        h = int.from_bytes(prf(key, b"date\0" + seed.encode())[:8], "big") % (2 * days)
        shift = h - days if h < days else h - days + 1  # never zero, so every date moves
        try:
            return (when + timedelta(days=shift)).strftime(fmt)
        except OverflowError:
            raise BadCell("is too close to the year 1 or 9999 to shift") from None

    return date


def _name(key: bytes) -> Cell:
    def name(cell: str, row: list[str]) -> str:
        words = cell.split()
        for attempt in count():
            digest = prf(key, b"name\0" + attempt.to_bytes(4, "big") + cell.encode())
            out = []
            for i in range(len(words)):
                pick = int.from_bytes(digest[2 * i % 64 : 2 * i % 64 + 2], "big")
                pool = SURNAMES if i == len(words) - 1 and len(words) > 1 else FIRST_NAMES
                out.append(pool[pick % len(pool)])
            fake = " ".join(out)
            # A real James Smith could draw James Smith. Drawing again costs
            # nothing, because fake names were never one to one anyway.
            if fake.casefold() != " ".join(words).casefold():
                return fake.upper() if cell.isupper() else fake

    return name


# Common given names and surnames from many places, so a masked customer list
# looks like a real one. Not unique: two people can get the same fake name.
FIRST_NAMES = (
    "Aaliyah", "Aarav", "Abdul", "Ada", "Aditi", "Ahmed", "Aiko", "Alejandro", "Alex", "Alice",
    "Amara", "Amelia", "Ana", "Andrei", "Anika", "Arjun", "Asha", "Ava", "Ayesha", "Bao",
    "Ben", "Carlos", "Charlotte", "Chen", "Chloe", "Daniel", "David", "Diego", "Dmitri", "Elena",
    "Eli", "Ella", "Emily", "Emma", "Erik", "Fatima", "Felix", "Freya", "Gabriel", "Grace",
    "Hana", "Hannah", "Hiroshi", "Ibrahim", "Isabella", "Ivan", "Jack", "Jamal", "James", "Jia",
    "Jin", "Jose", "Kai", "Kavya", "Kenji", "Kiri", "Lachlan", "Layla", "Leila", "Liam",
    "Lucas", "Lucia", "Luis", "Maria", "Mason", "Mateo", "Maya", "Mei", "Mia", "Mohammed",
    "Nadia", "Nina", "Noah", "Nour", "Olivia", "Omar", "Oscar", "Priya", "Rafael", "Ravi",
    "Rosa", "Ruby", "Sakura", "Samuel", "Sara", "Sebastian", "Sofia", "Sophie", "Tamar", "Tariq",
    "Thomas", "Tom", "Uma", "Vikram", "Wei", "William", "Yara", "Yusuf", "Zara", "Zoe",
)
SURNAMES = (
    "Abara", "Ahmed", "Ali", "Anderson", "Bauer", "Brown", "Campbell", "Chen", "Costa", "Cruz",
    "Das", "Davies", "Dubois", "Edwards", "Evans", "Fernandes", "Fischer", "Garcia", "Gomez", "Gupta",
    "Hall", "Han", "Hassan", "Hernandez", "Hughes", "Ibrahim", "Ito", "Jackson", "Jensen", "Johnson",
    "Jones", "Kaur", "Kelly", "Khan", "Kim", "Kowalski", "Kumar", "Lee", "Lewis", "Li",
    "Lin", "Lopez", "Martin", "Martinez", "Mehta", "Miller", "Mitchell", "Moreau", "Morris", "Muller",
    "Murphy", "Nair", "Nakamura", "Nguyen", "Novak", "O'Brien", "Okafor", "Oliveira", "Park", "Patel",
    "Perez", "Petrov", "Pham", "Quinn", "Ramos", "Reddy", "Reyes", "Roberts", "Rossi", "Russo",
    "Ryan", "Saito", "Sanchez", "Santos", "Schmidt", "Shah", "Silva", "Singh", "Smith", "Suzuki",
    "Tanaka", "Taylor", "Thomas", "Thompson", "Tran", "Turner", "Walker", "Wang", "Watson", "White",
    "Williams", "Wilson", "Wong", "Wright", "Wu", "Yamamoto", "Yang", "Young", "Zhang", "Zhou",
)
