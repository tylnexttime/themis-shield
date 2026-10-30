"""Vetting records: one value-free evidence record for each data release vetted through the gate.

A data release (a folder of text documents) is run through the same pipeline as a chat request: markings, tiered
detection, the policy decision, the reviewed false-positive catalogue and the known-miss final sweep. RELEASE
documents are written de-identified, with their placeholder map kept in the local vault (the codebook). ASK documents
wait for a person's recorded resolution. BLOCK documents are never written.

The record says what was done and how well the method is known to work, in seven parts. The gate fills parts 1, 3
and the method baseline of part 4, and pre-fills what it can of parts 5 and 6. A person completes the rest, and the
sign-offs in part 7 are always people's. The record is evidence for a decision, never the decision.

The rules the record keeps:
- Counts, never values. No document text, finding value, file name or codebook entry is written into it, and the
  serialised record is checked against every value the run protected before it is saved.
- One record per release. The release is identified by the sha256 of its documents' content hashes, so changed
  content is a new release and needs a new record.
- Unresolved ASKs make the record "incomplete"; nothing in it ever says "approved".
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from . import __version__

SCHEMA_VERSION = "1"
ROUTES = ("cloud-deidentified", "local-only")
RESOLUTIONS = ("release", "exclude")
TEXT_SUFFIXES = (".txt", ".md", ".csv", ".tsv", ".json", ".jsonl", ".xml", ".html", ".htm", ".log")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,79}")
_ROLE = re.compile(r"[A-Za-z][A-Za-z0-9 .,&()/-]{0,79}")
# Values shorter than this are not searched for in the serialised record: a short value ("Data", an initial) would
# match inside the record's own words and refuse every record. The record holds no document text by construction;
# this is the second key. Derived documents get the stricter check (verify_no_originals) on every value.
MIN_RECORD_CHECK_CHARS = 6

# The method baseline: what the frozen evaluation measured for a version, quoted as published (eval/RESULTS-*.md).
# A version not listed here inherits the most recent evaluated version below it, and the record says so.
METHOD_BASELINES: dict[str, dict[str, Any]] = {
    "0.3.0": {
        "evaluated": "27/10/2026", "frozen_kit": "eval-kit-v0.3-frozen", "split": "D",
        "cases": 404, "gold_spans": 5841, "clean_cases": 125, "verdict": "FAIL",
        "results": "eval/RESULTS-v0.3.md",
        "by_typed_tier": {
            "off": {"released_leaks": 28, "r_any": 0.937, "r_full": 0.911, "sensitive_r_any": 0.740,
                    "clean_friction": 0.304},
            "on": {"released_leaks": 28, "r_any": 0.937, "r_full": 0.911, "sensitive_r_any": 0.740,
                   "clean_friction": 0.248},
        },
        "known_gaps": [
            "identifier numbers in table columns no rule matches (21 of the 28 released spans were driver licence "
            "numbers in identifier columns)",
            "indirect sensitive statements (sensitive-statement R_any 0.740; 0.665 on the harder half)",
        ],
    },
}


class VettingError(ValueError):
    """The release, its metadata or a resolution cannot be vetted as given."""


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version)[:3])


def method_baseline(version: str = __version__, *, typed_enabled: bool = False) -> dict:
    """The baseline for this version, or the latest evaluated one below it (``inherited_from`` says which)."""
    evaluated = sorted((v for v in METHOD_BASELINES if _version_key(v) <= _version_key(version)), key=_version_key)
    if not evaluated:
        return {"available": False, "note": f"no frozen evaluation covers themis-shield {version}"}
    source = evaluated[-1]
    entry = METHOD_BASELINES[source]
    arm = entry["by_typed_tier"]["on" if typed_enabled else "off"]
    return {
        "available": True, "evaluated_version": source,
        "inherited_from": None if source == version else source,
        "typed_tier": "on" if typed_enabled else "off",
        **{key: entry[key] for key in ("evaluated", "frozen_kit", "split", "cases", "gold_spans", "clean_cases",
                                       "verdict", "results", "known_gaps")},
        **arm,
    }


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def release_sha256(document_hashes: Sequence[str]) -> str:
    """The release's identity: sha256 over its documents' sha256s, in order. Names never take part."""
    return hashlib.sha256("\n".join(document_hashes).encode("ascii")).hexdigest()


def _check(pattern, value, what):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise VettingError(f"invalid {what}")
    return value


@dataclass(frozen=True)
class ReleaseInfo:
    """Part 1. Identifiers are the deployment's own (an asset register id, an object name, a release id)."""

    asset: str
    object: str
    release_id: str
    purpose: str
    route: str
    references: tuple[str, ...] = ()

    def __post_init__(self):
        for name in ("asset", "object", "release_id"):
            _check(_IDENTIFIER, getattr(self, name), name)
        for item in self.references:
            _check(_IDENTIFIER, item, "reference")
        if self.route not in ROUTES:
            raise VettingError(f"route must be one of {', '.join(ROUTES)}")
        if not isinstance(self.purpose, str) or not self.purpose.strip() or len(self.purpose) > 300:
            raise VettingError("purpose must be 1-300 characters")


@dataclass
class DocumentOutcome:
    """One document of the release, by position and content hash only."""

    index: int
    sha256: str
    chars: int
    decision: str
    reasons: tuple[str, ...] = ()
    label_counts: dict[str, int] = field(default_factory=dict)
    resolution: dict | None = None
    derived_sha256: str | None = None
    marking: str | None = None

    def view(self) -> dict:
        return {
            "index": self.index, "sha256": self.sha256, "chars": self.chars, "marking": self.marking,
            "decision": self.decision,
            "reasons": list(self.reasons), "findings_by_label": dict(sorted(self.label_counts.items())),
            "resolution": self.resolution, "derived": self.derived_sha256 is not None,
            "derived_sha256": self.derived_sha256,
        }


def build_record(
    release: ReleaseInfo,
    documents: Sequence[DocumentOutcome],
    *,
    gate: Mapping[str, Any],
    codebook: Mapping[str, Any],
    checks: Sequence[tuple[str, bool, str]] = (),
    derived_release_id: str | None = None,
    generated_utc: datetime | None = None,
) -> dict:
    """The JSON record (schema 1). Pure: it reads nothing and writes nothing."""
    generated = (generated_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    decisions = Counter(item.decision for item in documents)
    labels: Counter[str] = Counter()
    for item in documents:
        labels.update(item.label_counts)
    asks = [item for item in documents if item.decision == "ASK"]
    unresolved = [item.index for item in asks if item.resolution is None]
    derived = [item for item in documents if item.derived_sha256 is not None]
    return {
        "schema": "themis-shield/vetting-record", "schema_version": SCHEMA_VERSION,
        "status": "incomplete" if unresolved else "ready-for-review",
        "generated_utc": generated.isoformat(timespec="seconds"),
        "part1_identity": {
            "asset": release.asset, "object": release.object, "release_id": release.release_id,
            "release_sha256": release_sha256([item.sha256 for item in documents]),
            "documents": len(documents), "references": list(release.references),
            "purpose": release.purpose, "route": release.route,
        },
        "part2_classification": {"completed_by": None, "marking": None, "marking_source": None, "pii_state": None,
                                 "pii_kinds": None, "ceiling_check": None,
                                 "gate_markings": dict(sorted(Counter(item.marking or "UNREAD"
                                                                      for item in documents).items())),
                                 "gate_held_local_only": sum(1 for item in documents
                                                             if "ROUTE_LOCAL_MARKING" in item.reasons)},
        "part3_run": {
            "gate": dict(gate),
            "decisions": {key: decisions.get(key, 0) for key in ("RELEASE", "ASK", "BLOCK")},
            "findings_by_label": dict(sorted(labels.items())),
            "asks": [{"index": item.index, "reasons": list(item.reasons), "resolution": item.resolution}
                     for item in asks],
            "unresolved_asks": unresolved,
            "derived": {"release_id": derived_release_id, "documents": len(derived),
                        "release_sha256": release_sha256([item.derived_sha256 for item in derived])
                        if derived else None},
            "documents": [item.view() for item in documents],
        },
        "part4_effectiveness": {
            "method_baseline": method_baseline(gate.get("version", __version__),
                                               typed_enabled=bool(gate.get("typed_tier"))),
            "this_release": {"spot_check": None, "quasi_identifiers": None, "small_cells": None, "linkage": None,
                             "residual_risk": None, "acceptance_criterion": None, "completed_by": None,
                             "peer_checked_by": None},
        },
        "part5_codebook": {**dict(codebook), "restoration_roles": None, "key_rotation": None,
                           "retention_and_disposal": None, "custodian": None},
        "part6_inspection": {
            "automated": [{"check": name, "passed": passed, "detail": detail} for name, passed, detail in checks],
            "manual": [], "not_checked": None,
        },
        "part7_signoffs": [
            {"role": "data custodian", "who": None, "when": None, "conditions": None, "expires": None},
            {"role": "information security", "who": None, "when": None, "conditions": None, "expires": None},
        ],
    }


def assert_value_free(serialised: str, originals: Iterable[str]) -> None:
    """Refuse a record that contains any protected value (case-insensitive) of MIN_RECORD_CHECK_CHARS or more."""
    haystack = serialised.casefold()
    for value in originals:
        needle = value.strip().casefold()
        if len(needle) >= MIN_RECORD_CHECK_CHARS and needle in haystack:
            raise VettingError("the record would contain a protected value; nothing was written")


# --------------------------------------------------------------------------------------------------------------------
def _blank(value) -> str:
    return "_to be completed_" if value in (None, "", []) else str(value)


def render_markdown(record: Mapping[str, Any]) -> str:
    """The human copy of the record, with the same content as the JSON."""
    p1, p3, p4 = record["part1_identity"], record["part3_run"], record["part4_effectiveness"]
    p5, p6, gate = record["part5_codebook"], record["part6_inspection"], p3["gate"]
    base = p4["method_baseline"]
    lines = [
        f"# Vetting record: {p1['asset']} / {p1['object']} / release {p1['release_id']}",
        "",
        f"Status: **{record['status']}**. Generated {record['generated_utc']} by themis-shield {gate.get('version')} "
        f"(configuration sha256 `{gate.get('config_sha256')}`). Counts only: this record holds no document text, "
        "finding value, file name or codebook entry.",
        "",
        "## 1. Identity",
        "",
        f"- Asset `{p1['asset']}` · object `{p1['object']}` · release `{p1['release_id']}`",
        f"- Release content sha256 `{p1['release_sha256']}` ({p1['documents']} documents)",
        f"- References: {', '.join(f'`{r}`' for r in p1['references']) or 'none'}",
        f"- Purpose: {p1['purpose']}",
        f"- Requested route: {p1['route']}",
        "",
        "## 2. Classification and personal information",
        "",
        "- Marking and its source: _to be completed_",
        "- Personal information present (yes / no / unknown) and the evidence: _to be completed_",
        "- Kinds of personal information: _to be completed_",
        "- Ceiling check (nothing above the system's ceiling): _to be completed_",
        "- Markings the gate read, by class: " + ", ".join(
            f"{k} {v}" for k, v in record["part2_classification"]["gate_markings"].items()),
        f"- Documents kept local for their marking (never in the derived release): "
        f"{record['part2_classification']['gate_held_local_only']}",
        "",
        "## 3. De-identification run",
        "",
        f"- themis-shield {gate.get('version')}; detection `{gate.get('detection')}`, policy `{gate.get('policy')}`, "
        f"typed tier {'on' if gate.get('typed_tier') else 'off'}, false-positive catalogue "
        f"{'on' if gate.get('fp_catalogue') else 'off'}, known-miss register {'on' if gate.get('known_miss') else 'off'}",
        f"- Synthetic-only deployment: {'yes' if gate.get('synthetic_only') else 'no'}",
        f"- Decisions: RELEASE {p3['decisions']['RELEASE']} · ASK {p3['decisions']['ASK']} · "
        f"BLOCK {p3['decisions']['BLOCK']}",
        "- Findings by type (counts): " + (", ".join(f"{k} {v}" for k, v in p3["findings_by_label"].items()) or "none"),
        f"- Derived release: `{_blank(p3['derived']['release_id'])}`, {p3['derived']['documents']} documents, "
        f"sha256 `{p3['derived']['release_sha256'] or '-'}`",
        "",
        "| Document | sha256 (first 12) | Decision | Reasons | Resolution | Derived |",
        "|---|---|---|---|---|---|",
    ]
    for item in p3["documents"]:
        resolution = item["resolution"]
        lines.append(
            f"| {item['index']} | `{item['sha256'][:12]}` | {item['decision']} | {', '.join(item['reasons']) or '-'} "
            f"| {resolution['resolution'] + ' by ' + resolution['by_role'] if resolution else '-'} "
            f"| {'yes' if item['derived'] else 'no'} |"
        )
    if p3["unresolved_asks"]:
        lines += ["", f"**Unresolved ASKs:** documents {', '.join(map(str, p3['unresolved_asks']))}. The record stays "
                      "incomplete until a person resolves each one."]
    lines += ["", "## 4. Does it work?", "", "**Method baseline** (the gate's measured performance on its frozen "
              "evaluation, not on this release):", ""]
    if base["available"]:
        inherited = (f" Inherited from {base['inherited_from']}: this version has not been evaluated separately."
                     if base["inherited_from"] else "")
        lines += [
            f"- Split {base['split']} ({base['cases']} synthetic cases, {base['gold_spans']} personal spans), "
            f"frozen kit `{base['frozen_kit']}`, {base['evaluated']}, typed tier {base['typed_tier']}: "
            f"**{base['verdict']}**.{inherited}",
            f"- Released leaks {base['released_leaks']} of {base['gold_spans']} spans · R_any {base['r_any']:.3f} · "
            f"R_full {base['r_full']:.3f} · sensitive-statement R_any {base['sensitive_r_any']:.3f} · "
            f"clean-document friction {base['clean_friction']:.3f} ({base['results']})",
            "- Known gaps: " + "; ".join(base["known_gaps"]),
        ]
    else:
        lines.append(f"- {base['note']}")
    lines += [
        "", "**This release** (a person, peer-checked):", "",
        "- Spot check (sample size, findings): _to be completed_",
        "- Quasi-identifiers considered: _to be completed_",
        "- Small groups (threshold, action): _to be completed_",
        "- Linkage with other releases: _to be completed_",
        "- Residual risk, and the acceptance criterion it was judged against: _to be completed_",
        "", "## 5. Codebook custody", "",
        f"- Store: {p5.get('store')}; handle `{p5.get('handle')}`; {p5.get('entries')} entries; integrity sha256 "
        f"`{p5.get('integrity_sha256')}` (over placeholders, never values); key protector {p5.get('protector')}",
        "- Who may restore; key rotation; retention and disposal; custodian: _to be completed_",
        "", "## 6. Inspection checklist", "", "| # | Check | Result | Detail |", "|---|---|---|---|",
    ]
    for number, item in enumerate(p6["automated"], 1):
        lines.append(f"| {number} | {item['check']} | {'pass' if item['passed'] else 'FAIL'} | {item['detail']} |")
    lines += ["", "Checks by the person vetting, and what was not checked: _to be completed_", "",
              "## 7. Sign-offs", "", "| Role | Who | When | Conditions | Expires |", "|---|---|---|---|---|"]
    for item in record["part7_signoffs"]:
        lines.append(f"| {item['role']} | | | | |")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------------------------------------------
def read_release(folder: Path) -> list[tuple[str, str | None]]:
    """(relative path, text or None) for every file under the folder, sorted; None = not UTF-8 text."""
    folder = Path(folder)
    if not folder.is_dir():
        raise VettingError("release folder not found")
    result = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        relative = path.relative_to(folder).as_posix()
        text = None
        if path.suffix.lower() in TEXT_SUFFIXES:
            try:
                text = path.read_bytes().decode("utf-8-sig")
            except UnicodeDecodeError:
                text = None
        result.append((relative, text))
    if not result:
        raise VettingError("release folder is empty")
    return result


def load_resolutions(path: Path | None) -> dict[int, dict]:
    """``{"documents": {"<index>": {"sha256": ..., "resolution": "release"|"exclude", "by_role": ..., "note": ...}}}``"""
    if path is None:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    result = {}
    for key, value in (data.get("documents") or {}).items():
        if not str(key).isdigit() or not isinstance(value, dict):
            raise VettingError("resolutions are keyed by document index")
        if value.get("resolution") not in RESOLUTIONS:
            raise VettingError(f"resolution must be one of {', '.join(RESOLUTIONS)}")
        _check(_ROLE, value.get("by_role"), "resolution role")
        note = value.get("note") or ""
        if not isinstance(note, str) or len(note) > 300:
            raise VettingError("resolution note must be text of at most 300 characters")
        result[int(key)] = {"sha256": str(value.get("sha256", "")), "resolution": value["resolution"],
                            "by_role": value["by_role"], "note": note}
    return result


def _label(finding) -> str:
    primary = getattr(finding, "primary_label", None)
    return str(getattr(primary, "value", primary) or "UNKNOWN")


def vet_release(
    router,
    documents: Sequence[tuple[str, str | None]],
    release: ReleaseInfo,
    out_dir: Path,
    *,
    resolutions: Mapping[int, dict] | None = None,
    codebook_vault=None,
    config_sha256: str = "",
    gate_versions: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict:
    """Vet a release with the router's own pipeline; write the derived documents, the record and a local index.

    ``out_dir/release/`` holds the derived documents (``doc-0001.<ext>``; names never travel), ``out_dir/local-only/
    document-index.json`` maps positions back to file names and must stay with the codebook, and
    ``out_dir/vetting-record.{json,md}`` is the record. The codebook goes to ``codebook_vault`` (the CLI opens a
    dedicated vault file per release; the chat vault's four-hour idle expiry would make a release unrestorable).
    """
    from .catalogues.gate import decide_with_catalogue, final_sweep
    from .cloud.openai_compat import canonical
    from .detectors.markings import detect_markings, summarise_markings
    from .routing import Route, route_request
    from .detectors.typed import typed_settings
    from .placeholders import SENSITIVE_LABELS, TokenAllocator
    from .substitute import OriginalLeakError, SubstitutionError, substitute, verify_no_originals

    out_dir = Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise VettingError("output folder must be new or empty")
    resolutions = dict(resolutions or {})
    hashes = [sha256_text(text) if text is not None else hashlib.sha256(b"").hexdigest() + ":unreadable"
              for _, text in documents]
    release_hash = release_sha256(hashes)
    conversation_id = f"vet-{release_hash[:16]}"
    vault = router.vault if codebook_vault is None else codebook_vault
    allocator = TokenAllocator(conversation_id, vault)
    outcomes, derived_texts, checks = [], {}, []
    unreadable = 0
    for index, ((name, text), digest) in enumerate(zip(documents, hashes), 1):
        if text is None:
            unreadable += 1
            outcomes.append(DocumentOutcome(index, digest, 0, "BLOCK", ("BLOCK:not-utf8-text",)))
            continue
        outcome = DocumentOutcome(index, digest, len(text), "BLOCK")
        outcomes.append(outcome)
        # Markings first, as on the chat path (router_flow): a local-only marking never reaches a derived release,
        # and a marking that needs a person's confirmation holds the document for ASK.
        routing = route_request(summarise_markings((detect_markings(text),)), router.use_case)
        outcome.marking = routing.audit_fields.marking_class.name
        if routing.route == Route.LOCAL_ONLY:
            outcome.reasons = (routing.audit_fields.reason_code.value,)
            continue
        marking_hold = routing.audit_fields.reason_code.value if routing.mandatory_ask else None
        canary = getattr(router, "canary", None)
        try:
            if canary is not None:
                canary.before_cloud_work()
            evidence = router._scan(text)
        except Exception:
            outcome.reasons = ("BLOCK:detection-incomplete",)
            continue
        # As on the chat path: a failing, or not yet run, detector canary blocks cloud-bound work.
        blocked = canary.block_reason() if canary is not None else None
        if blocked is not None:
            outcome.reasons = (str(blocked),)
            continue
        result, evidence = decide_with_catalogue(router, text, evidence)
        outcome.label_counts = dict(Counter(_label(item) for item in evidence.findings))
        outcome.decision = result.decision.value
        outcome.reasons = tuple(reason.value for reason in result.reasons)
        if outcome.decision == "BLOCK":
            continue
        # A sensitive finding must name its statement span to be substituted. The finders copy the statement itself
        # as the value, so its own span is the statement (the chat path widens to the whole message instead, which
        # would empty a whole document here). Without this every document with one was BLOCKed (DD-15).
        findings = tuple(
            replace(item, statement_span=item.span)
            if item.statement_span is None and item.labels & SENSITIVE_LABELS else item
            for item in evidence.findings
        )
        try:
            derived = substitute(text, findings, allocator).tokenised_text
        except (SubstitutionError, PermissionError):
            outcome.decision, outcome.reasons = "BLOCK", outcome.reasons + ("BLOCK:substitution-failed",)
            continue
        swept = final_sweep(router, canonical({"text": derived}), result)
        outcome.decision = swept.decision.value
        outcome.reasons = tuple(reason.value for reason in swept.reasons)
        if marking_hold is not None:
            outcome.decision, outcome.reasons = "ASK", outcome.reasons + (marking_hold,)
        if outcome.decision == "ASK":
            resolution = resolutions.get(index)
            if resolution is not None and resolution["sha256"] == digest:
                outcome.resolution = {k: resolution[k] for k in ("resolution", "by_role", "note")}
                if resolution["resolution"] == "exclude":
                    continue
            else:
                continue
        derived_texts[index] = (Path(name).suffix.lower() or ".txt", derived)
    mapping = allocator.mapping()
    originals = tuple(mapping.values())
    leaked = []
    for index, (_, derived) in list(derived_texts.items()):
        try:
            verify_no_originals(derived, originals)
        except OriginalLeakError:
            leaked.append(index)
            outcome = outcomes[index - 1]
            outcome.decision, outcome.reasons = "BLOCK", outcome.reasons + ("BLOCK:original-in-output",)
            del derived_texts[index]
    for index, (_, derived) in derived_texts.items():
        outcomes[index - 1].derived_sha256 = sha256_text(derived)
    checks += [
        ("every file read as UTF-8 text", unreadable == 0, f"{unreadable} not read, held as BLOCK"),
        # Written output is verified either way; a document held here is a detection miss worth a person's look.
        ("no protected value left in any derived document", True,
         f"{len(derived_texts)} derived documents verified"
         + (f"; {len(leaked)} held because a value found elsewhere in the release was left in them (a detection "
            f"miss: documents {', '.join(map(str, leaked))})" if leaked else "")),
        ("ASK resolutions match the document hashes",
         all(resolutions[i]["sha256"] == outcomes[i - 1].sha256 for i in resolutions if 0 < i <= len(outcomes)),
         "a resolution whose hash does not match is ignored"),
    ]
    typed = typed_settings()
    gate = {
        "version": __version__, "config_sha256": config_sha256 or None,
        **{key: str(value) for key, value in (gate_versions or {}).items() if key in ("detection", "policy")},
        "typed_tier": bool(getattr(typed, "enabled", False)),
        "fp_catalogue": getattr(router, "fp_catalogue", None) is not None,
        "known_miss": getattr(router, "known_miss", None) is not None,
        "synthetic_only": True,
    }
    tokens = sorted(mapping)
    protector = getattr(vault, "_protector", None)
    codebook = {
        "store": "themis-shield codebook vault (encrypted SQLite; location is the custodian's record, not this one)",
        "handle": conversation_id, "entries": len(tokens),
        "integrity_sha256": hashlib.sha256("\n".join(tokens).encode("utf-8")).hexdigest(),
        "protector": type(protector).__name__ if protector is not None else "in-memory (tests only)",
    }
    record = build_record(
        release, outcomes, gate=gate, codebook=codebook, checks=checks,
        derived_release_id=f"{release.release_id}-deid" if derived_texts else None, generated_utc=now,
    )
    serialised = json.dumps(record, indent=1, ensure_ascii=False)
    markdown = render_markdown(record)
    names = [name for name, _ in documents]
    assert_value_free(serialised + markdown, originals)
    (out_dir / "release").mkdir(parents=True, exist_ok=True)
    (out_dir / "local-only").mkdir(parents=True, exist_ok=True)
    for index, (suffix, derived) in derived_texts.items():
        (out_dir / "release" / f"doc-{index:04d}{suffix}").write_text(derived, encoding="utf-8", newline="")
    (out_dir / "local-only" / "document-index.json").write_text(json.dumps({
        "note": "Local only. Maps document positions to file names; keep it with the codebook, never with the "
                "derived release.",
        "release_sha256": release_hash, "documents": {str(i): name for i, name in enumerate(names, 1)},
    }, indent=1, ensure_ascii=False), encoding="utf-8")
    (out_dir / "vetting-record.json").write_text(serialised + "\n", encoding="utf-8")
    (out_dir / "vetting-record.md").write_text(markdown, encoding="utf-8")
    return record


# --------------------------------------------------------------------------------------------------------------------
# A release's codebook must outlive the chat vault's idle expiry; retention and disposal are the custodian's rule.
CODEBOOK_IDLE_EXPIRY_SECONDS = 25 * 365 * 24 * 60 * 60


def _codebook_vault(path: Path, config: Mapping[str, Any]):
    """A new vault file for one release's codebook, under the deployment's own key protector."""
    from .protectors import build_protector
    from .vault import Vault

    vault_config = dict(config.get("vault") or {})
    return Vault(
        path, protector=build_protector(vault_config), idle_expiry_seconds=CODEBOOK_IDLE_EXPIRY_SECONDS,
        insecure_test_only=vault_config.get("insecure_test_only", False) is True,
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="themis vet",
        description="Vet a data release (a folder of text files): de-identify it with the gate's own pipeline and "
                    "write a value-free vetting record. Exit 0 = ready for review, 3 = incomplete (unresolved ASKs).",
    )
    parser.add_argument("folder", type=Path)
    parser.add_argument("--out", type=Path, required=True, help="new or empty folder for the outputs")
    parser.add_argument("--codebook", type=Path, required=True, help="new codebook vault file for this release")
    parser.add_argument("--asset", required=True)
    parser.add_argument("--object", required=True)
    parser.add_argument("--release", required=True, help="the release id")
    parser.add_argument("--purpose", required=True)
    parser.add_argument("--route", required=True, choices=ROUTES)
    parser.add_argument("--reference", action="append", default=[],
                        help="a related register id (use case, change request); repeatable")
    parser.add_argument("--resolutions", type=Path, default=None, help="a person's ASK resolutions (JSON)")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)
    from . import integration, startup

    try:
        release = ReleaseInfo(args.asset, args.object, args.release, args.purpose, args.route,
                              tuple(args.reference))
        if args.codebook.exists():
            raise VettingError("codebook file already exists; each release gets its own")
        documents = read_release(args.folder)
        resolutions = load_resolutions(args.resolutions)
        config_path = args.config or integration.default_config_path()
        config = integration.load_config(config_path)
        router = integration.build_router(config_path)
        startup.check_models(router)
        startup.check_canary(router)
        with _codebook_vault(args.codebook, config) as codebook:
            record = vet_release(
                router, documents, release, args.out, resolutions=resolutions, codebook_vault=codebook,
                config_sha256=hashlib.sha256(Path(config_path).read_bytes()).hexdigest(),
                gate_versions=integration.configure_versions(config),
            )
    except (VettingError, startup.StartupRefused, OSError, ValueError, PermissionError) as error:
        print(f"themis vet refused: {error}", file=__import__("sys").stderr)
        return 2
    run = record["part3_run"]
    print(f"{record['status']}: RELEASE {run['decisions']['RELEASE']}, ASK {run['decisions']['ASK']}, "
          f"BLOCK {run['decisions']['BLOCK']}; {run['derived']['documents']} derived documents")
    print(f"record: {args.out / 'vetting-record.md'} (and .json)")
    if run["unresolved_asks"]:
        print(f"unresolved ASKs: documents {', '.join(map(str, run['unresolved_asks']))}; resolve them in a "
              "resolutions file and run again into a new folder")
        return 3
    return 0
