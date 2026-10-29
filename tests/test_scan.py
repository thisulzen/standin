import tomllib
from pathlib import Path

from standin.csvfile import Format
from standin.rules import load
from standin.scan import guess, scan, words
from synth import CUSTOMER_HEADER, TRANSACTION_HEADER, customers, transactions, write_csv

UTF8 = Format("utf-8", ",", "\n")


def rule_of(header: str, values: list[str]):
    return guess(header, values)[0]


def test_header_words_are_split_from_any_naming_style():
    assert words("customerID") == {"customer", "id"}
    assert words("first_name") == {"first", "name"}
    assert words("Date of Birth") == {"date", "of", "birth"}


def test_guesses_for_a_synthetic_bank_extract():
    rows = customers(300)
    columns = {h: [r[i] for r in rows] for i, h in enumerate(CUSTOMER_HEADER)}
    got = {h: rule_of(h, v) for h, v in columns.items()}
    assert got == {
        "customer_id": "token",
        "full_name": "name",
        "email": "token",
        "phone": "token",
        "date_of_birth": {"rule": "date", "format": "%Y-%m-%d"},
        "card_number": "card",
        "balance": "keep",
        "notes": "blank",
        "status": "keep",
    }


def test_values_decide_when_the_header_says_nothing():
    assert rule_of("col7", ["a@b.co", "c@d.org"] * 10) == "token"
    assert rule_of("col7", ["4111111111111111"] * 10) == "card"
    assert rule_of("col7", ["0412 345 678", "(02) 9876 5432"] * 10) == "token"
    assert rule_of("col7", ["31/12/2020", "01/02/2021"] * 10) == "keep"
    assert rule_of("col7", ["12.50", "-3", "1,200.00"] * 10) == "keep"
    assert rule_of("col7", ["Corner Grocer", "Metro Transit"] * 30) == "keep"


def test_anything_unrecognised_or_empty_is_blanked():
    assert rule_of("col7", [f"free text {i}" for i in range(50)]) == "blank"
    assert rule_of("col7", ["", " "]) == "blank"
    # Few distinct values in a tiny sample isn't evidence of a category.
    assert rule_of("nickname", ["Jo", "Al", "Bo"]) == "blank"


def test_a_company_name_isnt_mistaken_for_a_person():
    assert rule_of("merchant_name", ["Corner Grocer", "Metro Transit"] * 30) == "keep"


def test_identifiers_with_letters_token_cant_replace_are_blanked():
    assert rule_of("address", ["12 Rue de l'Église", "4 High St"]) == "blank"


def test_scan_output_loads_and_covers_every_column_of_every_file(tmp_path):
    a = write_csv(tmp_path / "customers.csv", CUSTOMER_HEADER, customers(50))
    b = write_csv(tmp_path / "transactions.csv", TRANSACTION_HEADER, transactions(50, 50))
    text = scan([a, b], [UTF8, UTF8])
    (tmp_path / "rules.toml").write_text(text)
    rules = load(tmp_path / "rules.toml")
    assert set(rules) == set(CUSTOMER_HEADER) | set(TRANSACTION_HEADER)
    # "status" is a category in both files; stricter guesses would win a tie.
    assert rules["status"] == {"rule": "keep"}


def test_awkward_column_names_survive_the_trip_through_toml(tmp_path):
    header = ["Date of Birth", 'say "hi"', "a.b", "[x]", "tab\there", "del\x7f", "名前", ""]
    path = write_csv(tmp_path / "odd.csv", header, [["1"] * len(header)])
    assert list(tomllib.loads(scan([path], [UTF8]))) == header


def test_when_files_disagree_the_stricter_guess_wins(tmp_path):
    a = write_csv(tmp_path / "a.csv", ["code"], [["X"]] * 40 + [["Y"]] * 40)
    b = write_csv(tmp_path / "b.csv", ["code"], [[f"free text {i}"] for i in range(80)])
    assert tomllib.loads(scan([a, b], [UTF8, UTF8]))["code"] == "blank"
