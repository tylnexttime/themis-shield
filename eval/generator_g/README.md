# Split G pipeline (published after the result)

The authoring pipeline of split G as it ran (12/11/2026), with the inputs, the shared validator, both sealed halves'
plans, profiles, converted institution profiles, finder contexts, round counts, agreement, manifest and provenance.
Results: [`../RESULTS-v0.6.md`](../RESULTS-v0.6.md); plan and amendment: [`../FROZEN-PLAN-v0.6.md`](../FROZEN-PLAN-v0.6.md).
The cases are [`../cases/test-g-r.jsonl`](../cases/test-g-r.jsonl) and [`../cases/test-g-t.jsonl`](../cases/test-g-t.jsonl),
byte-identical to the sealed files.

**What differs from the authoring workspace** (each change made by script in the published copy only):

- Half T's plan files (`halves/t/plan.jsonl`, `halves/t/plan.topup.jsonl`) and `plan_data.py`: the sector word of
  invented workplace 4 is replaced by `local-services` (it matched the project's origin denylist). The sealed cases
  never carried it in any text; their `organisation` field was renamed to `Other workplace 4` before sealing
  (`rename_org_t.py`, `halves/t/work/org_label_override.json`, applied in `finalize.valid_cases`). Because of this,
  `halves/t/plan.sha256` and the plan hash in `manifest.json` describe the original plan, not this copy.
- `seal.py` and `PROVENANCE.md`: one folder name in the "files read" paragraph is replaced by `another-project`.
- `ali.py`: the account's endpoint hostname is replaced by `ws-WORKSPACE-ID`.
- `validator/check_cases.py`: one known-answer test's vendor-prefix fixture is written in two parts (as split F's was,
  so that secret scanners do not flag the fixture); the validator hash in `manifest.json` is of the original. Both
  copies pass the known-answer tests and reject 26 of 26 mutations.
- Not published: half S (dropped unsealed; it is not a test half), the raw requests and responses, logs and probes.

Split G was authored through Alibaba Cloud Model Studio: no training per the provider's published terms; retention not
stated. All content is invented. To re-run, the key is read from a local vault (`ali.py`); nothing here holds a key.
