"""Per-case context for generator C: people, identifiers, dates, addresses and organisation look-alikes.

Every helper returns weave parts (plain strings, S(...) spans, N(...) hard negatives) so templates never count offsets.
"""
from __future__ import annotations

import datetime as dt
import random
import re
import unicodedata
from dataclasses import dataclass

from c_pools import (ACMA_MOBILES, CULTURES, HARBOURLINE, HL_SUBURBS, ORGS, POLICY_STATEMENTS, PRON, PUBLIC_EVENTS, QUASI_PROSE,
                     SENS, STATES, STREETS, SUBURBS, TEST_CARDS, make_abn, make_iban, make_medicare, make_nino, make_secret,
                     make_ssn, make_tfn)
from c_weave import N, S, hl_allowed

MONTHS = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
ORG_BY_KEY = {o["key"]: o for o in ORGS}
ORG_BY_KEY["hl"] = HARBOURLINE

# a stable, checksum-valid ABN for each invented organisation (institutional: a hard negative)
for _o in ORGS:
    _r = random.Random(f"abn|{_o['key']}")
    _a = make_abn(_r)
    _o["abn"] = f"{_a[:2]} {_a[2:5]} {_a[5:8]} {_a[8:]}"
    _o["abn_forms"] = [_o["abn"], _a]


def fold(s: str) -> str:
    s = s.replace("ł", "l").replace("Ł", "L").replace("đ", "d").replace("Đ", "D").replace("ß", "ss").replace("ø", "o")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^A-Za-z0-9]", "", s)


@dataclass
class Person:
    given: str
    family: str
    gender: str
    order: str
    culture: str
    nick: str | None = None
    script: str | None = None

    @property
    def full(self) -> str:
        return f"{self.given} {self.family}" if self.order == "gf" else f"{self.family} {self.given}"

    @property
    def initials(self) -> str:
        toks = self.full.split()
        return "".join(t[0] for t in toks if t[0].isupper())

    @property
    def pron(self) -> dict:
        return PRON[self.gender]

    def form(self, kind: str) -> str:
        if kind == "full":
            return self.full
        if kind == "given":
            return self.given
        if kind == "family":
            return self.family
        if kind == "nick":
            return self.nick or self.given
        if kind == "quoted":
            if not self.nick:
                return self.full
            return f'{self.given} "{self.nick}" {self.family}' if self.order == "gf" else f'{self.family} {self.given} "{self.nick}"'
        if kind == "initials":
            return self.initials
        if kind == "dotted_initials":
            return ".".join(self.initials) + "."
        if kind == "script":
            return self.script or self.full
        if kind == "family_comma":
            return f"{self.family.upper()}, {self.given}"
        if kind == "upper":
            return self.full.upper()
        if kind == "lower":
            return self.full.lower()
        raise ValueError(kind)


def fmt_date(d: dt.date, style: str) -> str:
    if style == "dmy":
        return f"{d.day:02d}/{d.month:02d}/{d.year}"
    if style == "dmy_loose":
        return f"{d.day}/{d.month}/{d.year}"
    if style == "dmy_short":
        return f"{d.day:02d}/{d.month:02d}/{d.year % 100:02d}"
    if style == "iso":
        return d.isoformat()
    if style == "long":
        return f"{d.day} {MONTHS[d.month]} {d.year}"
    if style == "abbr":
        return f"{d.day} {MONTHS[d.month][:3]} {d.year}"
    if style == "dotted":
        return f"{d.day:02d}.{d.month:02d}.{d.year}"
    if style == "us":
        return f"{MONTHS[d.month]} {d.day}, {d.year}"
    if style == "dash":
        return f"{d.day:02d}-{d.month:02d}-{d.year}"
    raise ValueError(style)


DATE_STYLES = ["dmy", "dmy_loose", "dmy_short", "iso", "long", "abbr", "dotted", "us", "dash"]


def expand(rng: random.Random, pat: str) -> str:
    """Expand a tiny regex subset (\\d, [..], {n}, {a,b}, escapes, literals) into one matching string."""
    out, i = [], 0
    while i < len(pat):
        ch = pat[i]
        choices = None
        if ch == "\\":
            nxt = pat[i + 1]
            i += 2
            choices = "0123456789" if nxt == "d" else nxt
        elif ch == "[":
            j = pat.index("]", i)
            cls, i = pat[i + 1:j], j + 1
            chars = []
            k = 0
            while k < len(cls):
                if k + 2 < len(cls) and cls[k + 1] == "-":
                    chars += [chr(x) for x in range(ord(cls[k]), ord(cls[k + 2]) + 1)]
                    k += 3
                else:
                    chars.append(cls[k])
                    k += 1
            choices = "".join(chars)
        else:
            i += 1
            out.append(ch)
            continue
        reps = 1
        if i < len(pat) and pat[i] == "{":
            j = pat.index("}", i)
            q = pat[i + 1:j]
            i = j + 1
            reps = rng.randint(*map(int, q.split(","))) if "," in q else int(q)
        out.extend(rng.choice(choices) for _ in range(reps))
    return "".join(out)


class Ctx:
    def __init__(self, rng: random.Random, org: dict, is_hl: bool):
        self.rng, self.org, self.hl = rng, org, is_hl
        self.used_names: set[str] = set()
        self.used_sens: set[str] = set()
        self.used_quasi: set[str] = set()
        self.people: list[Person] = []

    # --- people ---------------------------------------------------------------------------------------------------
    def person(self, culture: str | None = None, script: bool | None = None) -> Person:
        rng = self.rng
        for _ in range(50):
            cul = culture or rng.choice(list(CULTURES) + ["anglo", "anglo"])
            cd = CULTURES[cul]
            use_script = script if script is not None else (bool(cd.get("script")) and rng.random() < 0.5)
            if use_script and cd.get("script"):
                g, f, sc, gender = rng.choice(cd["script"])
                nick = next((n for (gg, _, n) in cd["given"] if gg == g), None)
                p = Person(g, f, gender, cd["order"], cul, nick, sc)
            else:
                g, gender, nick = rng.choice(cd["given"])
                f = rng.choice(cd["family"])
                p = Person(g, f, gender, cd["order"], cul, nick, None)
            if p.full not in self.used_names and p.given not in {q.given for q in self.people} and p.family not in {q.family for q in self.people}:
                self.used_names.add(p.full)
                self.people.append(p)
                return p
        raise RuntimeError("could not draw a distinct person")

    def P(self, p: Person, kind: str = "full"):
        return S("PERSON", p.form(kind))

    def Pn(self, p: Person):
        """The name as a form or letter might carry it: native script when the person has one, else the full name."""
        return self.P(p, "script" if (p.script and self.rng.random() < 0.7) else "full")

    def mailbox_like(self, *keywords):
        hits = [m for m in self.org["mailboxes"] if any(k in m.split("@")[0] for k in keywords)]
        return self.neg("role_mailbox", self.rng.choice(hits or self.org["mailboxes"]))

    def pr(self, p: Person, key: str) -> str:
        return p.pron[key]

    # --- contact details --------------------------------------------------------------------------------------------
    def staff_email(self, p: Person):
        g, f = fold(p.given).lower(), fold(p.family).lower()
        style = self.rng.choice(["dot", "dot", "initial"])
        local = f"{g}.{f}" if style == "dot" else f"{g[0]}{f}"
        return S("EMAIL", f"{local}@{self.org['domain']}")

    def learner_email(self, p: Person):
        return S("EMAIL", f"{fold(p.given).lower()}{fold(p.family)[0].lower()}{self.rng.randint(10, 99)}@learner.harbourline.example")

    def personal_email(self, p: Person):
        g, f = fold(p.nick or p.given).lower(), fold(p.family).lower()
        rng = self.rng
        dom = rng.choice(["example.com", "example.net", "example.org", "mail.example.com", "post.example"])
        local = rng.choice([f"{g}.{f}", f"{g}_{f}{rng.randint(70, 99)}", f"{f}{g[0]}{rng.randint(1, 99)}", f"{g}{rng.randint(1980, 2005)}", f"the.real.{g}"])
        return S("EMAIL", f"{local}@{dom}")

    def obfuscated_email(self, p: Person):
        g, f = fold(p.given).lower(), fold(p.family).lower()
        return S("EMAIL", self.rng.choice([f"{g} dot {f} at example dot com", f"{g}.{f} at example dot net", f"{g} underscore {f} at mail dot example dot com"]))

    def username(self, p: Person):
        g, f = fold(p.given).lower(), fold(p.family).lower()
        rng = self.rng
        return S("USERNAME", rng.choice([f"{g[0]}{f}", f"{g}.{f}", f"{g}_{f}{rng.randint(1, 99)}", f"{(p.nick or p.given).lower().replace(' ', '')}{rng.randint(10, 99)}",
                                          f"{f}{g[:2]}", f"{g}-{f}-dev", f"{g[0]}{f[:5]}{rng.randint(100, 999)}"]))

    def url_personal(self, p: Person):
        g, f = fold(p.given).lower(), fold(p.family).lower()
        rng = self.rng
        return S("URL_PERSONAL", rng.choice([
            f"https://linkedin.example/in/{g}-{f}-{rng.randint(10, 99)}", f"https://github.example/{g}{f[0]}", f"https://orcid.example/0000-000{rng.randint(1, 3)}-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}",
            f"https://{g}{f}.blog.example/about", f"https://instagram.example/{g}_{f}", f"https://www.example.com/~{g[0]}{f}", f"https://mastodon.example/@{g}{f[:3]}",
            f"https://{f}.example.org/{g}/portfolio", f"http://{g}-{f}.example.net"]))

    def mobile(self, style: str | None = None):
        rng = self.rng
        n = rng.choice(ACMA_MOBILES)  # "0491 570 006"
        d = n.replace(" ", "")
        style = style or rng.choice(["spaced", "spaced", "plain", "intl", "intl0", "dashed", "dotted", "digits", "words", "broken", "brackets"])
        if style == "spaced":
            v = n
        elif style == "plain":
            v = d
        elif style == "intl":
            v = f"+61 {d[1:4]} {d[4:7]} {d[7:]}"
        elif style == "intl0":
            v = f"+61 (0) {d[1:4]} {d[4:7]} {d[7:]}"
        elif style == "dashed":
            v = f"{d[:4]}-{d[4:7]}-{d[7:]}"
        elif style == "dotted":
            v = f"{d[:4]}.{d[4:7]}.{d[7:]}"
        elif style == "digits":
            v = " ".join(d)
        elif style == "words":
            words = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine"}
            v = " ".join(words[x] for x in d)
        elif style == "broken":
            v = f"{d[:4]} {d[4:7]}\n{d[7:]}"
        elif style == "brackets":
            v = f"({d[:4]}) {d[4:7]} {d[7:]}"
        else:
            raise ValueError(style)
        return S("PHONE", v)

    def landline(self):
        rng = self.rng
        area = rng.choice(["02", "03", "07", "08"])
        mid = rng.choice(["5550", "7010"])
        last = rng.randint(8000, 9999)
        v = rng.choice([f"({area}) {mid} {last}", f"{area} {mid} {last}", f"+61 {area[1]} {mid} {last}", f"{area}{mid}{last}"])
        return S("PHONE", v)

    def uk_phone(self):
        n = self.rng.randint(0, 999)
        return S("PHONE", self.rng.choice([f"07700 900{n:03d}", f"+44 7700 900{n:03d}", f"+44 (0)7700 900 {n:03d}"]))

    def us_phone(self):
        rng = self.rng
        a, n = rng.choice(["212", "415", "312", "617", "503"]), rng.randint(0, 99)
        return S("PHONE", rng.choice([f"({a}) 555-01{n:02d}", f"{a}-555-01{n:02d}", f"+1 {a} 555 01{n:02d}", f"{a}.555.01{n:02d}"]))

    def home_address(self, style: str | None = None):
        rng = self.rng
        suburb, pc = rng.choice(HL_SUBURBS if self.hl else SUBURBS)
        state = STATES[pc[0]]
        num = rng.randint(1, 180)
        unit = rng.choice(["", "", "", f"{rng.randint(1, 12)}/", f"Unit {rng.randint(1, 30)}, "])
        street = rng.choice(STREETS)
        style = style or rng.choice(["one", "one", "nostate", "multi", "short"])
        if style == "one":
            v = f"{unit}{num} {street}, {suburb} {state} {pc}"
        elif style == "nostate":
            v = f"{unit}{num} {street}, {suburb} {pc}"
        elif style == "multi":
            v = f"{unit}{num} {street}\n{suburb} {state} {pc}"
        else:
            v = f"{num} {street}, {suburb}"
        return S("ADDRESS", v)

    def suburb_cell(self):
        suburb, pc = self.rng.choice(HL_SUBURBS if self.hl else SUBURBS)
        return S("ADDRESS", self.rng.choice([pc, suburb, f"{suburb} {pc}"]))

    def postcode_cell(self):
        return S("ADDRESS", self.rng.choice(HL_SUBURBS if self.hl else SUBURBS)[1])

    def overseas_address(self, country: str):
        rng = self.rng
        if country == "US":
            v = rng.choice([f"{rng.randint(10, 999)} Mercer Lane, Apt {rng.randint(1, 40)}, Fallbrook Heights, WA 98000",
                            f"{rng.randint(10, 999)} Juniper Court, Larchmont Falls, OR 97000", f"PO Box {rng.randint(100, 9999)}, Cedar Hollow, VT 05000"])
        else:
            v = rng.choice([f"{rng.randint(1, 120)} Tanner's Row, Wrexley, LS99 9XX", f"Flat {rng.randint(1, 30)}, {rng.randint(1, 99)} Harrow Lane, Dunwick, EH99 9ZZ",
                            f"{rng.randint(1, 60)} Ferry Steps, Port Ellery, TR99 9YY"])
        return S("ADDRESS", v)

    # --- dates ------------------------------------------------------------------------------------------------------
    def date_obj(self, y0: int, y1: int) -> dt.date:
        return dt.date(self.rng.randint(y0, y1), self.rng.randint(1, 12), self.rng.randint(1, 28))

    def dob(self, style: str | None = None, y0: int = 1950, y1: int = 2007):
        return S("DATE_OF_BIRTH", fmt_date(self.date_obj(y0, y1), style or self.rng.choice(DATE_STYLES)))

    def odate(self, style: str | None = None, y0: int = 2025, y1: int = 2027):
        return S("OTHER_DATE", fmt_date(self.date_obj(y0, y1), style or self.rng.choice(DATE_STYLES)))

    def weekday(self):
        return self.neg("weekday", self.rng.choice(WEEKDAYS))

    def plain_date(self, style: str = "long", y0: int = 2026, y1: int = 2027) -> str:
        return fmt_date(self.date_obj(y0, y1), style)

    # --- identifiers (gold) ------------------------------------------------------------------------------------------
    def tfn(self, style: str | None = None):
        d = make_tfn(self.rng)
        style = style or self.rng.choice(["spaced", "spaced", "plain", "dotted", "zw", "fullwidth", "broken"])
        if style == "spaced":
            v = f"{d[:3]} {d[3:6]} {d[6:]}"
        elif style == "plain":
            v = d
        elif style == "dotted":
            v = f"{d[:3]}.{d[3:6]}.{d[6:]}"
        elif style == "zw":
            v = "​".join([d[:3], d[3:6], d[6:]])
        elif style == "fullwidth":
            v = "".join(chr(ord(ch) - ord("0") + ord("０")) for ch in d)
        else:
            v = f"{d[:3]} {d[3:6]}\n{d[6:]}"
        return S("TFN", v)

    def _ten_digits_ok(self, d: str) -> bool:
        """Avoid 10-digit identifiers that a text scan would read as a US 555 or 1300/1800 number."""
        return not (re.fullmatch(r"\d{3}555\d{4}", d) or re.match(r"1[38]00\d{6}$", d))

    def medicare(self):
        d = make_medicare(self.rng)
        while not self._ten_digits_ok(d):
            d = make_medicare(self.rng)
        return S("MEDICARE", self.rng.choice([f"{d[:4]} {d[4:9]} {d[9]}", d, f"{d[:4]}-{d[4:9]}-{d[9]}", f"{d[:4]} {d[4:9]} {d[9]}"]))

    def abn(self):
        d = make_abn(self.rng)
        return S("ABN", self.rng.choice([f"{d[:2]} {d[2:5]} {d[5:8]} {d[8:]}", d, f"{d[:2]} {d[2:5]} {d[5:8]} {d[8:]}"]))

    def national_id(self, kind: str | None = None):
        kind = kind or self.rng.choice(["ssn", "nino"])
        return S("NATIONAL_ID", make_ssn(self.rng) if kind == "ssn" else make_nino(self.rng))

    def licence(self):
        n = self.rng.randint(8, 10)
        while True:
            v = str(self.rng.randint(1, 9)) + "".join(str(self.rng.randint(0, 9)) for _ in range(n - 1))
            if self._ten_digits_ok(v):
                return S("DRIVER_LICENCE", v)

    def passport(self):
        rng = self.rng
        return S("PASSPORT", rng.choice(["PA", "N", "E", "RA", "M"]) + "".join(str(rng.randint(0, 9)) for _ in range(7)))

    def bsb_account(self, split: bool = False):
        rng = self.rng
        bsb = f"{rng.choice(['999', '980', '990'])}-{rng.randint(100, 999)}"
        acct = str(rng.randint(1, 9)) + "".join(str(rng.randint(0, 9)) for _ in range(rng.choice([6, 7, 8])))
        if split:
            return S("BANK_ACCOUNT", bsb), S("BANK_ACCOUNT", acct)
        return S("BANK_ACCOUNT", f"{bsb} {acct}")

    def iban(self):
        v = make_iban(self.rng)
        if self.rng.random() < 0.5:
            v = " ".join(v[i:i + 4] for i in range(0, len(v), 4))
        return S("BANK_ACCOUNT", v)

    def card(self):
        d = self.rng.choice(TEST_CARDS)
        if len(d) == 16:
            v = self.rng.choice([f"{d[:4]} {d[4:8]} {d[8:12]} {d[12:]}", d, f"{d[:4]}-{d[4:8]}-{d[8:12]}-{d[12:]}"])
        elif len(d) == 15:
            v = self.rng.choice([f"{d[:4]} {d[4:10]} {d[10:]}", d])
        else:
            v = self.rng.choice([f"{d[:4]} {d[4:10]} {d[10:]}", d])
        return S("PAYMENT_CARD", v)

    def secret(self, kind: str):
        return S("SECRET", make_secret(self.rng, kind))

    def staff_id(self):
        pre, n = self.org["staff_id"]
        return S("INTERNAL_ID", pre + "".join(str(self.rng.randint(0, 9)) for _ in range(n)))

    def customer_id(self):
        if self.hl:
            return S("INTERNAL_ID", self.rng.choice("56") + "".join(str(self.rng.randint(0, 9)) for _ in range(7)))
        pre, n = self.org["customer_id"]
        return S("INTERNAL_ID", pre + "".join(str(self.rng.randint(0, 9)) for _ in range(n)))

    def participant_code(self):
        return S("INTERNAL_ID", f"P-{self.rng.randint(100, 999)}-{self.rng.choice('ABCDEFGHJKLMNPRSTUVWXYZ')}{self.rng.choice('ABCDEFGHJKLMNPRSTUVWXYZ')}")

    def other_id(self, kind: str):
        rng = self.rng
        if kind == "mrn":
            return S("INTERNAL_ID", f"MRN {rng.randint(100000, 999999)}")
        if kind == "frequent_flyer":
            return S("INTERNAL_ID", f"FF{rng.randint(10000000, 99999999)}")
        if kind == "member":
            return S("INTERNAL_ID", f"M-{rng.randint(100000, 999999)}")
        if kind == "patient":
            return S("INTERNAL_ID", f"UR{rng.randint(1000000, 9999999)}")
        raise ValueError(kind)

    # --- sensitive statements -----------------------------------------------------------------------------------------
    def sens(self, label: str, p: Person | None = None, kind: str = "pred"):
        pool = SENS[label]
        key = kind
        if label == "SEXUALITY" and kind == "pred":
            key = "pred_f" if (p is None or p.gender == "f") else "pred_m"
        choices = [x for x in pool[key] if (label, x) not in self.used_sens] or pool[key]
        if p is None:  # no person to agree with: prefer phrases without pronouns
            choices = [x for x in choices if "{" not in x] or choices
        phrase = self.rng.choice(choices)
        self.used_sens.add((label, phrase))
        pron = (p.pron if p else PRON["f"])
        return S(label, phrase.format(**pron))

    def sens_cell(self, label: str):
        return self.sens(label, None, "cell")

    def quasi(self, site_form: str | None = None):
        choices = [q for q in QUASI_PROSE if q not in self.used_quasi] or QUASI_PROSE
        q = self.rng.choice(choices)
        self.used_quasi.add(q)
        site = site_form or self.site()["forms"][0]
        return S("QUASI_ID", q.format(site=site))

    # --- organisation look-alikes (hard negatives) --------------------------------------------------------------------
    def neg(self, kind: str, value: str):
        """A hard negative; in Harbourline cases only profile values are annotated, anything else stays plain text."""
        if self.hl and not hl_allowed(value):
            return value
        return N(kind, value)

    def org_name(self, i: int | None = None):
        forms = self.org["forms"]
        return self.neg("organisation", forms[i] if i is not None else self.rng.choice(forms))

    def org_abn(self):
        return self.neg("organisation_abn", self.rng.choice(self.org["abn_forms"]))

    def site(self) -> dict:
        return self.rng.choice(self.org["sites"])

    def site_form(self, site: dict | None = None):
        site = site or self.site()
        return self.neg("site", self.rng.choice(site["forms"]))

    def site_address(self, site: dict | None = None):
        site = site or self.site()
        return self.neg("site_address", site["address"])

    def reception(self, site: dict | None = None):
        site = site or self.site()
        return self.neg("reception_phone", site["reception"])

    def line(self):
        purpose, num = self.rng.choice(self.org["lines"])
        return purpose, self.neg("business_phone", num)

    def mailbox(self):
        return self.neg("role_mailbox", self.rng.choice(self.org["mailboxes"]))

    def role(self):
        return self.neg("role_title", self.rng.choice(self.org["roles"]))

    def building(self):
        return self.neg("building", self.rng.choice(self.org["buildings"]))

    def product(self):
        code, title = self.rng.choice(self.org["products"])
        ck, tk = ("programme_code", "programme_title") if self.hl else ("product_code", "service_name")
        return self.neg(ck, code), self.neg(tk, title)

    def project(self):
        code, title = self.rng.choice(self.org["projects"])
        return self.neg("project_code", code), self.neg("project_title", title)

    def committee(self):
        return self.neg("committee", self.rng.choice(self.org["committees"]))

    def initiative(self):
        return self.neg("initiative", self.rng.choice(self.org["initiatives"]))

    def policy_statement(self):
        return self.neg("policy_statement", self.rng.choice(POLICY_STATEMENTS))

    def public_event(self):
        return self.neg("public_event", self.rng.choice(PUBLIC_EVENTS))

    def public_date(self):
        event, date = self.rng.choice(self.org["public_dates"])
        return self.neg("event_name", event), self.neg("public_date", date)

    def ref(self, key: str, kind: str = "reference_number"):
        pat = self.org["refs"][key]
        return self.neg(kind, expand(self.rng, pat))

    def any_ref(self, kind: str = "reference_number"):
        key = self.rng.choice(list(self.org["refs"]))
        return self.ref(key, kind)

    def lookalike(self):
        r = self.rng.random()
        if r < 0.4:
            return self.committee()
        if r < 0.8:
            return self.initiative()
        return self.policy_statement() if not self.hl else self.initiative()

    def service_account(self):
        return self.neg("service_name", self.rng.choice(["svc_backup", "svc_ci_runner", "svc_booking", "svc_reporting", "deploy-bot", "svc_scheduler", "ldap-sync"]))
