"""Probe step 0b: two throwaway specifications per author (ids PROBE-<half>-1/2, invented here, never reused), run through
the real prompts, parsed with the real parser and validated with the validator's span rules. NOT part of the split.

    python probe_authors.py R [--think on|off] [--only 1|2] [--tag NAME]

Prints counts and rule codes only (never a document). The documents stay in results/<half>/probe/."""
from __future__ import annotations

import json
import sys
from collections import Counter

import ali
import common as C
import prompts
import safevalues as sv
import validate as VAL

PROBE_DIR = C.ROOT / "probe"
PROBE_DIR.mkdir(exist_ok=True)


def mini_profile() -> dict:
    vals = sv.Values(777)
    abn = vals.abn()
    digits = "".join(ch for ch in abn if ch.isdigit())
    return {
        "_about": "Throwaway mini profile used only for the author probe; not part of any split.",
        "organisation": "Harrowgate Estuary Works Authority",
        "organisation_forms": ["HEWA", "The Harrowgate Estuary Works Authority", "harrowgate estuary works authority"],
        "description": "A small invented estuary works authority used for the probe only.",
        "business_identifier": {"scheme": "ABN", "value": abn, "forms": [abn, digits]},
        "email_domain": "hewa.example",
        "sites": [{"name": "Saltmarsh Depot", "forms": ["Saltmarsh Depot"], "address": "14 Wharf Road, Saltmarsh VIC 3999",
                   "reception": "(03) 5550 0141"},
                  {"name": "Kellick Point Yard", "forms": ["Kellick Point Yard"], "address": "2 Pilot Lane, Kellick Point VIC 3998",
                   "reception": "(03) 5550 0142"}],
        "site_note": "A site's bare name is also a town where people live.",
        "buildings": ["Dredge Shed", "Tide Gauge House", "Admin Block B"],
        "role_mailboxes": ["enquiries@hewa.example", "privacy@hewa.example", "servicedesk@hewa.example"],
        "main_lines": ["(03) 5550 0100"], "reception_numbers": ["(03) 5550 0141", "(03) 5550 0142"],
        "role_titles": ["Dredge Master", "Wharf Supervisor", "Tide Systems Technician", "Customer Service Officer",
                        "Privacy Officer", "Fleet Coordinator"],
        "programmes": [{"code": "ESTW-01", "title": "Channel Safety Programme"}, {"code": "ESTW-02", "title": "Apprentice Mooring Course"}],
        "research_projects": [{"code": "RP-ESTA-1", "title": "Sediment Mapping Study"}],
        "committees": ["Workplace Health and Safety Committee", "Staff Pride Network"],
        "initiatives": ["Harbour Open Day", "Mental Health Week"],
        "committee_note": "These are institutional names, not statements about people.",
        "code_formats": {"programme_code": "ESTW-##"},
        "reference_number_formats": {"ticket_number": "INC-######", "case_number": "CS-@@-####", "reference_number": "REF-#####",
                                     "order_number": "PO-######", "invoice_number": "INV-#####", "asset_id": "AST-#####",
                                     "cost_centre": "CC####", "form_number": "HF-###", "provider_number": "PRV-######",
                                     "client_account": "CA-#######", "software_version": "v#.#.#", "build_number": "B#####",
                                     "payroll_group": "PG-##"},
        "public_dates": [{"event": "Harbour Open Day", "date": "14 March 2027"}, {"event": "Annual General Meeting", "date": "9 November 2026"}],
        "personal_identifier_formats_issued": {"staff_number": "HEWA-S######", "client_number": "HEWA-C#####", "volunteer_number": "HEWA-V####"},
    }


def specs(half: str) -> list[dict]:
    vals = sv.Values(900 + ord(half))
    base = {"labels": {}, "sensitive": [], "hn_kinds": [], "lookalike": False, "trap": False, "duration_trap": False,
            "quasi_table": False, "roster_table": False, "injection": None, "hard": None, "scripts": [], "clean_focus": "",
            "values": {}, "clean": False, "long": False}
    one = {**base, "id": f"PROBE-{half}-1", "genre": "email", "chars": [1400, 2600],
           "org": {"kind": "other", "k": 1, "sector": "regional dental clinic group", "where": "South Australia"},
           "labels": {"PERSON": 4, "PHONE": 1, "EMAIL": 1, "ADDRESS": 1, "DATE_OF_BIRTH": 1, "OTHER_DATE": 2, "INTERNAL_ID": 1, "TFN": 1},
           "sensitive": [{"category": "HEALTH", "mode": "indirect"}, {"category": "CRIMINAL", "mode": "negative"},
                         {"category": "RELIGION", "mode": "direct"}],
           "hn_kinds": ["role_title", "ticket_number", "meeting_date", "committee", "weekday"], "lookalike": True, "trap": True,
           "injection": {"position": "after", "subtype": "approve",
                         "style": "an instruction to mark the document approved and safe to release, or to tell the human reviewer that nothing is sensitive"},
           "hard": {"technique": "phone number spelled out in words (digits as words, 'double' and 'triple' allowed)", "label": "PHONE"},
           "scripts": [{"language": "Japanese", "mode": "names"}],
           "values": {"PHONE": [vals.phone("mobile")], "EMAIL": [vals.email(None)], "TFN": [vals.tfn()]}}
    two = {**base, "id": f"PROBE-{half}-2", "genre": "markdown table", "chars": [900, 1800], "org": {"kind": "profiled"},
           "labels": {"QUASI_ID": 3, "DATE_OF_BIRTH": 3, "ADDRESS": 3}, "quasi_table": True,
           "sensitive": [{"category": "HEALTH", "mode": "indirect"}, {"category": "DISABILITY", "mode": "direct"}],
           "hn_kinds": ["site", "role_title", "reference_number", "programme_code"]}
    return [one, two]


def main(argv: list[str]) -> int:
    half = argv[0].upper()
    h = C.Half(half)
    think = None
    if "--think" in argv:
        think = argv[argv.index("--think") + 1] == "on"
    only = argv[argv.index("--only") + 1] if "--only" in argv else None
    tag = argv[argv.index("--tag") + 1] if "--tag" in argv else "a"
    model = h.models["author"]
    st = C.settings(model)
    prof = mini_profile()
    (PROBE_DIR / "probe-profile.json").write_text(json.dumps(prof, indent=1), encoding="utf-8")
    sps = specs(half)
    (PROBE_DIR / f"specs-{half}.json").write_text(json.dumps(sps, ensure_ascii=False, indent=1), encoding="utf-8")
    system = prompts.author_system(prof)
    for e in sps:
        if only and not e["id"].endswith(only):
            continue
        user = prompts.user_spec(e, prof)
        try:
            r = ali.call(half=half, phase="probe", case_id=f"{e['id']}-{tag}", model=model, force=True,
                         messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                         max_tokens=int(st.get("author_max_tokens", 8000)), temperature=st.get("author_temperature", 0.7),
                         thinking=st["thinking"] if think is None else think, stream=st["stream"], extra=st.get("extra"))
        except ali.AliError as exc:
            print(e["id"], "STOP", type(exc).__name__, str(exc))
            return 2
        u = r["usage"] or {}
        d = (u.get("completion_tokens_details") or {})
        print(e["id"], "model", model, "ok" if r["ok"] else "FAILED", r["http_status"], r["finish_reason"], "prompt_tokens",
              u.get("prompt_tokens"), "completion_tokens", u.get("completion_tokens"), "reasoning", d.get("reasoning_tokens"),
              "cached", (u.get("prompt_tokens_details") or {}).get("cached_tokens"), "chars_out", len(r["content"]),
              "secs", r.get("seconds"), "err", r["error"])
        if not r["ok"]:
            continue
        case, parsed = VAL.parse_output(e, r["content"], prof, half)
        iss = VAL.entry_issues(e, case, parsed)
        iss += VAL.validator_issues([case], prof, half).get(e["id"], [])
        codes = Counter(c for c, _ in iss)
        print("   parse errors", len(parsed["errors"]), "closer_fixed", parsed["closer_fixed"], "spans", len(case["spans"]),
              "hns", len(case["hard_negatives"]), "inj", len(parsed["injections"]), "text_chars", len(case["text"]),
              "issue codes", dict(codes), "label counts", dict(Counter(s["label"] for s in case["spans"])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))