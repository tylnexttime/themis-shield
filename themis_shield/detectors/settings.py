"""Process-wide detection behaviour version and the named v0.3 friction fixes.

``v0.2`` (default) adds the deterministic fixes measured on the v0.2 development set: time-shaped tracer dates are
not dates of birth, trailing punctuation is stripped before allow-list matching, the USI checksum needs a digit, the
organisation's own ABN is allow-listed even when checksum-valid, and rare categorical values in identifier-bearing
tables become QUASI_ID. ``v0.1`` restores the evaluated v0.1 behaviour byte for byte (the negative control).

Set at start-up from ``[detection] version`` (or the evaluation runner's ``--detection-version``).

v0.3 friction fixes (lane 1) are named switches on top of v0.2, each measured alone and together by replay on the
development sets. ``[detection] fixes`` (a list of names, ``"all"`` or ``"none"``) or the runner's
``--detection-fixes`` selects them; ``version = "v0.1"`` switches every one of them off as well. All are on by
default except the opt-in ones (``OPT_IN_DETECTION_FIXES``: ``finder-whitespace-trust`` and ``policy-statements``);
``fixes = "none"`` reproduces the v0.2.1 behaviour byte for byte and ``"all"`` turns the opt-in ones on too.

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
  reference to where a secret lives (``os.environ['X']``, ``var.x``, ``{{ lookup(...) }}``, ``vault:secret/...``,
  ``<your-api-key>``, ``REPLACE_ME``, a URL whose password slot is a template), declares one (``variable "x" {``) or
  plainly holds no value (an import, a lone brace). A literal value, or any other line, keeps the label. See
  ``references_only`` in ``detectors/secret_context.py``.
- ``tracer-evidence-sanity``: a PII-Tracer value that cannot be what its label says (an amount as an identifier, a
  word as a URL, a lone role word as a person; in a document with no person found, a date with no birth context as a
  date of birth and a place without a number as an address) is still substituted but no longer holds a document both
  finders read as clean. See ``detectors/tracer_sanity.py``.
- ``finder-windows`` (on since 0.3.2, measured live 30/10/2026; DD-05): a document longer than 6,000 characters is read by the LLM finders
  in line-bounded windows (table headers repeated), so a dense document no longer overflows the finder's answer and
  fails closed; see ``finder_windows`` in ``detectors/llm_finder.py``. It changes the finder prompts, so captured
  answers cannot replay it: it is measured live before it is adopted.
- ``detectors-detection-view`` (on since 0.3.2, measured live 30/10/2026; DD-06): PII-Tracer and the LLM finders read the detection
  view (invisible characters removed, NFKC per character) like every other detector, and their spans map back to the
  source; see ``on_detection_view`` in ``detectors/contracts.py``. Text the view leaves unchanged is read as it is,
  so only documents with invisible or compatibility characters change.
- ``joined-terms`` (DD-17): an institutional term the allow-list finds inside a finding's value, joined to the rest of
  that value by a hyphen, dash, underscore, slash, backslash or by nothing (the ``KWA`` of a staff number
  ``KWA-EMP-92315``), is part of the value and does not cut it, so no prefix of a personal identifier is left in
  clear. A term that is the whole value, or is set apart by a space or punctuation, is trimmed as before; see
  ``_joined`` in ``detectors/allowlist.py``.

v0.5 detection fixes (from split F, now development data):

- ``location-columns`` (from split F): a table column whose header names where a person lives (``residence_postcode``,
  ``home suburb``, ``postcode of residence``, ``home address``, ``residential town``, ``place of residence``,
  ``lives in``) protects every non-empty cell as ADDRESS. A header that names a workplace, site, office, branch,
  depot, campus, ward, delivery, return or business location is not one, and neither is a bare ``postcode`` or
  ``suburb`` (often a site's); see ``table_location_spans`` in ``detectors/propagate.py``.
- ``secret-config-lookalikes``: four shapes in clean configuration files that a SECRET finding (from any detector) took
  for a credential and that a rule can tell apart with certainty. Two extend ``secret-references`` and act only with it
  on: a template call to a secret manager that names a path and at most a field (``"{{ vault 'kv/app/db#password' }}"``,
  ``"{{ vault('kv/app/db', 'password') }}"``), and a URI of a non-web scheme made only of lower-case words under a key
  that says it is a reference (``credentialRef: "keyhold://kv-prod/edge/syd#admin"``); split G's clean files added a
  ``${NAME:path}`` lookup (``"${SECRET_MANAGER:prod/app/db/password}"``), a template lookup written with a colon
  (``{{ vault:kv/app#password }}``), the same URI under a key whose last word names a credential
  (``broker_password: "sm://org/prod/broker-password"``), and the pieces a finder cuts out of such a reference (a
  fragment, or the key with its quotes and separator, ``finance_api_key:``). Two are about the span itself: a
  path of lower-case words that is a directory (it ends in ``/``) or a configuration file (a file extension such as
  ``.yaml``, ``.json`` or ``.tf`` follows it), and the key of an assignment that is a camel-case identifier of three or
  more words (``targetCPUUtilizationPercentage: 65``). The same shape as a value (``password: correctHorseBatteryStaple``),
  a key alone that is not such an identifier, a literal in a trailing comment and a lone base64 key keep their label
  (COR-001).
  See ``word_structure`` and ``references_only`` in ``detectors/secret_context.py``.
- ``propagate-detection-view`` (DD-19, review COR-002): name propagation and the table name, identifier,
  quasi-identifier, location and statement passes read the detection view (invisible characters removed, NFKC per
  character) like the other detectors, and every span they make maps back to the source characters that produced it,
  invisible ones inside it included. One zero-width space or soft hyphen in a table header or in a later mention no
  longer switches that protection off. Findings coming in are mapped into the view first. Text the view leaves
  unchanged is read as it is; see ``propagate`` in ``detectors/propagate.py``.
- ``negative-statements`` (from split F): a statement that a person does not have a sensitive attribute is a
  statement of that category: ``is not on any medication``, ``No regular medications``, ``denies any thoughts of
  self-harm`` (HEALTH); ``No disclosable court outcomes``, ``has never been charged``, ``the police certificate shows no
  matters`` (CRIMINAL; "matters" and "outcomes" only near a police, court, conviction or criminal word, and never in a
  paragraph about a company); ``is not experiencing family violence``; ``am not a member of any union``; and a ``No``
  (or other whole answer) after a question about Aboriginal or Torres Strait Islander origin, ethnicity, criminal
  history, union membership or disability. The span is the clause. A negative in a general rule about a class of
  people is not one (DD-26): ``A person with no criminal record is treated the same as any other applicant ...``,
  with a generic subject (a person, any applicant, applicants, staff, anyone who) before it and nothing personal in the
  sentence; ``He has no criminal record`` and ``Theo has never been charged`` still match. See
  ``negative_statement_spans`` in ``detectors/statements.py``.
- ``unique-roles`` (from split F): a role the text says only one person holds (``the only analyst on the overnight
  roster``, ``the one contractor seconded from ...``, ``Sole night-shift pharmacist at ...``) is a QUASI_ID: "the
  only", "only", "sole", "the sole" or "the one", up to three describing words (no function word or number: "only one
  manager on duty" is not one), a role word (the last words of the common-role list, generic ones such as staff,
  member or employee left out), then "on", "at", "in", "from", "for", "with", "based", "seconded" or "who"; the span
  ends at punctuation, a cell separator, an auxiliary verb or 50 characters (a "who" clause still running at 50
  characters goes on to the auxiliary verb or cell end after it, within 120: DD-26). See ``unique_role_spans`` in
  ``detectors/propagate.py``.
- ``quasi-cells`` (from split F): a QUASI_ID finding inside one table cell that covers only part of it is widened to
  the whole cell (a piece after an unquoted ", " belongs to the cell before it, as for ``location-columns``), so the
  rest of a role description is not released beside it; a ";" inside the cell ends a clause, and only the clause
  that holds the finding is widened (DD-26). A finding across cells, a cell over 160 characters and a
  QUASI_ID label given only by PII-Tracer (it calls single sensitive words quasi-identifiers) are left as they are;
  see ``quasi_cell_spans`` in ``detectors/propagate.py``.
- ``policy-statements`` (from split G's friction; **opt-in, off by default** since 13/11/2026): **not safe for release
  decisions: a rule-based genericity test misses sentence shapes; see RC-01, RC-03 (second independent review). A
  model-based check with abstain-to-hold is the planned replacement.** Two rounds of review each found another
  sentence shape that released a statement about a specific person ("Staff are expected to remember that the cook has
  epilepsy." was ASK in 0.4.0 and RELEASE with this switch on); a leak is worse than friction. The code and the switch
  stay for measurement. What it does when selected: a sensitive finding, from any detector, is dropped when the clause it
  lies in is plainly a general rule about a class of people and about no one in particular (taxonomy section 3: a
  sensitive statement is labelled only when it is about a person). Fixed wording only: a generic subject (``staff with
  a disability``, ``employees``, ``any employee``, ``anyone who``, ``union matters``, ``union activity``, ``the College
  does not ask whether any employee ...``) followed by a modal or generic verb (``can request``, ``may apply``, ``are
  entitled to``, ``is treated as``, ``are governed by``, ``does not ask whether``), and nothing personal in the clause
  (no he/she/I/you, no name or PERSON finding, no ``the only``/``sole``/``one``, no ``a staff member at ...``, no
  ``asked``/``said``). A finding across sentences, a sensitive-column cell and a finding with another label are kept;
  no other finding changes. See ``drop_policy_statements`` in ``detectors/policy_statements.py``.
- ``indirect-statements`` (from split G): a sensitive statement that does not name its category, in the taxonomy's
  own examples, is a statement of it: ``did eighteen months inside``, ``is on a community corrections order``,
  ``completed a community corrections order``, ``is on parole``, ``sees his parole officer`` (CRIMINAL); ``leaves early
  on Fridays for Shabbat``, ``keeps kosher``, ``needs halal`` (RELIGION); ``carries the delegate's card``, ``I am the
  delegate contact for outdoor staff`` (UNION). Each needs a person: a singular or personal verb form, or (for "did
  time inside", Shabbat and halal) a personal pronoun before it in its clause; a modal, "if" or "any applicant" before
  it (a policy sentence) never matches. The span is the clause, from the pronoun when there is one. Split G also
  added to ``negative-statements``: ``has never been the subject of any police check``, ``does not have any other
  court orders`` (FAMILY_VIOLENCE near a protection or intervention order, otherwise CRIMINAL), ``has no family
  violence indicators on file``, and a ``Yes`` or ``No`` after a form line that ends with ":" and speaks of a person
  (``I do not identify as Aboriginal or Torres Strait Islander: No``). See ``indirect_statement_spans`` and
  ``negative_statement_spans`` in ``detectors/statements.py``.
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
    # v0.4, from split E (DD-17; detectors/allowlist.py).
    "joined-terms",
    # v0.5, from split F (development data since its v0.5 run; detectors/propagate.py, detectors/secret_context.py).
    "location-columns", "secret-config-lookalikes",
    # From the 11/11/2026 review (DD-19, COR-002; detectors/propagate.py).
    "propagate-detection-view",
    # v0.5, from split F's weakest safety areas (detectors/statements.py, detectors/propagate.py).
    "negative-statements", "unique-roles", "quasi-cells",
    # v0.5, from split G's friction (detectors/policy_statements.py).
    "policy-statements",
    # From split G's sensitive misses (development data; detectors/statements.py).
    "indirect-statements",
)
SECRET_CONTEXT_FIXES = (
    "secret-references", "secret-url-components", "secret-auth-codes", "secret-document-ids", "secret-org-codes", "secret-mrz",
    "secret-config-lookalikes",
)
# Every fix held the ratchet (released leaks, R_any, sensitive R_any, QUASI_ID R_any) on both development sets
# (the v0.1 set and split C, replayed from captured finder answers) and is on by default. Three of them
# (organisation-contacts, checksum-context, and the ACN half of rule-completeness) changed no decision or metric
# there: no development case exercises the situation they are for, so they are adopted on their unit tests.
# The real-document fixes (secret context classes, finder-whitespace, address-line-numbers) held the ratchet on the
# development sets and on the private real slice. finder-whitespace-trust is off: on the real slice it released
# documents whose only hold was the unmatched-values rule and that also had genuine finder misses (+2 leaks).
# Opt-in switches, off by default:
# - finder-whitespace-trust: see above.
# - policy-statements (steward's decision, 13/11/2026): not safe for release decisions; a rule-based genericity test
#   misses sentence shapes (second review RC-01, RC-03). A model-based check with abstain-to-hold is the planned
#   replacement.
OPT_IN_DETECTION_FIXES = ("finder-whitespace-trust", "policy-statements")
DEFAULT_DETECTION_FIXES: tuple[str, ...] = tuple(
    name for name in DETECTION_FIXES if name not in OPT_IN_DETECTION_FIXES
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
