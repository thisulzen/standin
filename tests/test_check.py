from pathlib import Path

import pytest

from standin import Problem
from standin.check import check
from standin.csvfile import Format
from synth import write_csv

UTF8 = Format("utf-8", ",", "\n")
RULES = {"id": {"rule": "token"}, "email": {"rule": "token"}, "note": {"rule": "keep"}, "tag": {"rule": "blank"}, "x": {"rule": "drop"}}
HEADER = ["id", "email", "note", "tag", "x"]


def compare(tmp_path: Path, original: list[list[str]], masked: list[list[str]], header=None) -> list[str]:
    a = write_csv(tmp_path / "a.csv", HEADER, original)
    b = write_csv(tmp_path / "b.csv", header or [c for c in HEADER if c != "x"], masked)
    return check(RULES, a, b, UTF8, UTF8)[1]


def test_a_properly_masked_file_has_no_findings(tmp_path):
    assert compare(tmp_path, [["C1", "jo@example.com", "hi", "t", "1"]], [["K7", "qe@zxcvbnm.qwe", "hi", ""]]) == []


def test_each_kind_of_leak_is_found_and_no_value_is_quoted(tmp_path):
    original = [
        ["C100001", "jo@example.com", "fine", "a", "1"],
        ["C100002", "al@example.com", "C100001", "b", "2"],
        ["C100003", "bo@example.com", "call jo@example.com", "", "3"],
        ["C100004", "cy@example.com", "card 4111 1111 1111 1111", "", "4"],
    ]
    masked = [
        ["C100001", "qw@zxcvbnm.qwe", "fine", "a"],  # id unchanged, tag not blank
        ["K200002", "er@zxcvbnm.qwe", "C100001", ""],  # a real id copied into a kept note
        ["K200003", "ty@zxcvbnm.qwe", "call jo@example.com", ""],
        ["K200004", "ui@zxcvbnm.qwe", "changed", ""],
    ]
    findings = "\n".join(compare(tmp_path, original, masked))
    assert '"id" (token): 1 cell unchanged from the original, line 2' in findings
    assert '"tag" (blank): 1 cell not blank, line 2' in findings
    assert '"note" (keep): 1 cell holding a value from a masked column of the original, line 3' in findings
    assert '"note" (keep): 1 cell with something that looks like an email address, line 4' in findings
    assert '"note" (keep): 1 cell meant to be kept but different from the original, line 5' in findings
    assert "C100001" not in findings and "example.com" not in findings


def test_card_numbers_in_kept_columns_are_found(tmp_path):
    original = [["C1", "a@b.co", "card 4111 1111 1111 1111 ok", "", "1"]]
    findings = compare(tmp_path, original, [["K1", "q@w.er", "card 4111 1111 1111 1111 ok", ""]])
    assert findings == ['"note" (keep): 1 cell with something that looks like a card number, line 2']


def test_values_with_nothing_to_mask_are_not_findings(tmp_path):
    assert compare(tmp_path, [["-", "", "", "", ""]], [["-", "", "", ""]]) == []


def test_files_that_cant_be_compared_are_a_problem(tmp_path):
    with pytest.raises(Problem, match="columns aren't what these rules make"):
        compare(tmp_path, [["C1", "a@b.co", "", "", ""]], [["K1", "q@w.er", "", "", "1"]], header=HEADER)
    with pytest.raises(Problem, match="has 2 rows but .* has 1"):
        compare(tmp_path, [["C1", "a@b.co", "", "", ""]] * 2, [["K1", "q@w.er", "", ""]])
