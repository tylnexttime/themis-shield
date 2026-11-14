# Split H pipeline (published after the result)

The authoring pipeline of split H as it ran (12-13/11/2026), with the inputs, the shared validator, both sealed halves'
plans (the planned entries, the first top-up and the second, targeted top-up of the plan's amendment), profiles as the
authors wrote them, converted institution profiles, finder contexts, round counts, agreement, manifest, provenance and
token usage. Results: [`../RESULTS-v0.7.md`](../RESULTS-v0.7.md); plan, amendment and named kit:
[`../FROZEN-PLAN-v0.7.md`](../FROZEN-PLAN-v0.7.md). The cases are [`../cases/test-h-a.jsonl`](../cases/test-h-a.jsonl)
and [`../cases/test-h-b.jsonl`](../cases/test-h-b.jsonl), byte-identical to the sealed files (sha256
`3f29c5735b4a5113396a961cde10d110ac6eebda21af4c9f960b7cdbca97fe0f` and
`5544671eb45323179b45588461b6b96e266bb668a62cd6b1c33c504991c20069`). No case needed a denylist replacement.

Half A: written by `mistralai/mistral-large-2512` (Mistral, through OpenRouter with zero-data-retention routing) about
an invented regional airline and airport operator, labelled blind by `kimi-k3.1-pro` (Moonshot, Alibaba Cloud Model
Studio). Half B: written by `deepseek-v4.1-flash` (DeepSeek, Alibaba Cloud Model Studio) about an invented independent
school group, labelled blind by `openai/gpt-oss-120b` (OpenAI, OpenRouter). Pointer check on both halves by
`openai/gpt-oss-120b`. A Claude model (Sonnet 5.5) built and ran the pipeline and reported counts only.

**What differs from the authoring workspace** (each change made by script in the published copy only):

- `seal.py` and `PROVENANCE.md`: one folder name in the "files read" sentence is replaced by `another-project` (it
  matched the project's origin denylist), as for split G.
- `llm.py`: the Alibaba account's endpoint hostname is replaced by `ws-WORKSPACE-ID`.
- `validator/check_cases.py`: one known-answer test's vendor-prefix fixture is written in two parts, so that secret
  scanners do not flag it; the validator hash in `manifest.json` is of the original. The published copy passes the
  known-answer tests and rejects 26 of 26 mutations.
- Not published: the raw requests and responses, the per-round working state (`halves/*/work/`), logs, probes, the
  per-call usage log (its totals are in `usage-summary.md`) and the backup of the first round.

Data handling: OpenRouter calls forced zero-data-retention routing; Alibaba Cloud Model Studio calls were made under its
published terms (no training; retention not stated). All content is invented. To re-run, keys are read from a local
vault (`llm.py`); nothing here holds a key.
