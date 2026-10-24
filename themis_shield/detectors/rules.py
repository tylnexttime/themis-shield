"""Narrow stdlib checksum/contact/credential backstop, grouped into rule packs.

Rule packs:

- ``core`` (always on): email addresses, personal URLs, API keys, hex/base64
  secrets, private keys, secret assignments and "phone: NNNN NNNN" forms.
- ``au``: Australian identifiers with checksums (TFN, Medicare, ABN, USI) and
  Australian phone number formats.
- ``intl``: international E.164 phone numbers and IBANs (mod-97 checksum).

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

from ..types import Label
from .contracts import StageReport, StageResult, Status, finding
from .settings import v02


USI_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
TFN_WEIGHTS = (1, 4, 3, 7, 5, 8, 6, 9, 10)
MEDICARE_WEIGHTS = (1, 3, 7, 9, 1, 3, 7, 9)
ABN_WEIGHTS = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)
CHECKSUM_LABELS = frozenset({Label.USI, Label.TFN, Label.MEDICARE, Label.ABN})
IGNORABLE = "\u200b\u2060\ufeff"
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
    value = unicodedata.normalize("NFKC", value).upper()
    value = value.translate(dict.fromkeys(map(ord, IGNORABLE)))
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

    Each expanded character points to its complete source character. Selected
    zero-width separators are omitted only from the view, not from the source.
    """
    characters, offsets = [], []
    for index, character in enumerate(text):
        if character in IGNORABLE:
            continue
        for normal in unicodedata.normalize("NFKC", character):
            characters.append(normal)
            offsets.append((index, index + 1))
    return "".join(characters), tuple(offsets)


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

        if au:
            for match in NUMERIC.finditer(view):
                for label in (Label.TFN, Label.MEDICARE, Label.ABN):
                    if validate_identifier(label, match.group()).checksum_valid:
                        emit(match.start(), match.end(), label, True)
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
        for pattern, label in patterns:
            for match in pattern.finditer(view):
                end = match.end()
                if label == Label.URL_PERSONAL:
                    end = match.start() + len(match.group().rstrip(".,;!?)"))
                emit(match.start(), end, label)
        for match in LOCAL_PHONE.finditer(view):
            emit(*match.span("number"), Label.PHONE)
        for match in SECRET_ASSIGNMENT.finditer(view):
            group = next(
                name for name in ("double", "single", "bare")
                if match.group(name) is not None
            )
            emit(*match.span(group), Label.SECRET)
        for match in SECRET_PROSE.finditer(view):
            # Avoid obvious generic prose; assignments and key patterns do not
            # use this heuristic. The LLM stages remain responsible for context.
            if match.group("value").casefold() not in {
                "reset", "policy", "requirements", "protected", "required",
            }:
                emit(*match.span("value"), Label.SECRET)
        return StageResult(
            tuple(out), StageReport(self.detector_id, Status.OK, len(out))
        )
