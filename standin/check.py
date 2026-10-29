"""Check a masked file against its original, independently of how it was made.

It reads the two files side by side, row by row, and looks for whatever would
make the masking worthless: a masked cell still equal to its original, a
blanked column that isn't blank, a kept column that changed (a sign the file
was masked with different rules), a value from a masked column turning up in a
kept one, and anything in a kept column that looks like an email address or a
card number. It needs the rules file, to know which columns were meant to be
masked, but not the key.
"""

from collections import defaultdict
from itertools import zip_longest
from pathlib import Path

from . import Problem
from .csvfile import Format, table
from .scan import CARD, EMAIL, is_card

MASKED = {"token", "card", "date", "name"}
MIN_LEAK_LENGTH = 6  # shorter values ("AU", "Mr") turn up in kept columns by coincidence
SHOW_LINES = 5


def check(rules: dict[str, dict], original: Path, masked: Path, fmt_o: Format, fmt_m: Format) -> tuple[int, list[str]]:
    """Return the number of rows compared and a list of findings.

    No findings means nothing was found, which isn't proof nothing leaked.
    Findings name columns and line numbers but never quote a value. Raises
    Problem when the files can't be compared at all.
    """
    head_o, rows_o = table(original, fmt_o)
    head_m, rows_m = table(masked, fmt_m)
    missing = [c for c in head_o if c not in rules]
    if missing:
        raise Problem(f'{original} has columns with no rule: {", ".join(missing)}, so there\'s nothing to check them against')
    expected = [c for c in head_o if rules[c]["rule"] != "drop"]
    if head_m != expected:
        raise Problem(f"{masked}'s columns aren't what these rules make of {original}: expected {expected}, found {head_m}")

    kinds = {c: rules[c]["rule"] for c in expected}
    where = [head_o.index(c) for c in expected]
    found: dict[tuple[str, str], list[int]] = defaultdict(list)
    sensitive = set()  # ponytail: every distinct masked value in memory, about 500 MB per million customers here; an on-disk sorted set if extracts outgrow RAM
    count = 0
    for pair in zip_longest(rows_o, rows_m):
        if None in pair:
            n_o = count + (pair[0] is not None) + sum(1 for _ in rows_o)
            n_m = count + (pair[1] is not None) + sum(1 for _ in rows_m)
            raise Problem(f"{original} has {n_o:,} rows but {masked} has {n_m:,}")
        (_, row_o), (line, row_m) = pair
        count += 1
        for i, cell in enumerate(row_m):
            before = row_o[where[i]]
            rule = kinds[expected[i]]
            if rule in MASKED:
                # Dates are left out: a kept date column (when an account
                # was opened) matches someone's real birthday all the time.
                if rule != "date" and len(before) >= MIN_LEAK_LENGTH:
                    sensitive.add(before)
                # A cell with no letters or digits (a lone "-") has nothing to mask.
                if cell == before and any(c.isalnum() for c in cell):
                    found[expected[i], "unchanged from the original"].append(line)
            elif rule == "blank" and cell:
                found[expected[i], "not blank"].append(line)
            elif rule == "keep" and cell != before:
                found[expected[i], "meant to be kept but different from the original"].append(line)

    # A second pass, because a kept cell can only be tested against the masked
    # values once all of them have been seen.
    _, rows_m = table(masked, fmt_m)
    kept = [i for i, c in enumerate(expected) if kinds[c] == "keep"]
    for line, row in rows_m:
        for i in kept:
            cell = row[i]
            if cell in sensitive:
                found[expected[i], "holding a value from a masked column of the original"].append(line)
            if EMAIL.search(cell):
                found[expected[i], "with something that looks like an email address"].append(line)
            if any(is_card(m.group()) for m in CARD.finditer(cell)):
                found[expected[i], "with something that looks like a card number"].append(line)

    findings = []
    for (column, what), lines in found.items():
        shown = ", ".join(map(str, lines[:SHOW_LINES])) + (", ..." if len(lines) > SHOW_LINES else "")
        findings.append(f'"{column}" ({kinds[column]}): {len(lines):,} cell{"s" if len(lines) > 1 else ""} {what}, line{"s" if len(lines) > 1 else ""} {shown}')
    return count, findings
