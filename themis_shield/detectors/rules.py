"""Narrow stdlib checksum/contact/credential backstop, grouped into rule packs.

Rule packs:

- ``core`` (always on): email addresses, personal URLs, API keys, hex/base64
  secrets, private keys, secret assignments and "phone: NNNN NNNN" forms.
- ``au``: Australian identifiers with checksums (TFN, Medicare, ABN, USI) and
  Australian phone number formats.
- ``intl``: international E.164 phone numbers and IBANs (mod-97 checksum); with the v0.3
  ``rule-completeness`` fix, payment cards (issuer prefix, length and Luhn).

The default is ``core`` only; deployments opt in to regional packs with
``[detection] rule_packs`` (or the ``THEMIS_RULE_PACKS`` environment variable,
comma-separated). Checksum formulas follow the public specifications of each
identifier; there is no runtime import of any data generator or gold
annotations.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
from threading import Lock
import unicodedata

from ..normalise import detection_view, strip_default_ignorable
from ..types import Label
from .contracts import StageReport, StageResult, Status, finding
from .secret_context import WORD_MASH, credential_shaped, url_component, words_only_path
from .settings import fix_on, v02


USI_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
TFN_WEIGHTS = (1, 4, 3, 7, 5, 8, 6, 9, 10)
MEDICARE_WEIGHTS = (1, 3, 7, 9, 1, 3, 7, 9)
ABN_WEIGHTS = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)
CHECKSUM_LABELS = frozenset({Label.USI, Label.TFN, Label.MEDICARE, Label.ABN})
SEPARATORS = r"[\s.\-]*"
RULE_PACKS = ("core", "au", "intl")
DEFAULT_RULE_PACKS = ("core",)
RULE_PACKS_ENV = "THEMIS_RULE_PACKS"


@dataclass(frozen=True, slots=True)
class ChecksumResult:
    label: Label
    format_valid: bool
    checksum_valid: bool


def _compact(value: str) -> str:
    value = unicodedata.normalize("NFKC", strip_default_ignorable(value)).upper()
    return re.sub(r"[\s.\-]", "", value)


def usi_check_char(body9: str) -> str:
    if len(body9) != 9 or any(ch not in USI_ALPHABET for ch in body9):
        raise ValueError("invalid-usi-body")
    factor, total = 2, 0
    for character in reversed(body9):
        addend = USI_ALPHABET.index(character) * factor
        total += addend // 32 + addend % 32
        factor = 3 - factor
    return USI_ALPHABET[(32 - total % 32) % 32]


def validate_identifier(label: Label | str, value: str) -> ChecksumResult:
    label = Label(label)
    compact = _compact(value)
    valid_format = valid_checksum = False
    if label == Label.USI:
        valid_format = (
            len(compact) == 10
            and all(ch in USI_ALPHABET for ch in compact)
        )
        valid_checksum = valid_format and usi_check_char(compact[:9]) == compact[9]
    elif label == Label.TFN:
        valid_format = bool(re.fullmatch(r"[0-9]{9}", compact))
        valid_checksum = valid_format and (
            sum(int(a) * w for a, w in zip(compact, TFN_WEIGHTS)) % 11 == 0
        )
    elif label == Label.MEDICARE:
        valid_format = bool(re.fullmatch(r"[2-6][0-9]{9,10}", compact))
        valid_checksum = valid_format and (
            sum(int(compact[i]) * MEDICARE_WEIGHTS[i] for i in range(8)) % 10
            == int(compact[8])
            and compact[9] != "0"
        )
    elif label == Label.ABN:
        valid_format = bool(re.fullmatch(r"[1-9][0-9]{10}", compact))
        if valid_format:
            digits = [int(ch) for ch in compact]
            digits[0] -= 1
            valid_checksum = (
                sum(a * w for a, w in zip(digits, ABN_WEIGHTS)) % 89 == 0
            )
    else:
        raise ValueError("unsupported-checksum-label")
    return ChecksumResult(label, valid_format, bool(valid_checksum))


def normalised_view(text: str) -> tuple[str, tuple[tuple[int, int], ...]]:
    """NFKC detection view with conservative original-character back-mapping.

    Each expanded character points to its complete source character. Every
    Unicode default-ignorable code point (zero-width characters, soft hyphen,
    bidi controls, variation selectors, tag characters) is omitted from the
    view only, not from the source; see :func:`themis_shield.normalise.detection_view`.
    """
    view = detection_view(text)
    return view.text, view.offsets


NUMERIC = re.compile(
    rf"(?<!\w)[0-9](?:{SEPARATORS}[0-9]){{8,10}}(?!\w)"
)
USI_PATTERN = re.compile(
    rf"(?<!\w)[{USI_ALPHABET}](?:{SEPARATORS}[{USI_ALPHABET}]){{9}}(?!\w)",
    re.I,
)
EMAIL = re.compile(
    r"(?<![\w.+@-])[\w.!#$%&'*+/=?^`{|}~-]+@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}(?![\w@-])"
)
PHONE = re.compile(
    r"(?<!\w)(?:"
    r"(?:04[0-9]{2}|\(04[0-9]{2}\)|"
    r"\+?61[\s.-]*(?:\(0\)[\s.-]*)?4[0-9]{2})"
    r"[\s.-]*[0-9]{3}[\s.-]*[0-9]{3}"
    r"|(?:\(0[2378]\)|0[2378]|\+?61[\s.-]*(?:\(0\)[\s.-]*)?[2378])"
    r"[\s.-]*[0-9]{4}[\s.-]*[0-9]{4}"
    r"|1[38]00[\s.-]*[0-9]{3}[\s.-]*[0-9]{3}"
    r"|13[\s.-]*[0-9]{2}[\s.-]*[0-9]{2}"
    r")(?!\w)"
)
LOCAL_PHONE = re.compile(
    r"(?i)\b(?:phone|tel|telephone|ph|call)\s*[:=]?\s*"
    r"(?P<number>[0-9]{4}[ -][0-9]{4})(?!\w)"
)
URL = re.compile(
    r"(?i)(?<![\w@])(?:https?://[^\s<>\"']+|"
    r"(?:www\.)?(?:linkedin\.com/in|facebook\.com|instagram\.com|"
    r"github\.com|tiktok\.com|youtube\.com)/[^\s<>\"']+|"
    r"[A-Za-z0-9-]+\.(?:wordpress|blogspot)\.com(?:/[^\s<>\"']*)?)"
)
KEY = re.compile(
    r"(?<!\w)(?:sk[-_]|pk[-_]|ghp_|github_pat_|xox[bp]-|AKIA|nvt_live_)"
    r"[A-Za-z0-9_-]{16,}(?!\w)"
)
HEX_SECRET = re.compile(r"(?<!\w)[0-9a-fA-F]{32,64}(?!\w)")
B64_SECRET = re.compile(
    r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{30,}={0,2}(?![A-Za-z0-9+/])"
)
PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"
)
SECRET_ASSIGNMENT = re.compile(
    r"""(?ix)
    (?<!\w)["']?
    (?:[a-z0-9]+[_-])*
    (?:password|passwd|pwd|pin|secret|token|api[_-]?key|access[_-]?key)
    ["']?\s*[:=]\s*
    (?:"(?P<double>[^"\r\n]+)"|'(?P<single>[^'\r\n]+)'|
        (?P<bare>[^\s,;)\]}]+))
    """
)
SECRET_PROSE = re.compile(
    r"(?i)\b(?:password|pwd|pin)\s+(?:is\s+)?"
    r"(?P<value>[^\s,;]{4,40})"
)


# ----------------------------------------------------------------------------------------------------------------------
# v0.3 fix "secret-placeholders": a secret assignment whose WHOLE value is a template placeholder or a reference to a
# secret manager names where the secret lives; the secret itself is not in the text. A placeholder is exempt only
# when it carries no literal value at all:
# - the placeholder must be the entire value (quoted: everything inside the quotes; bare: up to whitespace, a comma,
#   a semicolon or the end), so "${VAULT:x}s3cretValue" or "<set>abc123" is still a secret;
# - no default or fallback part: "${DB_PASSWORD:-Xk9pL2}", "${DB_PASSWORD:=x}", "${db.password:Tr0ub4dor}" and
#   "${X-x}" are values (only an empty default, "${X:-}", is exempt); a colon inside "${...}" is accepted only after
#   a short list of secret-manager prefixes ("${VAULT:db-password}", "${ssm:/app/db}");
# - angle brackets only around descriptive placeholder text: words with a space ("<set in vault>",
#   "<your password>") or one all-caps word from a short list ("<REDACTED>"); "<swordfish>" is a value;
# - only the assignment and "password is ..." heuristics consult it; the key, hex and base64 patterns still run over
#   the whole text, so a high-entropy string written inside a placeholder is still caught by them.
# Review fix (v0.3 freeze): the first version accepted defaults and single lower-case words; see the development log.
# ----------------------------------------------------------------------------------------------------------------------
_VAR_NAME = r"[A-Za-z_][A-Za-z0-9_.]{0,126}"                 # an environment or property name: no ":" and no "-"
_REF_NAME = r"[A-Za-z0-9_./][A-Za-z0-9_./#@\-]{0,126}"        # a secret-manager path after a prefix, or a template name
_REF_PREFIX = r"(?i:vault|ssm|secret|secrets|secretsmanager|kv|keyvault|env|sm|op)"
PLACEHOLDER_WORDS = frozenset({
    "REDACTED", "REMOVED", "HIDDEN", "MASKED", "SECRET", "PASSWORD", "TOKEN", "VALUE", "PLACEHOLDER",
})
PLACEHOLDER = re.compile(
    r"""(?x)
    \$\{\{\s*""" + _VAR_NAME + r"""\s*\}\}                          # ${{ secrets.DB_PASSWORD }} (CI expressions)
    | \$\{\s*""" + _REF_PREFIX + r"""\s*:\s*""" + _REF_NAME + r"""\s*\}  # ${VAULT:db-password}, ${ssm:/app/db}
    | \$\{\s*""" + _VAR_NAME + r"""(?::[-=])?\s*\}                  # ${DB_PASSWORD}, ${X:-} (empty default only)
    | \{\{-?\s*\.?""" + _VAR_NAME + r"""(?:\s*\|\s*[A-Za-z_]+)*\s*-?\}\}  # {{ secret }}, {{- .Values.db.password -}}
    | \$\(\s*[A-Za-z_][A-Za-z0-9_.\-]{0,80}\s*\)                     # $(DB_PASSWORD), $(var.password)
    | %\([A-Za-z_][A-Za-z0-9_]{0,80}\)[sdr]                            # %(db_password)s
    | %[A-Za-z_][A-Za-z0-9_]{0,80}%                                   # %DB_PASSWORD%
    | \$[A-Z][A-Z0-9]*_[A-Z0-9_]{1,60}                               # $DB_PASSWORD (upper case with an underscore)
    | <\s*[A-Za-z][A-Za-z_\-]*(?:[ _\-][A-Za-z][A-Za-z_\-]*){0,10}\s*>  # checked by _descriptive_angle below
    | \[\s*(?:REDACTED|REMOVED|HIDDEN|MASKED|SECRET|PASSWORD|TOKEN)\s*\]
    | \((?:REDACTED|REMOVED|HIDDEN|MASKED)\)
    | (?:REDACTED|MASKED)
    | \*{3,}                                                          # ******** (masked)
    | (?:vault|secret|secrets|ssm|kms|keyvault|op)://[A-Za-z0-9_./#:@\-]{1,200}   # secret-manager URIs
    | arn:[a-z\-]{1,20}:(?:secretsmanager|ssm):[A-Za-z0-9_./:#\-]{1,200}           # cloud secret ARNs
    | @[A-Za-z]{1,40}\.KeyVault\([^()\s]{1,200}\)                                  # key-vault app-setting references
    | projects/[A-Za-z0-9_\-]{1,80}/secrets/[A-Za-z0-9_\-]{1,80}(?:/versions/[A-Za-z0-9_\-]{1,40})?
    """
)
_VALUE_END = re.compile(r"\s|[,;]|$")


def _descriptive_angle(value: str) -> bool:
    """Angle-bracket text that cannot be a value: words with a space, or one all-caps placeholder word."""
    inner = value.strip("<>").strip()
    return " " in inner or inner in PLACEHOLDER_WORDS


def placeholder_value(view: str, start: int, end: int | None = None) -> bool:
    """True when the value starting at ``start`` is wholly a placeholder or secret-manager reference.

    With ``end`` (a quoted value), the placeholder must fill ``view[start:end]`` exactly; without it (a bare value),
    the placeholder must run from ``start`` to whitespace, a comma, a semicolon or the end of the text.
    """
    match = PLACEHOLDER.match(view, start, len(view) if end is None else end)
    if not match:
        return False
    if end is not None:
        if match.end() != end:
            return False
    elif not _VALUE_END.match(view, match.end()):
        return False
    value = match.group()
    return not value.startswith("<") or _descriptive_angle(value)


# ----------------------------------------------------------------------------------------------------------------------
# v0.3 fix "checksum-context": about 1 in 11 random nine-digit numbers passes the TFN check (1 in 22 ten-digit numbers
# the Medicare check, 1 in 89 eleven-digit numbers the ABN check), so an order or invoice number is often a "valid"
# TFN. A number whose label, directly before it, is a reference word ("Invoice 123...", "Order no. 123...",
# "Ticket #123...") becomes a generic identifier (INTERNAL_ID) in the rules, still substituted, unless the label's
# own positive context is anywhere on the line before it or right after it ("TFN", "tax file"), which keeps the
# label. Tuning round 1 (development log): the reference word must label the number; "in case you need it: N" and
# "the ABN for the invoice is N" are not case or invoice numbers.
# ----------------------------------------------------------------------------------------------------------------------
CONTEXT_BEFORE_CHARS = 40
CONTEXT_AFTER_CHARS = 16
NEGATIVE_NUMBER_CONTEXT = re.compile(
    r"(?i)(?:\b(?:invoice|inv|order|ord|ref|reference|ticket|case|account|acct|acn|build|serial|s/n|receipt|"
    r"tracking|transaction|txn|booking|quote|purchase\s+order|po)\b|\bA\.C\.N\b\.?)"
    r"(?:\s*(?:no|num|number|id|ref)\b\.?)?\s*[:#=\-]*\s*$"
)
POSITIVE_NUMBER_CONTEXT = {
    Label.TFN: re.compile(r"(?i)\btfn\b|\btax\s*file\b"),
    Label.MEDICARE: re.compile(r"(?i)\bmedicare\b"),
    Label.ABN: re.compile(r"(?i)\babn\b|\bbusiness\s+number\b"),
}
ACN_CONTEXT = re.compile(r"(?i)\bacn\b|\bA\.C\.N\b|\bcompany\s+number\b")
ACN_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 1)


def _line_window(view: str, start: int, end: int) -> tuple[str, str]:
    """The text on the same line just before and just after ``view[start:end]``."""
    before = view[max(0, start - CONTEXT_BEFORE_CHARS):start]
    before = before[before.rfind("\n") + 1:]
    after = view[end:end + CONTEXT_AFTER_CHARS]
    newline = after.find("\n")
    return before, after if newline < 0 else after[:newline]


def number_context(label: Label, view: str, start: int, end: int) -> str | None:
    """``"positive"``, ``"negative"`` or ``None`` for a checksum-valid number; positive wins.

    Positive: the label's own context word anywhere on the line before the number, or after it up to the next digit
    or sentence break ("N (TFN)"). Negative: a reference word that labels the number, directly before it.
    """
    before, after = _line_window(view, start, end)
    after = re.split(r"[0-9.;!?]", after, maxsplit=1)[0]
    positive = POSITIVE_NUMBER_CONTEXT.get(label)
    if positive is not None and (positive.search(before) or positive.search(after)):
        return "positive"
    return "negative" if NEGATIVE_NUMBER_CONTEXT.search(before) else None


def acn_valid(value: str) -> bool:
    """Australian Company Number: eight-digit body, weights 8..1, check digit = (10 - sum mod 10) mod 10."""
    compact = _compact(value)
    if not re.fullmatch(r"[0-9]{9}", compact):
        return False
    total = sum(int(digit) * weight for digit, weight in zip(compact, ACN_WEIGHTS))
    return (10 - total % 10) % 10 == int(compact[8])


def company_number(view: str, start: int, end: int) -> bool:
    """A nine-digit number introduced as an ACN on its line that passes the ACN check: company evidence."""
    before, _ = _line_window(view, start, end)
    return bool(ACN_CONTEXT.search(before)) and acn_valid(view[start:end])


# ----------------------------------------------------------------------------------------------------------------------
# v0.3 fix "rule-completeness" (intl pack): payment cards by issuer prefix, length and the Luhn check.
# Prefixes and lengths follow the published issuer identification ranges of the major card schemes (ISO/IEC 7812).
# ----------------------------------------------------------------------------------------------------------------------
CARD = re.compile(r"(?<![\w.\-])[0-9](?:[ \-]?[0-9]){11,18}(?![\w\-]|\.[0-9])")
_CARD_RANGES = (
    # (low prefix, high prefix, lengths); prefixes compare as strings of equal length
    ("4", "4", (13, 16, 19)),
    ("51", "55", (16,)), ("2221", "2720", (16,)),
    ("34", "34", (15,)), ("37", "37", (15,)),
    ("6011", "6011", (16, 17, 18, 19)), ("644", "649", (16, 17, 18, 19)), ("65", "65", (16, 17, 18, 19)),
    ("3528", "3589", (16, 17, 18, 19)),
    ("300", "305", (14, 15, 16, 17, 18, 19)), ("36", "36", (14, 15, 16, 17, 18, 19)),
    ("38", "39", (14, 15, 16, 17, 18, 19)),
    ("62", "62", (16, 17, 18, 19)),
)


def luhn_valid(digits: str) -> bool:
    if not digits.isdigit() or len(digits) < 2:
        return False
    total = 0
    for index, character in enumerate(reversed(digits)):
        value = int(character)
        if index % 2:
            value = value * 2 - 9 if value > 4 else value * 2
        total += value
    return total % 10 == 0


def payment_card_valid(value: str) -> bool:
    """Issuer prefix and length from the scheme table, the Luhn check, and not one repeated digit."""
    digits = re.sub(r"[ \-]", "", value)
    if not digits.isdigit() or len(set(digits)) == 1:
        return False
    for low, high, lengths in _CARD_RANGES:
        prefix = digits[:len(low)]
        if len(digits) in lengths and low <= prefix <= high:
            return luhn_valid(digits)
    return False


# ----------------------------------------------------------------------------------------------------------------------
# v0.3 fix "organisation-contacts": role mailboxes and Australian inbound numbers are an organisation's, not a
# person's, in nearly every document. They are still substituted (a sole trader's info@ can be personal), but a
# tracer finding on one is no longer evidence for the empty-finder hold. Generic function and department words only;
# a mailbox whose local part has any other word in it (a name, a place) is not a role mailbox here.
# ----------------------------------------------------------------------------------------------------------------------
ROLE_LOCAL_WORDS = frozenset({
    "info", "information", "enquiries", "enquiry", "inquiries", "inquiry", "support", "help", "helpdesk",
    "servicedesk", "service", "services", "desk", "admin", "administration", "noreply", "donotreply", "no", "not",
    "do", "reply", "contact", "contactus", "contacts", "us", "office", "frontdesk", "front", "reception",
    "bookings", "booking", "reservations", "sales", "accounts", "accountspayable", "payable", "receivable",
    "billing", "invoices", "finance", "payroll", "hr", "people", "careers", "jobs", "recruitment", "media", "press",
    "news", "newsletter", "comms", "communications", "marketing", "privacy", "feedback", "complaints", "hello",
    "team", "ops", "operations", "it", "ict", "security", "safety", "whs", "hse", "facilities", "maintenance",
    "legal", "compliance", "procurement", "purchasing", "orders", "events", "admissions", "enrolments",
    "enrollments", "records", "library", "webmaster", "postmaster", "hostmaster", "abuse", "noc", "mailer", "daemon",
    "notifications", "alerts", "general", "governance", "quality", "grants", "membership", "volunteers",
    "donations", "student", "students", "customer", "customers", "and", "the", "centre", "center",
})
# Words that belong in a role mailbox but are never one on their own ("us@", "no@", "the@").
_ROLE_CONNECTORS = frozenset({"no", "not", "do", "us", "and", "the", "front", "desk"})
INBOUND_NUMBER = re.compile(r"13[0-9]{4}|1[38]00[0-9]{6}")
_CONTACT_EDGE = ".,;:!?)]}'\"([{<>"


def role_mailbox(value: str) -> bool:
    """The local part is made only of generic function or department words (``info``, ``no-reply``, ``hr.payroll``)."""
    value = value.strip(_CONTACT_EDGE)
    if value.count("@") != 1:
        return False
    local = value.split("@", 1)[0].split("+", 1)[0].casefold()
    words = [word for word in re.split(r"[._\-]", local) if word]
    return bool(words) and all(word in ROLE_LOCAL_WORDS for word in words) and not (
        len(words) == 1 and words[0] in _ROLE_CONNECTORS
    )


def inbound_number(value: str) -> bool:
    """An Australian inbound number: 13 xx xx, 1300 xxx xxx or 1800 xxx xxx."""
    value = unicodedata.normalize("NFKC", strip_default_ignorable(value)).strip(_CONTACT_EDGE + " ")
    if re.search(r"[^0-9\s.\-()]", value):
        return False
    return bool(INBOUND_NUMBER.fullmatch(re.sub(r"[^0-9]", "", value)))


def organisation_value(text: str, start: int, end: int, labels, *, contacts: bool, companies: bool) -> bool:
    """True when a finding's whole value is an organisation's contact or company number (value-based, no profile).

    ``contacts``: a role mailbox (EMAIL) or an inbound number (PHONE). ``companies``: a nine-digit ACN-valid number
    introduced as an ACN on its line, whatever label a detector gave it.
    """
    value = text[start:end]
    if contacts:
        if Label.EMAIL in labels and role_mailbox(unicodedata.normalize("NFKC", strip_default_ignorable(value))):
            return True
        if Label.PHONE in labels and inbound_number(value):
            return True
    if companies:
        before = detection_view(text[max(0, start - CONTEXT_BEFORE_CHARS):start]).text
        number = detection_view(value).text.strip(_CONTACT_EDGE + " ")
        if re.fullmatch(r"[0-9](?:[\s.\-]*[0-9]){8}", number):
            local = before + number
            return company_number(local, len(before), len(local))
    return False


INTL_PHONE = re.compile(
    r"(?<![\w+])\+[1-9][0-9]{0,2}(?:[\s.-]?\(?[0-9]{1,5}\)?){2,6}(?!\w)"
)
IBAN = re.compile(
    r"(?<!\w)[A-Z]{2}[0-9]{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?(?!\w)"
)


def iban_valid(value: str) -> bool:
    compact = re.sub(r"\s", "", value).upper()
    if not 15 <= len(compact) <= 34 or not re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]+", compact):
        return False
    rotated = compact[4:] + compact[:4]
    digits = "".join(str(int(ch, 36)) for ch in rotated)
    return int(digits) % 97 == 1


def _intl_phone_valid(value: str) -> bool:
    return 8 <= len(re.sub(r"[^0-9]", "", value)) <= 15


_packs_lock = Lock()
_active_packs: tuple[str, ...] | None = None


def validate_rule_packs(packs) -> tuple[str, ...]:
    """Validate pack names; ``core`` is always included."""
    if isinstance(packs, str):
        packs = [item.strip() for item in packs.split(",") if item.strip()]
    packs = tuple(dict.fromkeys(packs))
    if not packs or any(pack not in RULE_PACKS for pack in packs):
        raise ValueError("unknown rule pack")
    return packs if "core" in packs else ("core", *packs)


def active_rule_packs() -> tuple[str, ...]:
    global _active_packs
    with _packs_lock:
        if _active_packs is None:
            configured = os.environ.get(RULE_PACKS_ENV)
            _active_packs = validate_rule_packs(configured) if configured else DEFAULT_RULE_PACKS
        return _active_packs


def set_rule_packs(packs) -> tuple[str, ...]:
    """Set the process-wide rule packs at startup; ``None`` restores the default."""
    global _active_packs
    with _packs_lock:
        _active_packs = validate_rule_packs(packs) if packs is not None else None
    return active_rule_packs()


def canonical_phone(value: str) -> str:
    digits = re.sub(r"[^0-9]", "", unicodedata.normalize("NFKC", value))
    if digits.startswith("610"):
        return digits[2:]
    if digits.startswith("61"):
        return "0" + digits[2:]
    return digits


@dataclass(frozen=True, slots=True)
class RulesDetector:
    detector_id: str = "rules"
    packs: tuple[str, ...] | None = None

    def detect(self, text: str) -> StageResult:
        packs = validate_rule_packs(self.packs) if self.packs is not None else active_rule_packs()
        au, intl = "au" in packs, "intl" in packs
        view, offsets = normalised_view(text)
        out = []

        def emit(a: int, b: int, label: Label, checked: bool = False) -> None:
            if a >= b:
                return
            out.append(finding(
                offsets[a][0], offsets[b - 1][1], label,
                self.detector_id, 1, 1.0 if checked else 0.8,
                checksum_valid=True if checked else None,
            ))

        context, companies = fix_on("checksum-context"), fix_on("rule-completeness")
        if au:
            for match in NUMERIC.finditer(view):
                for label in (Label.TFN, Label.MEDICARE, Label.ABN):
                    if validate_identifier(label, match.group()).checksum_valid:
                        a, b = match.span()
                        if label == Label.TFN and companies and company_number(view, a, b):
                            # v0.3: a number introduced as an ACN that passes the ACN check is a company number,
                            # even when it also passes the TFN check (about 1 in 11 do). Still substituted.
                            emit(a, b, Label.INTERNAL_ID, True)
                        elif context and number_context(label, view, a, b) == "negative":
                            # The label changes; the checksum result does not (it guards the allow strength).
                            emit(a, b, Label.INTERNAL_ID, True)
                        else:
                            emit(a, b, label, True)
            for match in USI_PATTERN.finditer(view):
                # v0.2: the USI check character alone matches about one ordinary 10-letter word in 32
                # ("Breakwater"), so a USI must also contain at least one digit.
                if v02() and not any(ch.isdigit() for ch in match.group()):
                    continue
                if validate_identifier(Label.USI, match.group()).checksum_valid:
                    emit(match.start(), match.end(), Label.USI, True)
        patterns = [
            (EMAIL, Label.EMAIL), (PHONE, Label.PHONE), (URL, Label.URL_PERSONAL),
            (KEY, Label.SECRET), (HEX_SECRET, Label.SECRET),
            (B64_SECRET, Label.SECRET), (PRIVATE_KEY, Label.SECRET),
        ]
        if not au:
            patterns.remove((PHONE, Label.PHONE))
        if intl:
            for match in INTL_PHONE.finditer(view):
                if _intl_phone_valid(match.group()):
                    emit(match.start(), match.end(), Label.PHONE)
            for match in IBAN.finditer(view):
                if iban_valid(match.group()):
                    emit(match.start(), match.end(), Label.BANK_ACCOUNT, True)
            if companies:
                for match in CARD.finditer(view):
                    if payment_card_valid(match.group()):
                        emit(match.start(), match.end(), Label.PAYMENT_CARD, True)
        url_fix, org_fix = fix_on("secret-url-components"), fix_on("secret-org-codes")
        for pattern, label in patterns:
            for match in pattern.finditer(view):
                end = match.end()
                if label == Label.URL_PERSONAL:
                    end = match.start() + len(match.group().rstrip(".,;!?)"))
                if pattern in (HEX_SECRET, B64_SECRET):
                    # v0.3: slash-joined words in a URL path and run-together words identify nobody and are no
                    # credential; every other high-entropy string is emitted and judged in context at the end.
                    if url_fix and url_component(view, match.start(), end) == "path" and words_only_path(
                        match.group()
                    ):
                        continue
                    if org_fix and WORD_MASH.fullmatch(match.group()):
                        continue
                emit(match.start(), end, label)
        for match in LOCAL_PHONE.finditer(view):
            emit(*match.span("number"), Label.PHONE)
        placeholders = fix_on("secret-placeholders")
        exempt: list[tuple[int, int]] = []
        for match in SECRET_ASSIGNMENT.finditer(view):
            group = next(
                name for name in ("double", "single", "bare")
                if match.group(name) is not None
            )
            start, end = match.span(group)
            if placeholders:
                if placeholder_value(view, start, None if group == "bare" else end):
                    # The whole placeholder, so the prose rule cannot pick a word out of "<your password here>".
                    exempt.append((start, PLACEHOLDER.match(view, start).end()))
                    continue
                if group == "bare" and PLACEHOLDER.match(view, start):
                    # Placeholder syntax with a value attached ("${VAULT:x}Zq8kLm2"): the bare pattern stops at the
                    # closing brace, so take the whole token, up to whitespace, a comma or a semicolon.
                    end = _VALUE_END.search(view, start).start()
            emit(start, end, Label.SECRET)
        for match in SECRET_PROSE.finditer(view):
            # Avoid obvious generic prose; assignments and key patterns do not
            # use this heuristic. The LLM stages remain responsible for context.
            if placeholders and (
                placeholder_value(view, match.start("value"))
                or any(a <= match.start() < b for a, b in exempt)
            ):
                continue
            if fix_on("secret-prose-values") and not credential_shaped(
                match.group().split(None, 1)[0], match.group("value"),
            ):
                continue
            if match.group("value").casefold() not in {
                "reset", "policy", "requirements", "protected", "required",
            }:
                emit(*match.span("value"), Label.SECRET)
        return StageResult(
            tuple(out), StageReport(self.detector_id, Status.OK, len(out))
        )
