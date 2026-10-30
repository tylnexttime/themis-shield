"""Process-wide detection behaviour version and the named v0.3 friction fixes.

``v0.2`` (default) adds the deterministic fixes measured on the v0.2 development set: time-shaped tracer dates are
not dates of birth, trailing punctuation is stripped before allow-list matching, the USI checksum needs a digit, the
organisation's own ABN is allow-listed even when checksum-valid, and rare categorical values in identifier-bearing
tables become QUASI_ID. ``v0.1`` restores the evaluated v0.1 behaviour byte for byte (the negative control).

Set at start-up from ``[detection] version`` (or the evaluation runner's ``--detection-version``).

v0.3 friction fixes (lane 1) are named switches on top of v0.2, each measured alone and together by replay on the
development sets. ``[detection] fixes`` (a list of names, ``"all"`` or ``"none"``) or the runner's
``--detection-fixes`` selects them; ``version = "v0.1"`` switches every one of them off as well. All are on by
default; ``fixes = "none"`` reproduces the v0.2.1 behaviour byte for byte.

- ``secret-placeholders``: a secret assignment whose whole value is a template placeholder or a reference to a
  secret manager (``${VAULT:...}``, ``{{ secret }}``, ``<set in vault>``, ``%(name)s``, ``$(VAR)``, ``<REDACTED>``)
  is not a secret value. A value with anything else attached is still a secret.
- ``organisation-contacts``: a role mailbox (generic local part such as info@, support@, noreply@) or an Australian
  inbound number (13 xx xx, 1300, 1800) is still substituted, but is not evidence for the empty-finder hold.
- ``checksum-context``: a TFN-, Medicare- or ABN-valid number next to negative context ("invoice", "order", "ref",
  "ticket", ...) is labelled a generic identifier by the rules (still substituted); positive context keeps the label.
- ``rule-completeness``: a Luhn payment-card rule (``intl`` pack) and an ACN validator (``au`` pack): a 9-digit number
  introduced as an ACN that passes the ACN check is company evidence (generic identifier, not a TFN, and not evidence
  for the empty-finder hold).
- ``table-role-words``: in the table quasi-identifier pass, a cell that is only a common role word or title
  ("Manager", "Teacher", "Dr") is never a candidate; a rarer, qualified role ("Night fleet controller") still is.
- ``title-tokens``: a leading title or honorific in a found name ("Captain", "Doctor", "Sister") is not propagated
  through the document as a name token; the title stays beside the name, never part of it.
- ``contact-brackets``: a contact span that lost its opening bracket ("07) 5550 0145") still matches the whole
  public number it belongs to.
- ``secret-url-components``, ``secret-prose-values``, ``secret-auth-codes``, ``secret-document-ids``,
  ``secret-org-codes``, ``secret-mrz``: values the secret rules (or a detector's SECRET label) took for credentials
  that are URL components, prose words, card authorisation codes, document identifiers, organisation codes or a
  machine-readable zone; see ``detectors/secret_context.py``. Never dropped when they could identify someone:
  relabelled (identifier, passport, person) and substituted, without the SECRET block.
- ``finder-whitespace``: a finder value that is not in the text verbatim is matched with any run of whitespace
  (line breaks, no-break spaces) standing for any run of whitespace, token for token, then on the NFKC view.
  Such a value is substituted but still counts as not verbatim for the unmatched-values hold;
  ``finder-whitespace-trust`` counts it as matched (measured; see the development log before turning it on).
- ``address-line-numbers``: an address found from the start of a line takes in a street or unit number that stands
  alone on the line just before it ("48" / "EXAMPLE PDE ..." in scanned statements), so the number is not left in
  clear.

v0.4 detection fixes (from split D, now development data):

- ``column-identifiers``: a table column whose header names a personal identifier (licence, passport, Medicare, tax
  file, national ID, bank account, card, or a staff, employee, student, member, customer, client or patient number)
  is protected cell by cell; see ``table_identifier_spans`` in ``detectors/propagate.py``.
- ``bare-number-formats``: a profile format with no letters in it (eight bare digits, say) allow-lists a number only when the
  format's own key words stand within 40 characters before it ("Work order 12345678"); otherwise it would cancel any
  number of that shape, a licence or account number included. A bare format whose name is only generic words
  ("number", "id") allow-lists nothing. See ``detectors/profile_adapter.py``.
- ``sensitive-columns``: a table column whose header names a sensitive category (police check, criminal history,
  convictions, union membership, medical condition, disability, religion, sexuality, ethnicity or Indigenous status)
  marks every non-empty cell as a statement of that category; a "No" or "Clear" still describes the person.
- ``union-roles``: a sentence that gives a person a trade-union role or act (shop steward, union delegate or member,
  paying or collecting dues, joining the union, the picket line) is a UNION statement; generic policy, credit unions
  and a bare "steward" or "delegate" are not. See ``detectors/statements.py``.
- ``secret-references``: a SECRET finding (from any detector) is dropped when every line it touches assigns only a
  reference to where a secret lives (``os.environ['X']``, ``var.x``, ``{{ lookup(...) }}``, ``vault:...``,
  ``<your-api-key>``, ``REPLACE_ME``, a URL whose password slot is a template) or holds no value; a literal value or a
  long mixed token keeps it. See ``references_only`` in ``detectors/secret_context.py``.
- ``tracer-evidence-sanity``: a PII-Tracer value that cannot be what its label says (an amount as an identifier, a
  word as a URL, a lone role word as a person, a date with no birth context as a date of birth, a place without a
  number as an address) is still substituted but no longer holds a document both finders read as clean.
- ``finder-windows`` (on since 0.3.2, measured live 30/10/2026; DD-05): a document longer than 6,000 characters is read by the LLM finders
  in line-bounded windows (table headers repeated), so a dense document no longer overflows the finder's answer and
  fails closed; see ``finder_windows`` in ``detectors/llm_finder.py``. It changes the finder prompts, so captured
  answers cannot replay it: it is measured live before it is adopted.
- ``detectors-detection-view`` (on since 0.3.2, measured live 30/10/2026; DD-06): PII-Tracer and the LLM finders read the detection
  view (invisible characters removed, NFKC per character) like every other detector, and their spans map back to the
  source; see ``on_detection_view`` in ``detectors/contracts.py``. Text the view leaves unchanged is read as it is,
  so only documents with invisible or compatibility characters change.
"""

from __future__ import annotations

from threading import Lock


DETECTION_VERSIONS = ("v0.1", "v0.2")
DEFAULT_DETECTION_VERSION = "v0.2"
DETECTION_FIXES = (
    "secret-placeholders", "organisation-contacts", "checksum-context", "rule-completeness",
    "table-role-words", "title-tokens", "contact-brackets",
    # From the first run on real documents (v0.3 pre-freeze); see detectors/secret_context.py and llm_finder.py.
    "secret-url-components", "secret-prose-values", "secret-auth-codes", "secret-document-ids", "secret-org-codes",
    "secret-mrz", "finder-whitespace", "finder-whitespace-trust", "address-line-numbers",
    # v0.4, from split D (development data since its v0.3 run).
    "column-identifiers", "finder-windows", "detectors-detection-view", "bare-number-formats",
    # v0.4 item 4 (sensitive statements the finders missed; detectors/statements.py).
    "sensitive-columns", "union-roles",
    # v0.4 friction (split D's clean documents held; detectors/secret_context.py, detectors/tracer_sanity.py).
    "secret-references", "tracer-evidence-sanity",
)
SECRET_CONTEXT_FIXES = (
    "secret-references", "secret-url-components", "secret-auth-codes", "secret-document-ids", "secret-org-codes", "secret-mrz",
)
# Every fix held the ratchet (released leaks, R_any, sensitive R_any, QUASI_ID R_any) on both development sets
# (the v0.1 set and split C, replayed from captured finder answers) and is on by default. Three of them
# (organisation-contacts, checksum-context, and the ACN half of rule-completeness) changed no decision or metric
# there: no development case exercises the situation they are for, so they are adopted on their unit tests.
# The real-document fixes (secret context classes, finder-whitespace, address-line-numbers) held the ratchet on the
# development sets and on the private real slice. finder-whitespace-trust is off: on the real slice it released
# documents whose only hold was the unmatched-values rule and that also had genuine finder misses (+2 leaks).
DEFAULT_DETECTION_FIXES: tuple[str, ...] = tuple(
    name for name in DETECTION_FIXES if name != "finder-whitespace-trust"
)
# v0.4 (0.3.2): column-identifiers and bare-number-formats held the ratchet on all four development sets by replay
# (split D half C: released leaks 26 -> 3). finder-windows and detectors-detection-view changed the finder prompts, so
# they were measured live on the 54 development documents they can change: no set got worse, and the four documents
# that overflowed one answer now complete (half C subset: R_any 0.918 -> 0.996, sensitive R_any 0.556 -> 0.991).
_lock = Lock()
_version = DEFAULT_DETECTION_VERSION
_fixes: frozenset[str] = frozenset(DEFAULT_DETECTION_FIXES)


def configure_detection_version(version: str | None = None) -> str:
    """Set the detection behaviour version; ``None`` restores the default. Returns the active version."""
    global _version
    version = DEFAULT_DETECTION_VERSION if version is None else version
    if version not in DETECTION_VERSIONS:
        raise ValueError("unknown detection version")
    with _lock:
        _version = version
    return _version


def detection_version() -> str:
    return _version


def v02() -> bool:
    """True when the v0.2 detection fixes are on."""
    return _version != "v0.1"


def validate_detection_fixes(fixes) -> tuple[str, ...]:
    """Normalise a fix selection: a list of names, a comma-separated string, ``"all"`` or ``"none"``."""
    if isinstance(fixes, str):
        fixes = [item.strip() for item in fixes.split(",") if item.strip()]
    if not isinstance(fixes, (list, tuple, set, frozenset)) or any(type(item) is not str for item in fixes):
        raise ValueError("invalid detection fixes")
    fixes = list(fixes)
    if fixes in (["all"], ["none"]):
        return DETECTION_FIXES if fixes == ["all"] else ()
    if any(item not in DETECTION_FIXES for item in fixes):
        raise ValueError("unknown detection fix")
    return tuple(item for item in DETECTION_FIXES if item in fixes)


def configure_detection_fixes(fixes=None) -> tuple[str, ...]:
    """Select the v0.3 friction fixes; ``None`` restores the default. Returns the selection in canonical order."""
    global _fixes
    selected = DEFAULT_DETECTION_FIXES if fixes is None else validate_detection_fixes(fixes)
    with _lock:
        _fixes = frozenset(selected)
    return detection_fixes()


def detection_fixes() -> tuple[str, ...]:
    return tuple(item for item in DETECTION_FIXES if item in _fixes)


def fix_on(name: str) -> bool:
    """True when the named v0.3 fix is selected and the detection version is not the v0.1 control."""
    if name not in DETECTION_FIXES:
        raise ValueError("unknown detection fix")
    return _version != "v0.1" and name in _fixes
