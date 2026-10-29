"""Synthetic bank extracts for the tests. Every name, number and address is made up."""

import csv
import random
from pathlib import Path

from standin.rules import FIRST_NAMES, SURNAMES, luhn_digit

CUSTOMER_HEADER = ["customer_id", "full_name", "email", "phone", "date_of_birth", "card_number", "balance", "notes", "status"]
TRANSACTION_HEADER = ["txn_id", "customer_id", "posted", "amount", "merchant", "status"]
CUSTOMER_RULES = """\
customer_id = "token"
full_name = "name"
email = "token"
phone = "token"
date_of_birth = { rule = "date", by = "customer_id", days = 180 }
card_number = "card"
balance = "keep"
notes = "blank"
status = "keep"
txn_id = "token"
posted = { rule = "date", by = "customer_id" }
amount = "keep"
merchant = "keep"
"""


def card(rng: random.Random) -> str:
    body = "411111" + "".join(rng.choice("0123456789") for _ in range(9))
    n = body + luhn_digit(body)
    return " ".join(n[i : i + 4] for i in range(0, 16, 4))


def customers(n: int, seed: int = 0) -> list[list[str]]:
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        first, last = rng.choice(FIRST_NAMES), rng.choice(SURNAMES)
        rows.append([
            f"C{i:06d}",
            f"{first} {last}",
            f"{first}.{last}{i}@example.com".lower().replace("'", ""),
            f"+61 4{rng.randrange(10**7, 10**8)}",
            f"{rng.randrange(1940, 2006)}-{rng.randrange(1, 13):02d}-{rng.randrange(1, 29):02d}",
            card(rng),
            f"{rng.randrange(-5000, 100000) / 100:.2f}",
            rng.choice(["", "Prefers email", f"Spoke to {first} about fees"]),
            rng.choice(["active", "closed", "dormant"]),
        ])
    return rows


def transactions(n: int, customer_count: int, seed: int = 1) -> list[list[str]]:
    rng = random.Random(seed)
    return [
        [
            f"T{i:08d}",
            f"C{rng.randrange(customer_count):06d}",
            f"2025-{rng.randrange(1, 13):02d}-{rng.randrange(1, 29):02d}",
            f"{rng.randrange(-20000, 20000) / 100:.2f}",
            rng.choice(["Corner Grocer", "Metro Transit", "Online Books", "Fuel Stop", "Payroll"]),
            rng.choice(["posted", "pending"]),
        ]
        for i in range(n)
    ]


def write_csv(path: Path, header: list[str], rows: list[list[str]], **kwargs) -> Path:
    with open(path, "w", newline="", encoding=kwargs.pop("encoding", "utf-8")) as f:
        writer = csv.writer(f, **kwargs)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def read_csv(path: Path, **kwargs) -> list[list[str]]:
    with open(path, newline="", encoding=kwargs.pop("encoding", "utf-8")) as f:
        return list(csv.reader(f, **kwargs))
