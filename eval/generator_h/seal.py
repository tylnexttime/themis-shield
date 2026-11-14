"""Check, seal and document split H. Split H needs BOTH halves: nothing is sealed unless both halves pass all checks.

    python seal.py check A|B     build halves/<x>/cases.jsonl and run the three checks (validator, denylist, extra quotas)
    python seal.py seal          check both halves; if both pass: seal both (sealed/half-a|b.jsonl, .sha256, profile-a|b.json,
                                 profiles/), then manifest.json and PROVENANCE.md; otherwise seal nothing
    python seal.py final         manifest.json and PROVENANCE.md only (state of the work so far)
"""
from __future__ import annotations

import hashlib
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
import prompts
import runner
from store import load_entries, load_profile, load_state

ROOT = C.ROOT
DATA_HANDLING = ("OpenRouter calls forced zero data retention (provider.zdr = true, data_collection = deny; serving provider "
                 "logged per call). Alibaba Cloud's published Model Studio FAQ says customer data is not used for training; "
                 "no zero-retention setting is documented: no training per the provider's terms, retention not stated. All "
                 "content is invented.")


def counts(cases: list[dict], profile: dict) -> dict:
    labels = Counter(s["label"] for c in cases for s in c["spans"])
    prof = [c for c in cases if c["organisation"] == profile["organisation"]]
    inj = [c for c in cases if c.get("injection")]
    sens = [s for c in cases for s in c["spans"] if "indirect" in s]
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
        "sensitive_spans": {"total": len(sens), "indirect": sum(1 for s in sens if s.get("indirect")),
                            "negative": sum(1 for s in sens if s.get("negative"))},
        "hard_negatives_meeting_letter_case": {
            k: sum(1 for c in cases for x in c["hard_negatives"] if x["kind"] == k) for k in ("meeting_date", "letter_date", "case_number")},
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


def seal_one(h: C.Half) -> str:
    cases = F.valid_cases(h)
    F.write_cases(h.cases_path, cases)
    sealed = ROOT / "sealed" / f"half-{h.l}.jsonl"
    prof_s = ROOT / "sealed" / f"profile-{h.l}.json"
    sha_path = ROOT / "sealed" / f"half-{h.l}.sha256"
    for p in (sealed, prof_s, sha_path):
        if p.exists():
            os.chmod(p, stat.S_IWRITE)
    shutil.copyfile(h.cases_path, sealed)
    digest = C.sha256_file(sealed)
    sha_path.write_text(f"{digest}  half-{h.l}.jsonl\n", encoding="utf-8", newline="\n")
    shutil.copyfile(h.profile_path, prof_s)
    for p in (sealed, prof_s, sha_path):
        _ro(p)
    (h.dir / "work" / "seal-result.json").write_text(json.dumps({"half": h.L, "verdict": "SEALED", "sha256": digest,
                                                                  "cases": len(cases)}), encoding="utf-8")
    return digest


def seal_both() -> int:
    ok_all, res = True, {}
    for L in sorted(C.HALVES):
        h = C.Half(L)
        cases, ok, out = build_and_check(h)
        res[L] = {"cases": len(cases), "checks_pass": ok}
        print(f"half {L}: {len(cases)} cases, all checks pass: {ok}")
        print(out)
        ok_all &= ok
    if not ok_all:
        print("NOT SEALED: split H needs both halves clean with every quota met; nothing was sealed", res)
        final()
        return 1
    for L in sorted(C.HALVES):
        print(L, "sealed", seal_one(C.Half(L)))
    r = subprocess.run([C.PY, str(ROOT / "prep_profiles.py")] + [l.lower() for l in sorted(C.HALVES)], capture_output=True,
                       text=True, encoding="utf-8")
    print(r.stdout.strip(), (r.stderr or "")[-300:])
    return final()


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
    seed = h.cfg["seed"]
    return {"half": h.L, "id_prefix": h.prefix, "generator": h.L, "split": "test-h", "organisation_kind": h.cfg["org_kind"],
            "counts": counts(cases, profile), "seed_plan": seed, "seed_safe_values_plan": seed + 1,
            "seed_safe_values_topup": seed + 2, "seed_topup_choices": seed + 3,
            "seed_safe_values_topup2": seed + 6, "seed_topup2_choices": seed + 5,
            "sealed_short_rows": res.get("short_rows", {}), "seal_decision": res.get("decision"),
            "profile_name_objects_unwrapped_for_the_converter": C.jload(ROOT / "profiles" / f"normalised-{h.l}.json", {}),
            "models_assigned": {r: h.cfg[r] for r in ("author", "labeller", "pointer")}, "models_final": models,
            "settings": {m_: C.settings(m_) for m_ in {h.cfg["author"], h.cfg["labeller"], h.cfg["pointer"], *models.values()}},
            "substitutions": sub,
            "sha256": {"cases": res["sha256"], "plan": C.sha256_file(h.plan),
                       "plan_topup": C.sha256_file(h.plan_topup) if h.plan_topup.exists() else None,
                       "plan_topup2": C.sha256_file(h.plan_topup2) if h.plan_topup2.exists() else None,
                       "profile": C.sha256_file(ROOT / "sealed" / f"profile-{h.l}.json"),
                       "institution_profile": C.sha256_file(ROOT / "profiles" / "institution" / f"test-h-{h.l}.json"),
                       "context": C.sha256_file(ROOT / "profiles" / f"context-{h.l}.txt"),
                       "agreement_md": C.sha256_file(h.agreement_path) if h.agreement_path.exists() else None},
            "cases_planned": len(C.read_jsonl(h.plan)) + len(C.read_jsonl(h.plan_topup)) + len(C.read_jsonl(h.plan_topup2)),
            "dropped_total": sum(1 for r in st.values() if r["status"] == "dropped"),
            "status_totals": dict(Counter(r["status"] for r in st.values())),
            "profile_fixes": (rounds.get("profile", {}).get("tries") or [{}])[-1].get("fixes"),
            "rounds": {k: v for k, v in rounds.items() if k.startswith(("ingest", "review", "agreement", "topup", "convergence", "final"))}}  # includes the second top-up's t2 rounds


def provenance(man: dict, summ: dict) -> str:
    H = man["halves"]
    ku = C.jload(ROOT / "state" / "key_usage.json", {})
    lp = man.get("label_prompt_decision", {})
    L = ["# PROVENANCE: split H (two halves of per-document prose, written and labelled by four non-Claude models)", "",
         "Counts, hashes, token numbers, file names and model codes only; no case, value, name or profile term is quoted in "
         "this file. Everything in the set is invented.", "", "**Data handling:** " + DATA_HANDLING, "",
         "## Who made what", "",
         "| half | profiled organisation (invented) | author (profile, documents, repairs, top-up, review decisions) | blind labeller | pointer check |",
         "|---|---|---|---|---|"]
    for k, m in sorted(H.items()):
        fin, asg = m["models_final"], m["models_assigned"]
        cell = lambda r: f"`{asg[r]}` (" + C.provider_of(asg[r]) + ")" + (f" (final role holder: `{fin[r]}`)" if fin[r] != asg[r] else "")  # noqa: E731
        L.append(f"| {k} (`H{k}-`, sealed `sealed/half-{k.lower()}.jsonl`) | {m['organisation_kind']} | {cell('author')} | {cell('labeller')} | {cell('pointer')} |")
    L += ["", "| artefact | made by | from |", "|---|---|---|",
          "| `halves/<x>/plan.jsonl` | `plan.py`, deterministic, one fixed seed per half (seeds in `manifest.json`); checksum / range-restricted values by `safevalues.py` (own code, cross-checked against the validator) with one shared used-set, so values are unique across both halves, plan and top-up | `inputs/brief.h.md` quotas (split G scaled to about 80 cases) |",
          "| `profile.json` | one chat-completions call to the half's author (`profile.py`); ABN, phones and domain checked and, if invalid, regenerated by code (fix counts in `manifest.json`) | `inputs/profile-schema.md` and the task text |",
          "| the documents | one chat-completions request per plan entry (`pipeline.py`); system = `inputs/brief.h.md` + `inputs/taxonomy.g.compact.md` (+ the minified profile for the profiled organisation's cases); user = the case specification | the plan entry, the safe values |",
          "| offsets, `case_level`, `organisation`, flags | `markup.py` / `validate.py` (code); never a model | the inline markup |",
          "| repair, top-up, review decisions | the half's author; review decisions are `keep` / `fix` per disagreement item, applied by script as span edits (never a model rewriting the document) and accepted only when the validator is clean and no quota row newly fails | rule codes and pointer flags / the labeller's disagreement items |",
          "| real-world pointer check | `openai/gpt-oss-120b` for both halves, plain text only, JSON `{flag, reason}`, split G's second prompt (`prompts.POINTER_SYSTEM`) | the plain text only |",
          f"| blind labelling | the half's labeller, system = {'the compact taxonomy (`inputs/taxonomy.g.compact.md`)' if lp.get('mode') == 'compact' else 'the full `inputs/taxonomy.md`'} for both labellers (decided once, before labelling, from token counts: see below), user = plain text | the plain text only |",
          "| validation | `validator/check_cases.py` (split-G copy, protocol constants only, see `validator/VALIDATOR.md`), `validator/denylist_check.py` (list passed, never printed), `extra_quotas.py`, `check_plan.py` (denylist over the plan) | `halves/<x>/cases.jsonl` |", "",
          "## Label prompt decision", "",
          f"`state/label_prompt.json`: mode {lp.get('mode')}; rule: {lp.get('rule')}; projected kimi-k3.1-pro total for half A with the full taxonomy {lp.get('projected_total_full'):,} tokens, with the compact one {lp.get('projected_total_compact'):,} (from probe token counts and the plan's characters per case).", "",
          "## Model settings", "",
          "Alibaba Model Studio: OpenAI-compatible chat completions, streamed with `stream_options.include_usage`. OpenRouter: through `tools/openrouter_client.py` (zero data retention forced by that client), non-streamed, `usage.include = true`. At most 4 requests in flight per model (2 per process for `openai/gpt-oss-120b`, which both halves' processes use). Guards in `llm.py`, read from `usage.jsonl` before every request: OpenRouter refuse at US$4.80 (cap US$5.00); Alibaba refuse at `deepseek-v4.1-flash` 980,000, `kimi-k3.1-pro` 390,000, `qwen3.8-flash` 800,000 total tokens (cached included); no other model code is allowed.", ""]
    L += ["| model | provider | thinking / reasoning | temperature | other request fields | max_tokens policy |", "|---|---|---|---|---|---|"]
    seen = {}
    for k, m in sorted(H.items()):
        for mm, st in m["settings"].items():
            seen[mm] = st
    for mm, st in sorted(seen.items()):
        th = {True: "on", False: "off", None: "model default"}[st.get("thinking")] if C.provider_of(mm) == "alibaba" else (
            "reasoning effort " + str((st.get("extra") or {}).get("reasoning", {}).get("effort", "n/a")))
        temp = "author: " + str(st.get("author_temperature", 0.7)) + ", labeller / pointer / review: 0"
        L.append(f"| `{mm}` | {C.provider_of(mm)} | {th} | {temp} | {json.dumps(st.get('extra')) if st.get('extra') else 'none'} | authors: characters of the length band / 1.3 + 2,000, at most 30,000; labeller: characters / 1.2 + 6,000 (reasoning headroom), at most 33,000; pointer: 1,500; review: 40 per item + 300 |")
    L += ["", "## Tokens and cost (as returned in `usage`; per phase in `usage-summary.md`)", "",
          "| model | provider | calls | prompt | completion | reasoning (inside completion) | cached (inside prompt) | total | US$ | serving providers |", "|---|---|---|---|---|---|---|---|---|---|"]
    for mm, d in sorted(summ["models"].items()):
        c = d["cumulative"]
        usd = f"{c.get('cost_usd', 0):.4f}" if d["provider"] == "openrouter" else "free quota"
        L.append(f"| `{mm}` | {d['provider']} | {c['calls']} | {c['prompt']} | {c['completion']} | {c['reasoning']} | {c['cached']} | {c['total']} | {usd} | {', '.join(d['serving_providers'])} |")
    L += ["", f"All models: {summ['all_total']} total tokens; OpenRouter spend US${summ['openrouter_cost_usd']:.4f} (budget US$5.00). OpenRouter key total usage: before this job US${C.KEY_USAGE_AT_START_BEFORE_US} (12/11, 21:45 UTC), at the start of this job US${ku.get('start', {}).get('usage_usd')}, at the end US${ku.get('end', {}).get('usage_usd')}. Alibaba usage is not booked in the Anthropic ledger.", "",
          "## Substitutions (spare author qwen3.8-flash)", ""]
    subs = man["substitutions"]
    L += [f"- half {x['half']} {x['role']}: `{x['from']}` -> `{x['to']}` in phase {x['phase']} ({x['reason']}; error code {x['error_code']})" for x in subs] or ["- none"]
    L += ["", "## Halves not sealed (dropped, not hand-edited)", ""]
    for k in man["halves_not_sealed"]:
        hh = C.Half(k)
        txt = (hh.dir / "rounds" / "last_check.txt").read_text(encoding="utf-8") if (hh.dir / "rounds" / "last_check.txt").exists() else ""
        short = [ln.strip() for ln in txt.splitlines() if ln.strip().startswith("SHORT")]
        L.append(f"- half {k}: not sealed; {len(C.read_jsonl(hh.cases_path))} valid cases at the last check, {len(short)} quota rows short; rows in `halves/{k.lower()}/rounds/last_check.txt` (rule codes and counts only).")
    if not man["halves_not_sealed"]:
        L.append("- none")
    L += ["", "## Counts per half", ""]
    for k, m in sorted(H.items()):
        c = m["counts"]
        L += [f"### Half {k}", f"- cases: {c['cases']} (planned {m['cases_planned']}; dropped by rule {m['dropped_total']}); clean documents: {c['clean_documents']}; gold spans: {c['gold_spans']}; hard negatives: {c['hard_negatives']}; characters: {c['characters_total']}",
              f"- organisation: profiled {c['by_organisation_kind']['profiled']}, other workplaces {c['by_organisation_kind']['other_workplace']} over {c['by_organisation_kind']['other_workplaces_distinct']} labels",
              f"- injected cases: {c['injected_cases']} (positions {c['by_injection_position']}, without personal information {c['injected_cases_without_pi']}); hard-to-spot: {c['hard_to_spot_cases']}; long documents: {c['long_documents']}",
              f"- sensitive spans: {c['sensitive_spans']}", f"- sha256 cases `{m['sha256']['cases']}`; plan `{m['sha256']['plan']}`; profile `{m['sha256']['profile']}`", ""]
    L += ["## Files read by the orchestrator", "",
          "Split G's code and inputs (`*.py`, `inputs/`, `validator/`) copied into `split-h`; `w40-splitg-REPORT.md`; `themis-shield/eval/taxonomy.md` (hash-compared with `inputs/taxonomy.md`) and `eval/convert_profile.py` (imported by `prep_profiles.py`); `FROZEN-PLAN-v0.7.md`; `tools/openrouter_client.py` (imported); `api-credit-2026-11-11/tools/ali_probe.py` (how the Alibaba key is read); `tools/origin-denylist.txt` (loaded in code only, never printed or copied). No other split's cases, no gate code, prompts, detectors or results, no worktree, nothing under `another-project-*`; no web access except the two APIs.", "",
          "## Notes", "",
          "- Per-document free prose by non-Claude model families, one family per role (no family both writes and labels a half), so document style differs by half.",
          "- Differences from split G's pipeline (see the report): one call layer for both providers with cost and token guards; planned sizes 86 (half A) and 82 (half B); first top-up sized at 1.6x the measured shortfall (dropped without repair) and never beyond 88 cases; one shared used-set for pre-generated values; denylist over the plan; author prompt without the profile for other-workplace cases; review decisions as keep/fix per item applied as span edits by script; spare-author rule limited to the author role.",
          "- **Amendment of 13/11/2026 (second top-up, `second_topup.py`, `run_second.py`)**: decided by the gate's designer on counts only before sealing (commit `Split H amendment before sealing: one targeted top-up`). One more top-up per half for the failing coverage rows only, sized ceil(2.5 x shortfall), by the half's own author, with the one repair round the planned entries get (pointer check, repair on validator codes and pointer flags, pointer recheck of what was repaired); blind labelling and one review round for the new documents only; agreement recomputed over the whole half. A half is sealed if it passes every row; one that is still short is sealed with the shortfall recorded (`sealed_short_rows` in `manifest.json`, and in the sealing commit) if it is short on at most two coverage rows by at most two units each; otherwise dropped. Phase names of this round in `usage.jsonl`: `topup2`, `t2-pointer`, `t2-repair`, `t2-pointer2`, `t2-label`, `t2-review`.",
          "- Rounds (counts) are in `halves/<x>/rounds/`; token numbers per phase are in `usage-summary.md`."]
    return "\n".join(L) + "\n"


def final() -> int:
    halves = {}
    for L in C.HALVES:
        m = half_manifest(C.Half(L))
        if m:
            halves[L] = m
    s = runner.write_usage_summary()
    man = {"split": "test-h", "halves": halves, "halves_sealed": sorted(halves),
           "halves_not_sealed": sorted(set(C.HALVES) - set(halves)),
           "sha256_common": {"taxonomy": C.sha256_file(ROOT / "inputs" / "taxonomy.md"),
                             "taxonomy_compact": C.sha256_file(ROOT / "inputs" / "taxonomy.g.compact.md"),
                             "brief": C.sha256_file(ROOT / "inputs" / "brief.h.md"),
                             "pointer_prompt": hashlib.sha256(prompts.POINTER_SYSTEM.encode("utf-8")).hexdigest(),
                             "validator": C.sha256_file(ROOT / "validator" / "check_cases.py"),
                             "extra_quotas": C.sha256_file(ROOT / "extra_quotas.py")},
           "label_prompt_decision": C.jload(ROOT / "state" / "label_prompt.json", {}),
           "key_usage": C.jload(ROOT / "state" / "key_usage.json", {}),
           "substitutions": C.substitutions(),
           "tokens_by_model": {m: d["cumulative"] for m, d in s["models"].items()}, "tokens_all": s["all_total"],
           "openrouter_cost_usd": s["openrouter_cost_usd"]}
    (ROOT / "manifest.json").write_text(json.dumps(man, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    (ROOT / "PROVENANCE.md").write_text(provenance(man, s), encoding="utf-8", newline="\n")
    rc = 0
    for L in halves:  # the validator once more on each sealed file with the manifest next to it (MANIFEST rule)
        with tempfile.TemporaryDirectory() as td:
            shutil.copyfile(ROOT / "sealed" / f"half-{L.lower()}.jsonl", Path(td) / f"half-{L.lower()}.jsonl")
            shutil.copyfile(ROOT / "manifest.json", Path(td) / "manifest.json")
            r = subprocess.run([C.PY, "-I", str(ROOT / "validator" / "check_cases.py"), str(Path(td) / f"half-{L.lower()}.jsonl"),
                                "--profile", str(ROOT / "sealed" / f"profile-{L.lower()}.json"), "--half", L, "--quotas"],
                               capture_output=True, text=True, encoding="utf-8")
            print(L, "validator on sealed file with manifest beside it:", "PASS" if r.returncode == 0 else "FAIL")
            rc |= r.returncode
    print("manifest.json written; halves sealed:", sorted(halves))
    return rc


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "final":
        sys.exit(final())
    if cmd == "seal":
        sys.exit(seal_both())
    print(build_and_check(C.Half(sys.argv[2]))[2])
