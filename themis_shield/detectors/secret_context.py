"""v0.3 secret-rule context: values that look high-entropy or sit after a credential word but are not secrets.

First real-document run (v0.3 pre-freeze): most SECRET holds were not secrets. Each class below is a named switch
(``detectors/settings.py``) and never removes protection from a value: a value that is not a secret is still
substituted as a generic identifier (INTERNAL_ID), a passport or a person, unless it is plainly not identifying
(slash-joined words in a URL path, run-together words), where the rules simply do not emit it.

- ``secret-url-components``: a high-entropy string inside a URL is a secret only when it is the value of a query or
  fragment parameter whose name says so (``token``, ``access_token``, ``password``, ``key``, ``sig``, ``session``,
  ``code`` beside ``state``, ...). Any other query value is an identifier (it can identify the recipient); a path
  segment is an identifier, or nothing when it is only words and numbers.
- ``secret-prose-values``: "password <value>" / "PIN <value>" in prose needs a credential-shaped value: digits for a
  PIN; for a password, not a plain word followed by sentence punctuation ("have your PIN handy.").
- ``secret-auth-codes``: a short code after "AUTH", "APPROVED", "approval code" (card receipts) or "promo code" /
  "voucher code" is a transaction code, not a secret.
- ``secret-document-ids``: a GUID, a long upper-case or hexadecimal identifier after "Document ID", "Envelope ID",
  "Transaction", "Receipt number" and similar labels, and a long all-digit string, are identifiers.
- ``secret-org-codes``: a SWIFT/BIC code after "SWIFT" or "BIC", a biller code, and a bank reference that starts with
  a BIC and a date are organisation codes; run-together words ("AmountDescriptionSupplier") are not a secret.
- ``secret-mrz``: a machine-readable zone of a passport or identity card (lines of equal length made of A-Z, 0-9 and
  ``<``, with a valid date check digit) is passport and name material, not a secret.
"""

from __future__ import annotations

import re

from ..types import Label

# ----------------------------------------------------------------------------------------------------------------------
# URLs
# ----------------------------------------------------------------------------------------------------------------------
URL_ANY = re.compile(r"(?i)\bhttps?://[^\s<>\"'()\[\]{}]+")
SECRET_PARAM_WORDS = frozenset({
    "token", "password", "passwd", "pwd", "pass", "passcode", "secret", "key", "apikey", "sig", "signature", "auth",
    "authorization", "session", "sessionid", "sid", "credential", "credentials", "jwt", "bearer", "otp", "pin",
    "hmac", "hash", "nonce", "ticket", "assertion", "samlresponse",
})


def secret_parameter(name: str, query: str) -> bool:
    """A query or fragment parameter name that marks its value as a credential."""
    name = name.casefold()
    parts = [part for part in re.split(r"[_\-.]", name) if part]
    if name in SECRET_PARAM_WORDS or (parts and parts[-1] in SECRET_PARAM_WORDS):
        return True
    if name in ("code", "auth_code", "authcode"):
        # An authorisation code is a credential when it travels with an OAuth state.
        return bool(re.search(r"(?i)(?:^|[?&#;])state=", query))
    return False


def url_component(view: str, start: int, end: int) -> str | None:
    """``"secret"``, ``"query"`` or ``"path"`` for a value inside a URL; ``None`` outside any URL."""
    for match in URL_ANY.finditer(view):
        if match.start() <= start and end <= match.end():
            url = match.group()
            rel = start - match.start()
            marks = [i for i in (url.find("?"), url.find("#")) if i >= 0]
            first = min(marks) if marks else -1
            if first < 0 or rel <= first:
                return "path"
            query = url[first:]
            before = url[:rel]
            cut = max(before.rfind(ch) for ch in "?&#;")
            pair = before[cut + 1:]
            if "=" not in pair:
                return "query"
            return "secret" if secret_parameter(pair.split("=", 1)[0], query) else "query"
        if match.start() > end:
            break
    return None


def words_only_path(value: str) -> bool:
    """Slash-joined plain words or numbers ("net/promos/fonts/Sherpa", "365/commerce/cancel"): not identifying."""
    pieces = [piece for piece in value.split("/") if piece]
    return len(pieces) >= 2 and all(
        re.fullmatch(r"[A-Za-z]+(?:-[A-Za-z]+)*|[0-9]{1,6}", piece) for piece in pieces
    )


# ----------------------------------------------------------------------------------------------------------------------
# Prose
# ----------------------------------------------------------------------------------------------------------------------
def credential_shaped(keyword: str, value: str) -> bool:
    """A value after "PIN" must be digits; after "password"/"pwd" it must not be a plain word ending a sentence."""
    if keyword.casefold() == "pin":
        return bool(re.fullmatch(r"[0-9]{4,12}", value))
    return not re.fullmatch(r"[A-Za-z]+[.!?,:;]+", value)


# ----------------------------------------------------------------------------------------------------------------------
# Context classes on a SECRET finding (any stage)
# ----------------------------------------------------------------------------------------------------------------------
AUTH_CONTEXT = re.compile(
    r"(?i)(?:\b(?:auth(?:ori[sz]ation)?|appr(?:oval)?|approved)\s*(?:code|no\.?|number|num|#)?"
    r"|\b(?:promo(?:tion(?:al)?)?|voucher|coupon|discount)\s*code)\s*[:#]?\s*$"
)
ID_CONTEXT = re.compile(
    r"(?i)\b(?:(?:document|envelope|transaction|txn|receipt|payment|order|request|message|reference|ref|booking|"
    r"confirmation|invoice|reservation)\s*(?:id|no\.?|number|num|#|ref(?:erence)?|code)|transaction|envelope)"
    r"\s*[:#]?\s*$"
)
SWIFT_CONTEXT = re.compile(r"(?i)\b(?:swift|bic)(?:\s*(?:/\s*bic|code|address|no\.?))?\s*[:#]?\s*$")
BILLER_CONTEXT = re.compile(r"(?i)\bbiller\s*(?:code|id|no\.?)\s*[:#]?\s*$")
BIC = r"[A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?"
BIC_VALUE = re.compile(BIC + r"(?:\s[A-Z0-9]{3})?")
BIC_REFERENCE = re.compile(BIC + r"[A-Z]?20[0-9]{2}(?:0[1-9]|1[0-2])(?:[0-2][0-9]|3[01])[0-9]{6,}")
GUID = re.compile(r"[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}")
UPPER_ID = re.compile(r"(?=[A-Z0-9_\-]*[0-9])(?=[A-Z0-9_\-]*[A-Z])[A-Z0-9_\-]{12,}")
HEX_ID = re.compile(r"[0-9a-fA-F]{16,}")
WORD_MASH = re.compile(r"(?:[A-Z][a-z]{2,}){3,}")
MRZ_LENGTHS = (30, 36, 44)
_MRZ_LINE = re.compile(r"[A-Z0-9<]+")
_TOKEN_EDGE = ".,;:!?)]}'\"([{<>"


def _line(text: str, start: int, end: int) -> tuple[str, str, str]:
    """(line text before the whitespace-delimited token holding the span, that token, the whole line)."""
    line_start = max(text.rfind("\n", 0, start), text.rfind("\r", 0, start)) + 1
    ends = [i for i in (text.find("\n", end), text.find("\r", end)) if i >= 0]
    line_end = min(ends) if ends else len(text)
    line = text[line_start:line_end]
    token_start = start
    while token_start > line_start and not text[token_start - 1].isspace():
        token_start -= 1
    token_end = end
    while token_end < line_end and not text[token_end].isspace():
        token_end += 1
    return text[line_start:token_start], text[token_start:token_end].strip(_TOKEN_EDGE), line


def _mrz_check(field: str) -> int:
    total = 0
    for index, character in enumerate(field):
        value = int(character) if character.isdigit() else (
            0 if character == "<" else ord(character) - 55
        )
        total += value * (7, 3, 1)[index % 3]
    return total % 10


def mrz_line(text: str, start: int, end: int) -> str | None:
    """``"name"`` or ``"data"`` when the span lies in a machine-readable zone; ``None`` otherwise.

    A zone is two (TD2, TD3) or three (TD1) consecutive lines of equal length 36, 44 or 30, made of A-Z, 0-9 and
    ``<``, with ``<<`` somewhere and a valid check digit on the date of birth. The name is on the last TD1 line and on
    the first TD2/TD3 line.
    """
    lines = [(m.start(), m.end(), m.group().strip()) for m in re.finditer(r"[^\r\n]+", text)]
    index = next((i for i, (a, b, _) in enumerate(lines) if a <= start and end <= b), None)
    if index is None:
        return None
    for size in (3, 2):
        for first in range(max(0, index - size + 1), index + 1):
            rows = [row for _, _, row in lines[first:first + size]]
            if len(rows) < size:
                continue
            length = len(rows[0])
            if length not in MRZ_LENGTHS or any(len(row) != length or not _MRZ_LINE.fullmatch(row) for row in rows):
                continue
            if not any("<<" in row for row in rows):
                continue
            data = rows[1]
            dob = data[0:6] if length == 30 else data[13:19]
            check = data[6] if length == 30 else data[19]
            if not (dob.isdigit() and check.isdigit() and _mrz_check(dob) == int(check)):
                continue
            name_row = first + (2 if size == 3 else 0)
            return "name" if index == name_row else "data"
    return None


def secret_relabel(text: str, start: int, end: int, fixes) -> frozenset[Label] | None:
    """Labels that replace SECRET on a finding, or ``None`` to keep SECRET. ``fixes`` is the set of active names."""
    before, token, _ = _line(text, start, end)
    value = text[start:end].strip(_TOKEN_EDGE)
    if "secret-url-components" in fixes:
        where = url_component(text, start, end)
        if where == "secret":
            return None
        if where in ("query", "path"):
            return frozenset({Label.INTERNAL_ID})
    if "secret-auth-codes" in fixes and re.fullmatch(r"[A-Za-z0-9]{3,12}", value) and AUTH_CONTEXT.search(before):
        return frozenset({Label.INTERNAL_ID})
    if "secret-document-ids" in fixes:
        if ID_CONTEXT.search(before) and (
            GUID.search(token) or UPPER_ID.fullmatch(token) or HEX_ID.fullmatch(token)
            or UPPER_ID.fullmatch(value) or HEX_ID.fullmatch(value)
        ):
            return frozenset({Label.INTERNAL_ID})
        if re.fullmatch(r"[0-9]{20,}", value):
            return frozenset({Label.INTERNAL_ID})
    if "secret-org-codes" in fixes:
        if SWIFT_CONTEXT.search(before) and BIC_VALUE.fullmatch(text[start:end].strip()):
            return frozenset({Label.INTERNAL_ID})
        if BILLER_CONTEXT.search(before) and re.fullmatch(r"[0-9]{3,10}", value):
            return frozenset({Label.INTERNAL_ID})
        if BIC_REFERENCE.fullmatch(value):
            return frozenset({Label.INTERNAL_ID})
        if WORD_MASH.fullmatch(value):
            return frozenset({Label.INTERNAL_ID})
    if "secret-mrz" in fixes:
        kind = mrz_line(text, start, end)
        if kind == "name":
            return frozenset({Label.PERSON, Label.PASSPORT})
        if kind == "data":
            return frozenset({Label.PASSPORT})
    return None


# ----------------------------------------------------------------------------------------------------------------------
# v0.4 fix "secret-references": every line a SECRET finding touches either assigns only a reference to where a secret
# lives (an environment lookup, a template, a secret-manager path, a placeholder) or holds no value at all. Split D's
# clean configuration files were all held this way: the finders label "SECRET_KEY = os.environ['SECRET_KEY']" a
# secret although the value is never in the text. A line with a literal value is never a reference, so a real
# secret on such a line keeps its label; so does any line carrying a long mixed token.
# ----------------------------------------------------------------------------------------------------------------------
_NAME = r"[A-Za-z_][A-Za-z0-9_.\-]{0,126}"
_QUOTED_NAME = r"""["'][A-Za-z0-9_./:#@\-]{1,200}["']"""
ENV_REFERENCE = re.compile(
    r"""(?x)
    (?:os\.environ|ENV|env)\s*\[\s*""" + _QUOTED_NAME + r"""\s*\]           # os.environ['X'], ENV["X"]
    | (?:os\.environ\.get|os\.getenv|System\.getenv|Environment\.GetEnvironmentVariable|getenv)
      \s*\(\s*""" + _QUOTED_NAME + r"""\s*(?:,\s*(?:None|null|""|''))?\s*\)  # os.getenv('X'), never with a default
    | process\.env\.""" + _NAME + r"""                                     # process.env.X
    | (?:var|local)\.""" + _NAME + r"""                                     # Terraform var.x, local.x
    | \{\{\s*lookup\([^{}()]{1,200}\)\s*\}\}                               # {{ lookup('vault', '...', 'password') }}
    | \{\{\s*""" + _NAME + r"""\s*\}\}                                     # {{secrets.db_password}}
    | %\(\s*(?:env:)?""" + _NAME + r"""\s*\)[sdr]                          # %(env:DB_PASSWORD)s
    | (?:vault|ssm|kv|keyvault|secret|secrets):[A-Za-z0-9_./#@\-]{1,200}  # vault:secret/data/reports#webhook
    | <\s*(?:your|insert|enter|set|replace|add|put)[A-Za-z0-9 _\-]{0,60}>   # <your-api-key>, <insert token>
    | (?i:replace[_\-]?me|change[_\-]?me|todo|tbd|x{4,}|dummy|none|null|nil|empty|"")
    """
)
_ASSIGNMENT = re.compile(r"""^\s*(?:export\s+|-\s+)?["']?[A-Za-z0-9_.\-]{1,120}["']?\s*(?::|=|:=)\s*(?P<value>.*)$""")
_DECLARATION = re.compile(r"""^\s*(?:variable|output|secret|resource|data)\s+"[^"]{1,120}"(?:\s+"[^"]{1,120}")?\s*\{\s*$""")
_TRAILING = re.compile(r"""\s+(?:#|//).*$""")
_URL_PASSWORD = re.compile(
    r"""^[a-z][a-z0-9+.\-]{1,20}://[^:/@\s]{1,80}:(?P<password>\{\{[^{}]{1,240}\}\}|\$\{\{?[^{}]{1,120}\}?\}|[^@\s]{1,240})@"""
)
_LONG_MIXED = re.compile(r"(?=[A-Za-z0-9_\-+/=]*[0-9])(?=[A-Za-z0-9_\-+/=]*[A-Za-z])[A-Za-z0-9_\-+/=]{16,}")


def _reference(value: str) -> bool:
    from .rules import PLACEHOLDER

    value = _TRAILING.sub("", value).strip().rstrip(",;").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    if not value:
        return True  # a key with no value carries no secret
    if ENV_REFERENCE.fullmatch(value) or PLACEHOLDER.fullmatch(value):
        return True
    url = _URL_PASSWORD.match(value)
    return bool(url) and _reference(url.group("password"))


def references_only(text: str, start: int, end: int) -> bool:
    """True when every non-blank line the span touches is a reference-only assignment or holds no value at all."""
    line_start = max(text.rfind("\n", 0, start), text.rfind("\r", 0, start)) + 1
    ends = [i for i in (text.find("\n", max(start, end - 1)), text.find("\r", max(start, end - 1))) if i >= 0]
    line_end = min(ends) if ends else len(text)
    lines = [line for line in re.split(r"\r\n|\r|\n", text[line_start:line_end]) if line.strip()]
    if not lines:
        return False
    for line in lines:
        if _DECLARATION.match(line):
            continue
        assignment = _ASSIGNMENT.match(line)
        if assignment:
            if not _reference(assignment.group("value")):
                return False
            continue
        # Not an assignment ("import os", a block brace): a reference only if it carries no long mixed token.
        if _LONG_MIXED.search(line) or re.search(r"""["'][^"'\s]{8,}["']""", line):
            return False
    return True


def relabel_secrets(text: str, findings, fixes) -> tuple:
    """Replace SECRET on findings whose context says they are not secrets; every other label is kept."""
    from dataclasses import replace

    from .contracts import FindingEvidence

    out = []
    for item in findings:
        if Label.SECRET in item.labels and "secret-references" in fixes and references_only(text, item.start, item.end):
            # Not a secret, and nothing personal either: keep any other label the finding carries, else drop it.
            others = item.labels - {Label.SECRET}
            if not others:
                continue
            item = replace(item, labels=frozenset(others), primary_label=min(others, key=lambda label: label.value),
                           evidence=tuple(e for e in item.evidence if e.label != Label.SECRET))
        if Label.SECRET in item.labels:
            new = secret_relabel(text, item.start, item.end, fixes)
            if new is not None:
                labels = (item.labels - {Label.SECRET}) | new
                evidence = []
                for e in item.evidence:
                    if e.label == Label.SECRET:
                        evidence.extend(FindingEvidence(label, e.stage, e.tier, e.score) for label in sorted(new))
                    else:
                        evidence.append(e)
                seen, unique = set(), []
                for e in evidence:
                    key = (e.label, e.stage, e.tier)
                    if key not in seen:
                        seen.add(key)
                        unique.append(e)
                item = replace(
                    item, labels=frozenset(labels), primary_label=min(labels, key=lambda label: label.value),
                    evidence=tuple(unique),
                )
        out.append(item)
    return tuple(out)
