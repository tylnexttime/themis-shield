"""Step 0, the probes (NOT part of the split; throwaway specifications and texts invented here, kept in probe/).

    python probe_all.py tiny                 one tiny request per model code (a few tokens each)
    python probe_all.py author A|B [--tag t] [--only 1|2|3]   two throwaway specifications through the real prompts
    python probe_all.py labellers            labeller + pointer prompts: one probe text, trivial-text taxonomy sizes, pointer
                                             prompt on three texts (the probe text and two synthetic real-world positives)

Prints counts and rule codes only (never a document). The documents stay in results/<half>/probe/."""
from __future__ import annotations

import json
import sys
from collections import Counter

import agreement as A
import common as C
import llm
import pipeline
import prompts
import safevalues as sv
import validate as VAL
from markup import parse_markup

PROBE_DIR = C.ROOT / "probe"
PROBE_DIR.mkdir(exist_ok=True)


def show(tag, r):
    u = r.get("usage") or {}
    d = u.get("completion_tokens_details") or {}
    print(tag, "ok" if r["ok"] else "FAILED", r["http_status"], r["finish_reason"], "prompt", u.get("prompt_tokens"),
          "completion", u.get("completion_tokens"), "reasoning", d.get("reasoning_tokens"), "cached",
          (u.get("prompt_tokens_details") or {}).get("cached_tokens"), "chars_out", len(r["content"]), "secs",
          r.get("seconds"), "cost", r.get("cost"), "serving", r.get("serving_provider"), "err", r["error"])


def call(model, phase_half, case_id, messages, max_tokens, temperature=0, thinking=None, extra=None, stream=None):
    st = C.settings(model)
    return llm.call(half=phase_half, phase="probe", case_id=case_id, model=model, messages=messages, max_tokens=max_tokens,
                    temperature=temperature, thinking=st["thinking"] if thinking is None else thinking,
                    stream=st["stream"] if stream is None else stream, extra=extra if extra is not None else st.get("extra"),
                    force=True)


def tiny() -> int:
    for model in ("mistralai/mistral-large-2512", "openai/gpt-oss-120b", "deepseek-v4.1-flash", "kimi-k3.1-pro"):
        try:
            r = call(model, "P", f"tiny-{model.replace('/', '_')}", [{"role": "user", "content": "Reply with the single word OK."}],
                     200 if "gpt-oss" in model else 32)
            show(model, r)
        except llm.AliError as exc:
            print(model, "REFUSED/STOP", type(exc).__name__, str(exc)[:200])
    return 0


def mini_profile() -> dict:
    vals = sv.Values(777)
    abn = vals.abn()
    digits = "".join(ch for ch in abn if ch.isdigit())
    return {
        "_about": "Throwaway mini profile used only for the author probe; not part of any split.",
        "organisation": "Larkspur Ridge Lighthouse Trust", "organisation_forms": ["LRLT", "The Larkspur Ridge Lighthouse Trust"],
        "description": "A small invented lighthouse trust used for the probe only.",
        "business_identifier": {"scheme": "ABN", "value": abn, "forms": [abn, digits]}, "email_domain": "lrlt.example",
        "sites": [{"name": "Cape Merrin Station", "forms": ["Cape Merrin Station"], "address": "3 Beacon Road, Cape Merrin VIC 3999",
                   "reception": "(03) 5550 0241"},
                  {"name": "Tolland Head Depot", "forms": ["Tolland Head Depot"], "address": "9 Quay Lane, Tolland Head VIC 3998",
                   "reception": "(03) 5550 0242"}],
        "site_note": "A site's bare name is also a town where people live.",
        "buildings": ["Lamp Room Annex", "Stores Hall", "Admin Block C"],
        "role_mailboxes": ["enquiries@lrlt.example", "privacy@lrlt.example", "servicedesk@lrlt.example"],
        "main_lines": ["(03) 5550 0200"], "reception_numbers": ["(03) 5550 0241", "(03) 5550 0242"],
        "role_titles": ["Lamp Technician", "Station Supervisor", "Visitor Services Officer", "Privacy Officer", "Fleet Coordinator"],
        "programmes": [{"code": "LRT-01", "title": "Keeper Heritage Tours"}, {"code": "LRT-02", "title": "Apprentice Rigging Course"}],
        "research_projects": [{"code": "RP-LR-1", "title": "Lens Restoration Study"}],
        "committees": ["Work Health and Safety Committee", "Staff Pride Network"],
        "initiatives": ["Open Lighthouse Day", "Mental Health Week"], "committee_note": "Institutional names, not statements about people.",
        "code_formats": {"programme_code": "LRT-##"},
        "reference_number_formats": {"ticket_number": "TK-######", "case_number": "CS-@@-####", "reference_number": "REF-#####",
                                     "order_number": "PO-######", "invoice_number": "INV-#####", "asset_id": "AST-#####",
                                     "cost_centre": "CC####", "form_number": "LF-###", "provider_number": "PRV-######",
                                     "client_account": "CA-#######", "software_version": "v#.#.#", "build_number": "B#####",
                                     "payroll_group": "PG-##"},
        "public_dates": [{"event": "Open Lighthouse Day", "date": "21 March 2027"}],
        "personal_identifier_formats_issued": {"staff_number": "LRLT-S######", "client_number": "LRLT-C#####", "volunteer_number": "LRLT-V####"},
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
           "hard": {"technique": "phone number spelled out in words (digits as words, double and triple allowed)", "label": "PHONE"},
           "scripts": [{"language": "Japanese", "mode": "names"}],
           "values": {"PHONE": [vals.phone("mobile")], "EMAIL": [vals.email(None)], "TFN": [vals.tfn()]}}
    two = {**base, "id": f"PROBE-{half}-2", "genre": "markdown table", "chars": [900, 1800], "org": {"kind": "profiled"},
           "labels": {"QUASI_ID": 3, "DATE_OF_BIRTH": 3, "ADDRESS": 3}, "quasi_table": True,
           "sensitive": [{"category": "HEALTH", "mode": "indirect"}, {"category": "DISABILITY", "mode": "direct"}],
           "hn_kinds": ["site", "role_title", "reference_number", "programme_code"]}
    three = {**base, "id": f"PROBE-{half}-3", "genre": "case note", "chars": [1500, 2800],
             "org": {"kind": "other", "k": 2, "sector": "residential aged-care provider", "where": "Perth"},
             "labels": {"PERSON": 4, "OTHER_DATE": 1},
             "sensitive": [{"category": "HEALTH", "mode": "indirect"}, {"category": "UNION", "mode": "indirect"},
                           {"category": "FAMILY_VIOLENCE", "mode": "negative"}, {"category": "ETHNICITY", "mode": "direct"}],
             "hn_kinds": ["role_title", "case_number", "meeting_date"]}
    return [one, two, three]


def authors(argv: list[str]) -> int:
    half = argv[0].upper()
    h = C.Half(half)
    only = argv[argv.index("--only") + 1] if "--only" in argv else None
    tag = argv[argv.index("--tag") + 1] if "--tag" in argv else "a"
    model = h.models["author"]
    st = C.settings(model)
    prof = mini_profile()
    (PROBE_DIR / "probe-profile.json").write_text(json.dumps(prof, indent=1), encoding="utf-8")
    sps = specs(half)
    (PROBE_DIR / f"specs-{half}.json").write_text(json.dumps(sps, ensure_ascii=False, indent=1), encoding="utf-8")
    for e in sps:
        if only and not e["id"].endswith(only):
            continue
        system = prompts.author_system(prof, e["org"]["kind"] == "profiled")
        try:
            r = call(model, half, f"{e['id']}-{tag}", [{"role": "system", "content": system},
                                                      {"role": "user", "content": prompts.user_spec(e, prof)}],
                     8000, temperature=st.get("author_temperature", 0.7))
        except llm.AliError as exc:
            print(e["id"], "STOP", type(exc).__name__, str(exc)[:200])
            return 2
        show(e["id"] + " " + model, r)
        if not r["ok"]:
            continue
        case, parsed = VAL.parse_output(e, r["content"], prof, half)
        iss = VAL.entry_issues(e, case, parsed) + VAL.validator_issues([case], prof, half).get(e["id"], [])
        print("   system chars", len(system), "parse errors", len(parsed["errors"]), "closer_fixed", parsed["closer_fixed"],
              "spans", len(case["spans"]), "hns", len(case["hard_negatives"]), "inj", len(parsed["injections"]),
              "text_chars", len(case["text"]), "issue codes", dict(Counter(c for c, _ in iss)),
              "label counts", dict(Counter(s["label"] for s in case["spans"])))
    return 0


# two synthetic real-world positives, written afresh for this probe (a real company and a famous real person each tied to
# an invented private person's misconduct); never part of the split
POS1 = ("Incident note, 14 March. Customer service agent Tahlia Brandt at the Woolworths supermarket in Ballarat was stood "
        "down on Tuesday after management found she had been taking cash from the till. Woolworths has not commented. "
        "Tahlia has worked there since 2019 and is on a final written warning.")
POS2 = ("Court report. Daniel Pryce, the personal driver of Hugh Jackman, was charged on Friday with fraud after he "
        "allegedly sold confidential schedules of the actor to a tabloid. Mr Pryce is on bail and will appear again in "
        "May. His employment with Hugh Jackman has ended.")


def labellers() -> int:
    cand = sorted((C.ROOT / "results" / "a" / "probe").glob("PROBE-A-1-*.json")) + sorted((C.ROOT / "results" / "b" / "probe").glob("PROBE-B-1-*.json"))
    raw = None
    for p in reversed(cand):
        d = json.loads(p.read_text(encoding="utf-8"))
        if d.get("ok") and parse_markup(d["content"])["spans"]:
            raw = d["content"]
            break
    if raw is None:
        print("no author probe output")
        return 2
    parsed = parse_markup(raw)
    plain = parsed["plain"]
    gold = {(s["start"], s["end"], s["label"]) for s in parsed["spans"]}
    out = {}
    for model, half in (("kimi-k3.1-pro", "A"), ("openai/gpt-oss-120b", "B")):
        st = C.settings(model)
        for mode, system in (("full", prompts.taxonomy_text()), ("compact", prompts.taxonomy_compact())):
            if model == "openai/gpt-oss-120b" and mode == "compact":
                continue
            for what, text in (("doc", plain), ("trivial", "Hello.")):
                if model == "kimi-k3.1-pro" and mode == "compact" and what == "doc":
                    continue
                if model == "openai/gpt-oss-120b" and what == "trivial":
                    continue
                try:
                    r = call(model, half, f"LABEL-{mode}-{what}-{model.replace('/', '_')}",
                             [{"role": "system", "content": system}, {"role": "user", "content": prompts.label_user(text)}],
                             pipeline.label_max_tokens(text) if what == "doc" else 200, temperature=0)
                except llm.AliError as exc:
                    print(model, "STOP", type(exc).__name__, str(exc)[:200])
                    return 2
                show(f"label {mode} {what} {model}", r)
                out[f"{model}|{mode}|{what}"] = {"prompt": (r["usage"] or {}).get("prompt_tokens"),
                                                 "completion": (r["usage"] or {}).get("completion_tokens"), "chars": len(text)}
                if r["ok"] and what == "doc":
                    d = A.labeller_spans(plain, r["content"])
                    ex = {(s["start"], s["end"], s["label"]) for s in d["spans"]}
                    print("   labeller spans", len(d["spans"]), "gold spans", len(gold), "exact matches", len(ex & gold),
                          "drift", d["drift"], "dropped", d["dropped"], "markup errors", d["errors"])
    (PROBE_DIR / "label-probe-sizes.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    model = "openai/gpt-oss-120b"
    for name, text in (("probe-doc", plain), ("positive-1", POS1), ("positive-2", POS2)):
        r = call(model, "B", f"POINTER-{name}", [{"role": "system", "content": prompts.POINTER_SYSTEM},
                                                 {"role": "user", "content": prompts.pointer_user(text)}], 1500, temperature=0)
        show("pointer " + name, r)
        flag = pipeline.parse_answer(r["content"]) if r["ok"] else None
        print("   parsed flag", None if flag is None else flag[0], "reason_chars", None if flag is None else len(flag[1]))
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1]
    sys.exit({"tiny": lambda: tiny(), "author": lambda: authors(sys.argv[2:]), "labellers": lambda: labellers()}[cmd]())
