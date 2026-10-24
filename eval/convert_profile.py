#!/usr/bin/env python3
"""Convert the evaluation set's deployment profile into a themis-shield institution profile.

    python eval/convert_profile.py            write themis_shield/profiles/institution/harbourline-eval.json
    python eval/convert_profile.py --check    confirm the shipped file matches a fresh conversion

The evaluation set describes its fictional organisation in its own format (`eval/profiles/harbourline.json`:
`organisation`, `sites`, `programmes`, ...). The gate's allow-list adapter reads the institution-profile format
(`institution`, `campuses`, `courses`, ...). This converter is the only bridge between the two. It copies
institutional facts only: nothing here comes from case text, gold spans or hard-negative annotations.

Choices, all taken from the eval profile's own notes:
- A site's bare name is also a suburb where people live (`site_note`), so it becomes a campus `town`: the adapter
  never allow-lists a form equal to the town. Site addresses are business addresses and are allow-listed.
- `personal_identifier_formats_issued` describes identifiers issued to PEOPLE; it is never converted.
- `finder_do_not_flag` lists the profile's own institutional term kinds with one or two examples each, for the
  finder prompt (prompt v2 reads it; prompt v1 too, when present).
Python 3.12 standard library only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "profiles" / "harbourline.json"
TARGET = HERE.parent / "themis_shield" / "profiles" / "institution" / "harbourline-eval.json"


def _examples(items, count=2):
    picked = [item for item in items if item][:count]
    return ", ".join(picked)


def convert(source: dict) -> dict:
    sites = source.get("sites", [])
    programmes = source.get("programmes", [])
    out = {
        "_about": (
            "Institution profile for the fictional Harbourline Institute of the synthetic evaluation set, converted "
            "from eval/profiles/harbourline.json by eval/convert_profile.py (do not edit by hand). Every value is "
            "institutional and invented; none is personal information."
        ),
        "institution": source["organisation"],
        "institution_forms": [form for form in source.get("organisation_forms", []) if form != source["organisation"]],
        "email_domain": source.get("email_domain"),
        "campuses": [
            {
                "town": site["name"],
                "forms": list(site.get("forms", [])) + ([site["address"]] if site.get("address") else []),
                **({"reception": site["reception"]} if site.get("reception") else {}),
            }
            for site in sites
        ],
        "buildings": list(source.get("buildings", [])),
        "role_mailboxes": list(source.get("role_mailboxes", [])),
        "main_lines": [dict(line) for line in source.get("main_lines", [])],
        "reception_numbers": list(source.get("reception_numbers", [])),
        "role_titles": list(source.get("role_titles", [])),
        "programmes": [{"code": item["code"], "title": item["title"]} for item in programmes],
        "projects": [{"code": item["code"], "title": item["title"]} for item in source.get("research_projects", [])],
        "committees": list(source.get("committees", [])),
        "initiatives": list(source.get("initiatives", [])),
        "code_formats": dict(source.get("code_formats", {})),
        "reference_number_formats": dict(source.get("reference_number_formats", {})),
        "public_dates": [{"event": item["event"], "date": item["date"]} for item in source.get("public_dates", [])],
    }
    business = source.get("business_identifier") or {}
    if business.get("value"):
        out["abn"] = business["value"]
        out["abn_forms"] = list(business.get("forms") or [business["value"]])
    # Term kinds the finder must not list, each with examples from this profile only.
    dnf = [f"the organisation's own name ({_examples([source['organisation']] + out['institution_forms'], 3)})"]
    if sites:
        dnf.append(f"site and campus names used as places of work ({_examples([s['forms'][0] for s in sites if s.get('forms')])})")
    if out["buildings"]:
        dnf.append(f"building and room names ({_examples(out['buildings'])})")
    if programmes:
        dnf.append(
            f"programme, course and project codes and titles ({programmes[0]['code']}, {programmes[0]['title']})"
        )
    if out["committees"] or out["initiatives"]:
        dnf.append(
            "committee, staff-network and initiative names, even when they contain words such as health, pride, "
            f"faith or union ({_examples(out['committees'][-2:] + out['initiatives'][:1], 3)})"
        )
    if out["role_titles"]:
        dnf.append(f"role titles without a person's name ({_examples(out['role_titles'])})")
    if out["role_mailboxes"]:
        dnf.append(f"shared role mailboxes ({_examples(out['role_mailboxes'])})")
    if out["reception_numbers"]:
        dnf.append("reception, switchboard and enquiry numbers")
    if out.get("abn"):
        dnf.append("the organisation's own business number")
    if out["public_dates"]:
        dnf.append("dates of public events (open days, ceremonies, closures)")
    out["finder_do_not_flag"] = dnf
    return out


def render(source_path: Path = SOURCE) -> str:
    source = json.loads(source_path.read_text(encoding="utf-8"))
    return json.dumps(convert(source), indent=1, ensure_ascii=False) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    text = render()
    if a.check:
        ok = TARGET.is_file() and TARGET.read_text(encoding="utf-8") == text
        print("CHECK OK" if ok else f"CHECK FAILED: {TARGET} differs from a fresh conversion")
        return 0 if ok else 1
    TARGET.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
