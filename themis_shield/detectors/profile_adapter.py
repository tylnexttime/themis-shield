"""Deployment-profile adaptation; no annotation-derived exemptions.

"""

from __future__ import annotations

from collections.abc import Mapping

from ..config import DeploymentProfile


FORBIDDEN_ANNOTATION_KEYS = {
    "text", "spans", "hard_negatives", "case_level", "gold", "cases",
}


def bounded(pattern: str) -> str:
    alternatives = [
        alternative.strip().removeprefix("^").removesuffix("$")
        for alternative in pattern.strip().split("|")
    ]
    return r"\b(?:" + "|".join(alternatives) + r")\b"


def adapt(profile: DeploymentProfile | Mapping) -> dict:
    if isinstance(profile, DeploymentProfile):
        base = {
            "literals": list(profile.allowlist_literals),
            "patterns": list(profile.allowlist_patterns),
            "date_literals": list(profile.public_date_literals),
            "role_mailbox_local_parts": [],
            "institution_email_domains": [],
        }
        if profile.institution:
            # An institution profile loaded from the (external) profiles directory.
            facts = adapt(profile.institution)
            for key in ("literals", "patterns", "date_literals"):
                facts[key] = list(facts.get(key, [])) + base[key]
            return facts
        return base
    if not isinstance(profile, Mapping):
        raise ValueError("invalid-deployment-profile")
    if FORBIDDEN_ANNOTATION_KEYS.intersection(profile):
        raise ValueError("annotation-profile-forbidden")
    if not any(key in profile for key in ("campuses", "courses", "institution")):
        return {
            **dict(profile),
            "literals": list(profile.get("literals", [])),
            "patterns": list(profile.get("patterns", [])),
            "date_literals": list(profile.get("date_literals", [])),
        }

    towns = {campus["town"].casefold() for campus in profile.get("campuses", [])}
    literals = []
    for campus in profile.get("campuses", []):
        literals.extend(
            form for form in campus.get("forms", [])
            if form.casefold() not in towns
        )
        if campus.get("reception"):
            literals.append(campus["reception"])
    for site in profile.get("other_sites", []):
        literals.extend(site.get("forms", []))
        literals.append(site["name"])
        if site.get("reception"):
            literals.append(site["reception"])
    for key in (
        "buildings", "role_mailboxes", "reception_numbers", "role_titles",
        # Optional: other names of the organisation, and committee, staff-network and initiative names. They may
        # contain sensitive-sounding words but name no person.
        "institution_forms", "committees", "initiatives",
    ):
        literals.extend(profile.get(key, []))
    literals.extend(line["number"] for line in profile.get("main_lines", []))
    for key in ("courses", "units", "programmes", "projects"):
        for item in profile.get(key, []):
            literals.extend([
                item["code"], item["title"], " ".join(part for part in (item["code"], item["title"]) if part),
            ])
    literals.extend(profile.get("provider_code_forms", []))
    literals.extend(profile.get("abn_forms", []))
    if profile.get("institution"):
        literals.append(profile["institution"])
    patterns = [
        bounded(pattern)
        for key in ("code_formats", "reference_number_formats")
        for pattern in (profile.get(key) or {}).values()
    ]
    mailboxes = profile.get("role_mailboxes", [])
    return {
        "literals": sorted(set(value for value in literals if value)),
        "patterns": patterns,
        "role_mailbox_local_parts": sorted({
            mailbox.split("@", 1)[0] for mailbox in mailboxes
        }),
        "institution_email_domains": (
            [profile["email_domain"]] if profile.get("email_domain") else []
        ),
        "institute_abn": (
            (profile.get("abn_forms") or [profile.get("abn")])[0]
        ),
        "provider_code": profile.get("provider_code"),
        "provider_code_labels": list(profile.get("provider_code_labels", [])),
        "date_literals": [
            item["date"] for item in profile.get("public_dates", [])
        ],
    }
