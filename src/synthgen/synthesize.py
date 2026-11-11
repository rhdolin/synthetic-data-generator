"""Seeded synthetic filler (design step 2, D11, D19).

Every value is drawn from a random generator seeded by the MRN plus a field name, so a
patient always gets the same name, address and dates, and adding a new field never
changes existing ones.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import random

from faker import Faker

MESSY_FRACTION = 0.15  # D19


def rng(mrn: str, field: str) -> random.Random:
    digest = hashlib.sha256(f"{mrn}|{field}".encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def faker(mrn: str, field: str) -> Faker:
    fake = Faker("en_US")
    fake.seed_instance(rng(mrn, field).getrandbits(64))
    return fake


def person_name(mrn: str, sex: str, field: str = "name") -> tuple[str, str]:
    fake = faker(mrn, field)
    given = fake.first_name_female() if sex == "female" else fake.first_name_male()
    return given, fake.last_name()


def address(mrn: str) -> dict:
    fake = faker(mrn, "address")
    return {
        "line": [fake.street_address()],
        "city": fake.city(),
        "state": fake.state_abbr(include_territories=False),
        "postalCode": fake.zipcode(),
        "country": "US",
    }


def phone(mrn: str) -> str:
    r = rng(mrn, "phone")
    return f"555-{r.randint(200, 999)}-{r.randint(1000, 9999)}"


def npi(mrn: str, field: str) -> str:
    """A 10-digit number with a valid NPI check digit (Luhn over '80840' + 9 digits)."""
    body = "9" + "".join(str(rng(mrn, field).randint(0, 9)) for _ in range(8))
    digits = [int(d) for d in "80840" + body]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return body + str((10 - total % 10) % 10)


def diagnosis_date(mrn: str, tumor_board: dt.date, last_followup_day: int) -> dt.date:
    """Day 0 on the shifted timeline: the last follow-up lands 7-21 days before the board."""
    gap = rng(mrn, "board-gap").randint(7, 21)
    return tumor_board - dt.timedelta(days=gap + last_followup_day)


def is_messy(mrn: str) -> bool:
    return rng(mrn, "messy").random() < MESSY_FRACTION


def messy_kinds(mrn: str) -> list[str]:
    if not is_messy(mrn):
        return []
    kinds = ["no-ecog", "no-labs", "drug-text-only", "missing-encounter"]
    r = rng(mrn, "messy-kinds")
    return r.sample(kinds, r.randint(1, 2))
