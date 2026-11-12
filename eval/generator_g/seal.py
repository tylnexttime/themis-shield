"""Step 8: build cases.jsonl for a half from the pipeline state, run validator + denylist + extra quotas, seal.

    python seal.py check R     build halves/r/cases.jsonl and run the three checks (no sealing)
    python seal.py seal R      all of the above, then seal (sealed/half-r.jsonl, .sha256, profile-r.json, profiles)
    python seal.py final       manifest.json and PROVENANCE.md over the halves that sealed
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

import common as C
import finalize as F
import runner
from store import load_entries, load_profile, load_state

ROOT = C.ROOT


def counts(cases: list[dict], profile: dict) -> dict:
    labels = Counter(s["label"] for c in cases for s in c["spans"])
    prof = [c for c in cases if c["organisation"] == profile["organisation"]]
    inj = [c for c in cases if c.get("injection")]
    return {
        "cases": len(cases), "clean_documents": sum(1 for c in cases if not c["spans"]),
        "gold_spans": sum(labels.values()), "hard_negatives": sum(len(c["hard_negatives"]) for c in cases),
        "by_label": dict(sorted(labels.items())), "by_genre": dict(sorted(Counter(c["genre"] for c in cases).items())),
        "by_organisation_kind": {"profiled": len(prof), "other_workplace": len(cases) - len(prof),
                                 "other_workplaces_distinct": len({c["organisation"] for c in cases} - {profile["organisation"]})},
        "by_injection_position": dict(Counter(c["injection"]["position"] for c in inj)),
        "injected_cases": len(inj), "injected_cases_without_pi": sum(1 for c in inj if not c["spans"]),
        "hard_to_spot_cases": sum(1 for c in cases if c.get("hard_to_spot")),
        "long_documents": sum(1 for c in cases if 9000 <= len(c["text"]) <= 24000),
        "sensitive_spans": {"total": sum(1 for c in cases for s in c["spans"] if "indirect" in s),
                            "indirect": sum(1 for c in cases for s in c["spans"] if s.get("indirect")),
                            "negative": sum(1 for c in cases for s in c["spans"] if s.get("negative"))},
        "characters_total": sum(len(c["text"]) for c in cases),
    }


def build_and_check(h: C.Half) -> tuple[list[dict], bool, str]:
    cases = F.valid_cases(h)
    F.write_cases(h.cases_path, cases)
    rc, text = F.run_validator(h, h.cases_path)
    rc2, dtext = F.run_denylist(h, h.cases_path)
    rc3, etext = F.run_extra(h, h.cases_path)
    out = text + "\n\n" + dtext + "\n\n" + etext
    (h.dir / "rounds" / "last_check.txt").write_text(out, encoding="utf-8")
    return cases, rc == 0 and rc2 == 0 and rc3 == 0, out


def _ro(p: Path) -> None:
    os.chmod(p, stat.S_IREAD)


def seal_half(h: C.Half) -> int:
    cases, ok, out = build_and_check(h)
    print(out)
    result = {"half": h.L, "checks_pass": ok, "cases": len(cases)}
    if not ok:
        result["verdict"] = "NOT SEALED: a check failed (dropped, not hand-edited)"
        (h.dir / "work" / "seal-result.json").write_text(json.dumps(result), encoding="utf-8")
        print(h.L, result["verdict"])
        return 1
    profile = load_profile(h)
    (ROOT / "sealed").mkdir(exist_ok=True)
    sealed = ROOT / "sealed" / f"half-{h.l}.jsonl"
    prof_s = ROOT / "sealed" / f"profile-{h.l}.json"
    for p in (sealed, prof_s, ROOT / "sealed" / f"half-{h.l}.sha256"):
        if p.exists():
            os.chmod(p, stat.S_IWRITE)
    shutil.copyfile(h.cases_path, sealed)
    digest = C.sha256_file(sealed)
    sha_path = ROOT / "sealed" / f"half-{h.l}.sha256"
    sha_path.write_text(f"{digest}  half-{h.l}.jsonl\n", encoding="utf-8", newline="\n")
    shutil.copyfile(h.profile_path, prof_s)
    r = subprocess.run([C.PY, str(ROOT / "prep_profiles.py"), h.l], capture_output=True, text=True, encoding="utf-8")
    print(r.stdout.strip(), (r.stderr or "")[-300:])
    for p in (sealed, prof_s, sha_path):
        _ro(p)
    result.update({"verdict": "SEALED", "sha256": digest})
    (h.dir / "work" / "seal-result.json").write_text(json.dumps(result), encoding="utf-8")
    print(h.L, "sealed", digest)
    return 0


# ------------------------------------------------------------------ manifest and provenance
def half_manifest(h: C.Half) -> dict | None:
    res = C.jload(h.dir / "work" / "seal-result.json", {})
    if res.get("verdict") != "SEALED":
        return None
    cases = C.read_jsonl(ROOT / "sealed" / f"half-{h.l}.jsonl")
    profile = load_profile(h)
    st = load_state(h)
    rounds = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (h.dir / "rounds").glob("*.json")}
    models = h.models
    sub = [x for x in C.substitutions() if x["half"] == h.L]
    m = {"half": h.L, "id_prefix": h.prefix, "generator": h.L, "split": "test-g", "organisation_kind": h.cfg["org_kind"],
         "counts": counts(cases, profile), "seed_plan": h.cfg["seed"], "seed_safe_values_plan": h.cfg["seed"] + 1,
         "seed_safe_values_topup": h.cfg["seed"] + 2, "seed_topup_choices": h.cfg["seed"] + 3,
         "models_assigned": {"author": h.cfg["author"], "labeller": h.cfg["labeller"]},
         "models_final": models, "settings": {m_: C.settings(m_) for m_ in {h.cfg["author"], h.cfg["labeller"], *models.values()}},
         "substitutions": sub,
         "sha256": {"cases": res["sha256"], "plan": C.sha256_file(h.plan),
                    "plan_topup": C.sha256_file(h.plan_topup) if h.plan_topup.exists() else None,
                    "profile": C.sha256_file(ROOT / "sealed" / f"profile-{h.l}.json"),
                    "institution_profile": C.sha256_file(ROOT / "profiles" / "institution" / f"test-g-{h.l}.json"),
                    "context": C.sha256_file(ROOT / "profiles" / f"context-{h.l}.txt"),
                    "agreement_md": C.sha256_file(h.agreement_path) if h.agreement_path.exists() else None},
         "cases_planned": len(C.read_jsonl(h.plan)) + len(C.read_jsonl(h.plan_topup)),
         "dropped_total": sum(1 for r in st.values() if r["status"] == "dropped"),
         "status_totals": dict(Counter(r["status"] for r in st.values())),
         "profile_fixes": (rounds.get("profile", {}).get("tries") or [{}])[-1].get("fixes"),
         "rounds": {k: v for k, v in rounds.items() if k.startswith(("ingest", "review", "agreement", "topup"))}}
    return m


def provenance(man: dict, summ: dict) -> str:
    H = man["halves"]
    L = ["# PROVENANCE: split G (three halves of per-document prose, written and labelled by non-Claude models)", "",
         "Counts, hashes, token numbers, file names and model codes only; no case, value, name or profile term is quoted in "
         "this file. Everything in the set is invented.", "",
         "**Data handling:** Alibaba Cloud's published Model Studio FAQ says customer data is not used for training; no "
         "zero-retention setting is documented: no training per the provider's terms, retention not stated. All content is "
         "invented.", "",
         "## Who made what", "",
         "| half | profiled organisation (invented) | author (profile, documents, repairs, top-up, review decisions) | blind labeller (labelling, pointer check) |",
         "|---|---|---|---|"]
    for k, m in sorted(H.items()):
        fin = m["models_final"]
        L.append(f"| {k} (`G{k}-`, sealed `sealed/half-{k.lower()}.jsonl`) | {m['organisation_kind']} | `{m['models_assigned']['author']}`"
                 + (f" (final role holder: `{fin['author']}`)" if fin["author"] != m["models_assigned"]["author"] else "")
                 + f" | `{m['models_assigned']['labeller']}`"
                 + (f" (final role holder: `{fin['labeller']}`)" if fin["labeller"] != m["models_assigned"]["labeller"] else "") + " |")
    L += ["", "| artefact | made by | from |", "|---|---|---|",
          "| `halves/<x>/plan.jsonl` (about 99 specifications per half) | `plan.py`, deterministic, one fixed seed per half (seeds in `manifest.json`); checksum / range-restricted values by `safevalues.py` (own code, cross-checked against the validator) | `inputs/brief.g.md` quotas (split F's plan scaled to about 100 cases) |",
          "| `profile.json` | one chat-completions call to the half's author (`profile.py`); ABN, phones and domain checked and, if invalid, regenerated by code (fix counts in `manifest.json`) | `inputs/profile-schema.md` and the task text |",
          "| the documents | one chat-completions request per plan entry (`pipeline.py`); system = `inputs/brief.g.md` + `inputs/taxonomy.g.compact.md` + the minified profile, identical across the half's requests; user = the case specification | the plan entry, the safe values |",
          "| offsets, `case_level`, `organisation`, flags | `markup.py` / `validate.py` (code); never a model | the inline markup |",
          "| repair, top-up, review decisions | the half's author (a substitute where `manifest.json` records one); review fixes applied by script only when the plain text is identical and the validator is clean | rule codes and pointer flags / the labeller's disagreement items |",
          "| real-world pointer check | the half's labeller, plain text only, JSON `{flag, reason}` | prompt v1 (halves S, T) / v2 (half R, see Notes) |",
          "| blind labelling | the half's labeller, system = `inputs/taxonomy.md` only, user = plain text | the plain text only |",
          "| validation | `validator/check_cases.py` (split-F copy, protocol constants only, see `validator/VALIDATOR.md`), `validator/denylist_check.py` (list passed, never printed), `extra_quotas.py` | `halves/<x>/cases.jsonl` |", "",
          "## Model settings (and why)", "",
          "Alibaba Model Studio, OpenAI-compatible chat completions, streamed with `stream_options.include_usage`, at most 4 requests in flight per model. Token guards in `ali.py` (2,000,000 per model, 8,000,000 in total, read from `usage.jsonl` before every request). The probe findings below are measurements, not opinions:", ""]
    pf = man.get("probe_findings", {})
    for k, v in pf.items():
        L.append(f"- {k}: {v}")
    L += ["", "| model | role(s) | thinking | temperature | other request fields | max_tokens policy |", "|---|---|---|---|---|---|"]
    seen = {}
    for k, m in sorted(H.items()):
        for mm, st in m["settings"].items():
            seen[mm] = st
    for mm, st in sorted(seen.items()):
        th = {True: "on", False: "off", None: "model default"}[st.get("thinking")]
        temp = "author: " + str(st.get("author_temperature", 0.7)) + ", labeller / pointer: 0"
        L.append(f"| `{mm}` | {', '.join(sorted({r for m in H.values() for r, v in m['models_assigned'].items() if v == mm})) or 'substitute'} | {th} | {temp} | {json.dumps(st.get('extra')) if st.get('extra') else 'none'} | authors: characters of the length band / 1.3 + 2,000 (+4,000 if thinking is not off), at most 30,000; labeller: characters / 1.2 + 3,000; pointer: 600 |")
    L += ["", "## Tokens (as returned in `usage`; per phase in `usage-summary.md`)", "",
          "| model | calls | prompt | completion | reasoning (inside completion) | cached (inside prompt) | total |", "|---|---|---|---|---|---|---|"]
    for mm, d in sorted(summ["models"].items()):
        c = d["cumulative"]
        L.append(f"| `{mm}` | {c['calls']} | {c['prompt']} | {c['completion']} | {c['reasoning']} | {c['cached']} | {c['total']} |")
    L += ["", f"All models: {summ['all_total']} total tokens. Alibaba usage is not booked in the Anthropic ledger.", "",
          "## Substitutions (code refused / quota exhausted / markup not followed)", ""]
    subs = man["substitutions"]
    L += [f"- half {x['half']} {x['role']}: `{x['from']}` -> `{x['to']}` in phase {x['phase']} ({x['reason']}; error code {x['error_code']})" for x in subs] or ["- none"]
    L += ["", "## Halves not sealed (dropped, not hand-edited)", ""]
    for k in man["halves_not_sealed"]:
        hh = C.Half(k)
        txt = (hh.dir / "rounds" / "last_check.txt").read_text(encoding="utf-8") if (hh.dir / "rounds" / "last_check.txt").exists() else ""
        short = [ln.strip() for ln in txt.splitlines() if ln.strip().startswith("SHORT")]
        deny = [ln for ln in txt.splitlines() if ln.startswith("FAIL DENYLIST")]
        L.append(f"- half {k}: did not converge (validator clean with every quota met after one repair round and one top-up): "
                 f"{len(C.read_jsonl(hh.cases_path))} valid cases, {len(set(x.split(':')[0] for x in short))} distinct quota rows short, "
                 f"{len(deny)} cases with a denylist hit on the plan's workplace label; rows and counts in `halves/{k.lower()}/rounds/last_check.txt` and `halves/{k.lower()}/rounds/` (rule codes and counts only).")
    L += ["", "## Counts per half", ""]
    for k, m in sorted(H.items()):
        c = m["counts"]
        L += [f"### Half {k}", f"- cases: {c['cases']} (planned {m['cases_planned']}; dropped by rule {m['dropped_total']}); clean documents: {c['clean_documents']}; gold spans: {c['gold_spans']}; hard negatives: {c['hard_negatives']}",
              f"- organisation: profiled {c['by_organisation_kind']['profiled']}, other workplaces {c['by_organisation_kind']['other_workplace']} over {c['by_organisation_kind']['other_workplaces_distinct']} labels",
              f"- injected cases: {c['injected_cases']} (positions {c['by_injection_position']}, without personal information {c['injected_cases_without_pi']}); hard-to-spot: {c['hard_to_spot_cases']}; long documents: {c['long_documents']}",
              f"- sensitive spans: {c['sensitive_spans']}", f"- sha256 cases `{m['sha256']['cases']}`; plan `{m['sha256']['plan']}`; profile `{m['sha256']['profile']}`", ""]
    L += ["## Files read by the orchestrator", "",
          "`inputs/taxonomy.md` (= `themis-shield/eval/taxonomy.md`, identical), `format.md`, `profile-schema.md`, split F's `brief.md` (the model for the compact `brief.g.md`), split F's scripts (`*.py`), `validator/check_cases.py`, `validator/denylist_check.py` and `validator/VALIDATOR.md`, `themis-shield/eval/convert_profile.py` (imported by `prep_profiles.py`), `api-credit-2026-11-11/tools/ali_probe.py` (how the key is read), `tools/origin-denylist.txt` (loaded in code only, never printed or copied). Split F's `pass_rule.py` was copied by the `*.py` glob and opened before it was recognised as a gate-evaluation script; its content was not used for anything and it was removed from `split-g` together with the other two non-authoring scripts of that kind (names only: `report_measures.py`, `stage_codes.py`, which were not opened). No split-F case, profile or result was read; no gate code, prompt or detector; nothing under `another-project-*`; no web access except the API endpoint.", "",
          "## Notes", "",
          "- Every earlier split came from template generators or one Claude model; split G is per-document prose by non-Claude model families, one family per role, so document style differs by half.",
          "- Pointer check v2 (half R only): the first pass of half R's labeller with the original prompt flagged 68 of 98 documents, with reasons that treated invented personal names as real public figures. The prompt was re-run once with the same question plus the clarification that all private persons are invented (names that merely sound real are not a flag) and `when in doubt, false`; on a 15-document probe (10 of the v1 flags, 3 unflagged, 2 synthetic documents naming real companies and a famous person next to invented-person misconduct) it flagged the 2 synthetic positives only. The v1 results are kept in `results/r/pointer-v1/`. Halves S and T kept the original prompt (first-pass flag rates in `rounds/`).",
          "- Author prompt revision (one, as allowed): after the glm-5.3 probe produced a personal document with no gold markers, the user message got a reminder to wrap every personal item, and the Japanese `names` instruction asks for kana (the validator's Japanese detector looks for kana).",
          "- The workplace `organisation` field of non-profiled cases carries the plan's label (workplace number and sector); the invented workplace's own name appears only in the prose.",
          "- Rounds (counts) are in `halves/<x>/rounds/`; token numbers per phase are in `usage-summary.md`."]
    return "\n".join(L) + "\n"


def final() -> int:
    halves = {}
    for L in C.HALVES:
        m = half_manifest(C.Half(L))
        if m:
            halves[L] = m
    s = runner.write_usage_summary()
    man = {"split": "test-g", "halves": halves, "halves_sealed": sorted(halves),
           "halves_not_sealed": sorted(set(C.HALVES) - set(halves)),
           "sha256_common": {"taxonomy": C.sha256_file(ROOT / "inputs" / "taxonomy.md"),
                             "taxonomy_compact": C.sha256_file(ROOT / "inputs" / "taxonomy.g.compact.md"),
                             "brief": C.sha256_file(ROOT / "inputs" / "brief.g.md"),
                             "validator": C.sha256_file(ROOT / "validator" / "check_cases.py"),
                             "extra_quotas": C.sha256_file(ROOT / "extra_quotas.py")},
         "substitutions": C.substitutions(), "probe_findings": C.jload(ROOT / "state" / "probe_findings.json", {}),
         "tokens_by_model": {m: d["cumulative"] for m, d in s["models"].items()}, "tokens_all": s["all_total"]}
    (ROOT / "manifest.json").write_text(json.dumps(man, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    (ROOT / "PROVENANCE.md").write_text(provenance(man, s), encoding="utf-8", newline="\n")
    # validator once more on each sealed file with the manifest next to it (MANIFEST rule)
    for L, m in halves.items():
        with tempfile.TemporaryDirectory() as td:
            shutil.copyfile(ROOT / "sealed" / f"half-{L.lower()}.jsonl", Path(td) / f"half-{L.lower()}.jsonl")
            shutil.copyfile(ROOT / "manifest.json", Path(td) / "manifest.json")
            r = subprocess.run([C.PY, "-I", str(ROOT / "validator" / "check_cases.py"), str(Path(td) / f"half-{L.lower()}.jsonl"),
                                "--profile", str(ROOT / "sealed" / f"profile-{L.lower()}.json"), "--half", L, "--quotas"],
                               capture_output=True, text=True, encoding="utf-8")
            print(L, "validator on sealed file with manifest beside it:", "PASS" if r.returncode == 0 else "FAIL")
    print("manifest.json written; halves sealed:", sorted(halves))
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "final":
        sys.exit(final())
    h = C.Half(sys.argv[2])
    if cmd == "seal":
        sys.exit(seal_half(h))
    print(build_and_check(h)[2])