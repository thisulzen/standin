from datetime import datetime
from pathlib import Path

import pytest

from standin import Problem
from standin.rules import FIRST_NAMES, SURNAMES, BadCell, load, luhn_digit, plan

KEY = bytes(range(32))
OTHER_KEY = bytes(range(1, 33))


def mask_one(rule, values, key=KEY, header=None, rows=None):
    """Mask a single column; rows supplies the other columns a date rule can key on."""
    header = header or ["c"]
    rules = {c: {"rule": "keep"} for c in header} | {"c": rule}
    _, mask = plan(rules, header, key, "t.csv")
    i = header.index("c")
    if rows is None:
        rows = [[v] for v in values]
    return [mask(row)[i] for row in rows]


def passes_luhn(number: str) -> bool:
    digits = "".join(c for c in number if c.isdigit())
    return luhn_digit(digits[:-1]) == digits[-1]


def write_rules(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "rules.toml"
    path.write_text(text)
    return path


def test_load_accepts_short_and_long_forms(tmp_path):
    rules = load(write_rules(tmp_path, 'id = "token"\n"Date of birth" = { rule = "date", days = 90, by = "id" }\n'))
    assert rules == {"id": {"rule": "token"}, "Date of birth": {"rule": "date", "days": 90, "by": "id"}}


@pytest.mark.parametrize(
    "text, message",
    [
        ('id = "tokn"', "The rules are"),
        ('id = { rule = "token", days = 3 }', "no option days"),
        ('d = { rule = "date", days = 0 }', "at least 1"),
        ('d = { rule = "date", days = true }', "at least 1"),
        ('d = { rule = "date", format = "%d/%m" }', "year, month and day"),
        ('d = { rule = "date", by = 3 }', "column name in quotes"),
        # A dotted name is a TOML table, which reads as a column with no rule.
        ('customer.id = "token"', "needs quotes"),
        ("id = 3", "should be a rule name"),
        ("id = ", "isn't a valid rules file"),
        ("", "no rules in it"),
    ],
)
def test_load_refuses_bad_rules_files_with_a_way_forward(tmp_path, text, message):
    with pytest.raises(Problem, match=message):
        load(write_rules(tmp_path, text))


def test_load_reads_rules_saved_by_notepad_or_powershell(tmp_path):
    for encoding in ["utf-8-sig", "utf-16"]:
        (tmp_path / "rules.toml").write_bytes('"名前" = "name"\n'.encode(encoding))
        assert load(tmp_path / "rules.toml") == {"名前": {"rule": "name"}}


def test_load_reports_a_missing_rules_file(tmp_path):
    with pytest.raises(Problem, match="can't read the rules file"):
        load(tmp_path / "nope.toml")


def test_a_column_without_a_rule_stops_the_run():
    with pytest.raises(Problem, match='no rule: "email"'):
        plan({"id": {"rule": "keep"}}, ["id", "email"], KEY, "t.csv")


def test_rules_for_columns_a_file_doesnt_have_are_fine():
    header, _ = plan({"id": {"rule": "keep"}, "other": {"rule": "token"}}, ["id"], KEY, "t.csv")
    assert header == ["id"]


def test_duplicate_column_names_are_refused():
    with pytest.raises(Problem, match="two columns"):
        plan({"id": {"rule": "keep"}}, ["id", "id"], KEY, "t.csv")


def test_drop_removes_the_column_and_blank_empties_it():
    header, mask = plan({"a": {"rule": "drop"}, "b": {"rule": "blank"}, "c": {"rule": "keep"}}, ["a", "b", "c"], KEY, "t")
    assert header == ["b", "c"]
    assert mask(["1", "2", "3"]) == ["", "3"]


def test_every_rule_leaves_empty_cells_empty():
    for rule in [{"rule": r} for r in ["token", "card", "name", "date", "blank"]]:
        assert mask_one(rule, ["", "  "]) in (["", "  "], ["", ""])


def test_token_is_consistent_and_keyed():
    a = mask_one({"rule": "token"}, ["C000123", "C000124", "C000123"])
    assert a[0] == a[2] != a[1]
    assert a[0] != mask_one({"rule": "token"}, ["C000123"], key=OTHER_KEY)[0]


def test_token_refuses_letters_it_would_leave_in_place():
    # Restyling would replace the ASCII letters and leave the rest readable.
    for value in ["Zoë", "王小明", "Ølsen"]:
        with pytest.raises(BadCell, match='column "c" has letters or digits outside'):
            mask_one({"rule": "token"}, [value])


def test_card_keeps_the_bin_and_formatting_and_passes_luhn():
    cards = ["4111 1111 1111 1111", "5500-0000-0000-0004", "340000000000009", "6011000990139424"]
    for original, masked in zip(cards, mask_one({"rule": "card"}, cards)):
        assert masked != original
        assert masked[:7].replace(" ", "").replace("-", "")[:6] == original[:7].replace(" ", "").replace("-", "")[:6]
        assert [c for c in masked if not c.isdigit()] == [c for c in original if not c.isdigit()]
        assert len(masked) == len(original)
        assert passes_luhn(masked)


def test_card_never_gives_two_cards_the_same_stand_in():
    cards = [f"411111{i:09d}" for i in range(2000)]
    cards = [c + luhn_digit(c) for c in cards]
    assert len(set(mask_one({"rule": "card"}, cards))) == 2000


@pytest.mark.parametrize("value", ["4111 1111", "4111x111111111111", "41111111111111111111"])
def test_card_refuses_things_that_arent_card_numbers(value):
    with pytest.raises(BadCell, match="card number"):
        mask_one({"rule": "card"}, [value])


def test_luhn_digit_matches_known_test_numbers():
    for number in ["4111111111111111", "5500000000000004", "340000000000009", "6011000990139424"]:
        assert luhn_digit(number[:-1]) == number[-1]


def test_date_moves_every_date_within_the_window():
    dates = [f"2020-01-{d:02d}" for d in range(1, 29)]
    for original, masked in zip(dates, mask_one({"rule": "date", "days": 5}, dates)):
        gap = abs((datetime.fromisoformat(masked) - datetime.fromisoformat(original)).days)
        assert 1 <= gap <= 5


def test_date_by_column_keeps_one_customers_gaps():
    header = ["id", "c"]
    rows = [["C1", "2020-01-01"], ["C1", "2020-03-15"], ["C2", "2020-01-01"], ["C2", "2020-03-15"]]
    out = mask_one({"rule": "date", "days": 365, "by": "id"}, None, header=header, rows=rows)
    d = [datetime.fromisoformat(x) for x in out]
    assert (d[1] - d[0]).days == (d[3] - d[2]).days == 74
    assert d[0] != d[2]  # different customers move by different amounts


def test_date_keeps_its_format_and_time():
    out = mask_one({"rule": "date", "format": "%d/%m/%Y %H:%M"}, ["31/12/1999 23:59"])[0]
    assert datetime.strptime(out, "%d/%m/%Y %H:%M").time().isoformat() == "23:59:00"


def test_date_refuses_a_value_in_the_wrong_format_without_quoting_it():
    with pytest.raises(BadCell) as e:
        mask_one({"rule": "date"}, ["03/04/1987"])
    assert "%Y-%m-%d" in str(e.value) and "1987" not in str(e.value)


def test_date_by_a_column_the_file_lacks_is_refused():
    with pytest.raises(Problem, match="isn't a column in this file"):
        plan({"c": {"rule": "date", "by": "id"}}, ["c"], KEY, "t.csv")


def test_name_gives_fake_names_of_the_same_length_in_words():
    out = mask_one({"rule": "name"}, ["Jane Smith", "王小明", "Mary Anne Lee", "JANE SMITH", "Jane Smith"])
    first, last = out[0].split()
    assert first in FIRST_NAMES and last in SURNAMES
    assert out[1] in FIRST_NAMES
    assert len(out[2].split()) == 3
    assert out[3] == out[3].upper()
    assert out[0] == out[4]


def test_name_never_hands_someone_their_own_name():
    # The fake-name lists hold common real names, so without a redraw some
    # customers would come out under their own name.
    names = [f"{f} {s}" for f in FIRST_NAMES[:30] for s in SURNAMES[:30]] + list(FIRST_NAMES)
    assert all(a.casefold() != b.casefold() for a, b in zip(names, mask_one({"rule": "name"}, names)))
