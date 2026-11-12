"""Safe-value generators for split F (own checksum code, Python 3.12 standard library only).

Every value is built so that the validator's independent checks accept it: fiction phone ranges, reserved email
domains, checksum-valid TFN / Medicare / ABN / IBAN, payment cards only from the published test numbers (taken from
the validator's list), SSNs in the advertising block, NINOs with the QQ prefix, vendor-free random secrets.
`selftest()` cross-checks this module against the validator's functions (never the other way round).
"""
from __future__ import annotations

import random
import re
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "validator"))
import check_cases as V  # noqa: E402  (used for the test-card list and in selftest only)

# ---------------------------------------------------------------- own checksum code
def tfn_check_digit(first8: list[int]) -> int | None:
    s = sum(a * b for a, b in zip(first8, (1, 4, 3, 7, 5, 8, 6, 9)))
    for c in range(10):
        if (s + 10 * c) % 11 == 0:
            return c
    return None


def tfn_valid(d: str) -> bool:
    return len(d) == 9 and d.isdigit() and sum(int(c) * w for c, w in zip(d, (1, 4, 3, 7, 5, 8, 6, 9, 10))) % 11 == 0


def medicare_check_digit(first8: list[int]) -> int:
    return sum(a * b for a, b in zip(first8, (1, 3, 7, 9, 1, 3, 7, 9))) % 10


def abn_valid(d: str) -> bool:
    if len(d) != 11 or not d.isdigit() or d[0] == "0":
        return False
    digits = [int(c) for c in d]
    digits[0] -= 1
    return sum(a * b for a, b in zip(digits, (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19))) % 89 == 0


def luhn_valid(d: str) -> bool:
    tot = 0
    for i, c in enumerate(reversed(d)):
        n = int(c)
        if i % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        tot += n
    return tot % 10 == 0


def iban_check_digits(country: str, bban: str) -> str:
    num = "".join(str(int(ch, 36)) for ch in bban + country + "00")
    return f"{98 - int(num) % 97:02d}"


def iban_valid(iban: str) -> bool:
    s = iban.replace(" ", "").upper()
    return int("".join(str(int(ch, 36)) for ch in s[4:] + s[:4])) % 97 == 1


def group(s: str, n: int = 4) -> str:
    return " ".join(s[i:i + n] for i in range(0, len(s), n))


# ---------------------------------------------------------------- factory
SYLL = ["ka", "to", "mi", "ra", "sel", "van", "du", "or", "ni", "pe", "lu", "ta", "bri", "han", "zo", "wel", "fen",
        "mar", "ish", "ko", "te", "ul", "bar", "dan", "eth", "gro", "jos", "len", "nor", "qui", "sam", "yor"]
EMAIL_DOMAINS = ["example.com", "example.net", "example.org"]
IBAN_SPECS = [("GB", "XBNK", 6, 8), ("GB", "ZPAY", 6, 8), ("GB", "XMPL", 6, 8), ("IE", "XBNK", 6, 8),
              ("NL", "XBNK", 0, 10), ("DE", None, 0, 0)]
SECRET_KINDS = ["api key", "password", "PIN", "access token", "database password", "client secret", "CVV"]


class Values:
    """Hands out values that are unique across the whole plan (one factory per plan seed)."""

    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.used: set[str] = set()
        cards = sorted(V.TEST_CARDS)
        self.rng.shuffle(cards)
        self.cards = cards
        self.card_i = 0

    def _fresh(self, make):
        for _ in range(10000):
            v = make()
            key = re.sub(r"\D", "", v) or v
            if key not in self.used:
                self.used.add(key)
                return v
        raise RuntimeError("could not draw a fresh value")

    def phone(self, kind: str | None = None) -> str:
        r = self.rng
        kind = kind or r.choices(["mobile", "mobile_intl", "landline", "uk", "nanp"], [60, 10, 15, 8, 7])[0]

        def mk():
            if kind == "mobile":
                d = f"{r.randint(0, 9999):04d}"
                return f"0491 57{d[0]} {d[1:]}"
            if kind == "mobile_intl":
                d = f"{r.randint(0, 9999):04d}"
                return f"+61 491 57{d[0]} {d[1:]}"
            if kind == "landline":
                return f"({r.choice(['02', '03', '07', '08'])}) {r.choice(['5550', '7010'])} {r.randint(0, 9999):04d}"
            if kind == "uk":
                return f"07700 900{r.randint(0, 999):03d}"
            return f"({r.choice([212, 310, 415, 617, 303, 206])}) 555-01{r.randint(0, 99):02d}"
        return self._fresh(mk)

    def business_phone(self) -> str:
        r = self.rng

        def mk():
            if r.random() < 0.5:
                return f"({r.choice(['02', '03', '07', '08'])}) {r.choice(['5550', '7010'])} {r.randint(0, 9999):04d}"
            return f"{r.choice(['1300', '1800'])} 975 {r.randint(707, 711)}"
        return self._fresh(mk)

    def email(self, domain: str | None = None) -> str:
        r = self.rng

        def mk():
            a = "".join(r.choice(SYLL) for _ in range(r.randint(1, 2)))
            b = "".join(r.choice(SYLL) for _ in range(r.randint(2, 3)))
            style = r.choice(["{a}.{b}", "{a}{b}", "{a0}.{b}", "{b}.{a}", "{a}_{b}{n}", "{a0}{b}{n}"])
            local = style.format(a=a, b=b, a0=a[0], n=r.randint(1, 99))
            return f"{local}@{domain or r.choice(EMAIL_DOMAINS)}"
        return self._fresh(mk)

    def tfn(self) -> str:
        r = self.rng

        def mk():
            while True:
                d = [r.randint(1, 9)] + [r.randint(0, 9) for _ in range(7)]
                c = tfn_check_digit(d)
                if c is not None:
                    s = "".join(map(str, d + [c]))
                    return f"{s[:3]} {s[3:6]} {s[6:]}"
        return self._fresh(mk)

    def medicare(self) -> str:
        r = self.rng

        def mk():
            d = [r.randint(2, 6)] + [r.randint(0, 9) for _ in range(7)]
            s = "".join(map(str, d)) + str(medicare_check_digit(d)) + str(r.randint(1, 9))
            return f"{s[:4]} {s[4:9]} {s[9]}"
        return self._fresh(mk)

    def abn(self) -> str:
        r = self.rng

        def mk():
            while True:
                tail = [r.randint(0, 9) for _ in range(9)]
                for d1 in range(1, 10):
                    for d2 in range(10):
                        s = "".join(map(str, [d1, d2] + tail))
                        if abn_valid(s) and s not in V.REAL_ABNS:
                            return f"{s[:2]} {s[2:5]} {s[5:8]} {s[8:]}"
        return self._fresh(mk)

    def bank(self, kind: str | None = None) -> str:
        r = self.rng
        kind = kind or r.choice(["bsb", "bsb", "iban"])

        def mk():
            if kind == "bsb":
                return f"999-{r.randint(0, 999):03d} {r.randint(10_000_000, 99_999_999)}"
            cc, bank, sort_n, acct_n = r.choice(IBAN_SPECS)
            if cc == "DE":
                bban = "99999999" + "".join(str(r.randint(0, 9)) for _ in range(10))
            else:
                bban = bank + "".join(str(r.randint(0, 9)) for _ in range(sort_n + acct_n))
            return group(cc + iban_check_digits(cc, bban) + bban)
        return self._fresh(mk)

    def card(self) -> str:
        c = self.cards[self.card_i % len(self.cards)]
        self.card_i += 1
        if len(c) == 15:
            return f"{c[:4]} {c[4:10]} {c[10:]}"
        return group(c)

    def national_id(self, kind: str | None = None) -> str:
        r = self.rng
        kind = kind or r.choice(["ssn", "nino"])

        def mk():
            if kind == "ssn":
                return f"987-65-432{r.randint(0, 9)}"
            n = f"{r.randint(0, 999999):06d}"
            return f"QQ {n[:2]} {n[2:4]} {n[4:]} {r.choice('ABCD')}"
        if kind == "ssn":  # only ten SSNs exist in the block: allow reuse across cases but never inside one case
            return mk()
        return self._fresh(mk)

    def passport(self) -> str:
        r = self.rng
        return self._fresh(lambda: "".join(r.choice("PNEXKLMWY") for _ in range(r.choice([1, 2])))
                           + f"{r.randint(1_000_000, 9_999_999)}")

    def licence(self) -> str:
        r = self.rng
        def mk():
            n = r.choice([8, 8, 9, 10])
            return str(r.randint(10 ** (n - 1), 10 ** n - 1))
        return self._fresh(mk)

    def secret(self, kind: str | None = None) -> dict:
        r = self.rng
        kind = kind or r.choice(SECRET_KINDS[:6])
        alpha = string.ascii_letters + string.digits

        def mk():
            if kind == "PIN":
                return f"{r.randint(100000, 999999)}"
            if kind == "CVV":
                return f"{r.randint(100, 999)}"
            if kind in ("password", "database password"):
                n = r.randint(12, 18)
                body = "".join(r.choice(alpha + "-_") for _ in range(n - 1))
                return r.choice(string.ascii_letters) + body
            n = {"api key": 32, "access token": 40, "client secret": 36}.get(kind, 32)
            return r.choice(string.ascii_lowercase) + "".join(r.choice(string.ascii_lowercase + string.digits)
                                                                for _ in range(n - 1))
        for _ in range(1000):
            v = self._fresh(mk)
            if not V.VENDOR_SECRET_RE.search(v):
                return {"kind": kind, "value": v}
        raise RuntimeError("secret draw failed")


def selftest() -> None:
    """Cross-check this module's checksum code and generators against the validator (independent code)."""
    v = Values(1)
    for _ in range(300):
        t = v.tfn()
        assert tfn_valid(re.sub(r"\D", "", t)) and V.tfn_ok(re.sub(r"\D", "", t)), t
        m = re.sub(r"\D", "", v.medicare())
        assert V.medicare_ok(m)
        a = re.sub(r"\D", "", v.abn())
        assert abn_valid(a) and V.abn_ok(a) and a not in V.REAL_ABNS
        for kind in ("bsb", "iban"):
            b = v.bank(kind)
            if kind == "iban":
                assert iban_valid(b) and V.iban_ok(b), b
        c = v.card()
        assert V.luhn_ok(re.sub(r"\D", "", c)) and re.sub(r"\D", "", c) in V.TEST_CARDS
        assert V.ssn_ok(re.sub(r"\D", "", v.national_id("ssn")))
        assert V.nino_ok(v.national_id("nino"))
        for pk in ("mobile", "mobile_intl", "landline", "uk", "nanp"):
            assert V.phone_ok(v.phone(pk)), pk
        assert V.phone_ok(v.business_phone())
        assert V.domain_reserved(v.email().rsplit("@", 1)[1])
        assert re.fullmatch(r"[A-Z]{1,2}\d{7}", v.passport())
        assert 8 <= len(v.licence()) <= 10
        s = v.secret()
        assert not V.VENDOR_SECRET_RE.search(s["value"])
    assert tfn_valid("123456782") and not tfn_valid("123456783")
    print("safevalues self-test ok")


if __name__ == "__main__":
    selftest()