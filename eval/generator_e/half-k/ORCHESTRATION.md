# ORCHESTRATION: split E, half K (blind orchestrator log)

Orchestrator: Claude (Opus 5.5, `claude-opus-5-5`) as Alder's subagent, 30/10/2026. Role: relay, run and check
only. The orchestrator authored no case content, no profile and no generator code. It wrote the validator adaptation
(protocol constants only), the relay/run/label/agreement scripts in `split-e\orchestrator\`, and the orchestrator
messages (`split-e\orch-state\half-k\msg_*.md`: process and facts only).

## Models and routing

- Author: `deepseek/deepseek-v4.1-flash-1017` (DeepSeek), tool-calling loop from the first message;
  `max_tokens=100000`, `reasoning.max_tokens=24000`, `provider: {zdr: true, data_collection: deny,
  require_parameters: true}`. Model id as returned by the API on all 281 author calls:
  `deepseek/deepseek-v4.1-flash-1017`. Provider seen: Morph (281/281).
- Labeller: `z-ai/glm-5.3-flash` (Z.ai), blind (taxonomy.md + case text only), one case per call,
  `temperature=0`, `max_tokens=32000`. 366 calls (210 before review + 156 changed texts after). Providers seen:
  Wafer 350, Makora 9, OpenInference 4, Relace 1, InferenceNet 1, DigitalOcean 1. Model id returned:
  `z-ai/glm-5.3-flash` on all 366.
- Every call carries `zdr_requested: true` (`calls.jsonl`; the role of each call id in
  `orch-state\half-k\call_roles.jsonl`). Pre-check (no case content): a two-turn function-calling test routed under
  forced ZDR (provider Wafer). Transport faults: 0. Length cuts (`finish_reason=length`): 0. Empty replies: 0.

## Run log (UTC, 30/10/2026)

| phase | model calls | tool calls | build runs (pass/fail) | validator runs (pass/fail) | orchestrator check | cost USD |
|---|---|---|---|---|---|---|
| build (04:15-04:44) | 178 | 177 | 4 / 18 | 2 / 2 | accepted at the first text reply | 0.1935 |
| label (before) | 210 (labeller) | - | - | - | - | (labeller total below) |
| review (04:50-05:18) | 100 | 99 | 4 / 0 | 3 / 0 | accepted at the first text reply | 0.2572 |
| label (after) | 156 (labeller) | - | - | - | - | |
| provenance (05:21-05:22) | 3 | 2 | - | - | PROVENANCE.md present; generate.py, profile.json, cases.jsonl sha256 unchanged | 0.0056 |

- Build phase: first passing build at tool call 146 (04:39:50). Validator sequence in the build phase:
  2 rule-failure codes (CHECKSUM 4, PROFILE_HN 591) -> 0 failures with 1 quota short -> 0 failures, 0 quotas short
  (twice). Static guard refusals: 1 (the author revised and the next build ran). Runtime sandbox violations: 0.
  Timeouts: 0.
- Tool calls by name (all phases): read_file 151, replace_in_file 68, append_to_file 21, write_file 5,
  run_build 26, run_validator 7; tool errors 0. Total 278 (build 177 of the 250 limit; review 99; provenance 2).
- Final state (orchestrator's own validator run, `orch-state\half-k\validator_final.*`): 210 cases, 0 rule
  failures, 0 warnings, every quota row ok (0 short, 0 n/a).

## Final counts (from the sealed file)

210 cases; 60 clean; 2,731 gold spans (all 27 labels >= 20; UNION 35, CRIMINAL 31, ETHNICITY 33); 1,501 hard
negatives; 22 injected cases (before 8 / inside 7 / after 7; 8 with no personal information); 6 long documents
(8,000-26,000 characters); indirect share of sensitive spans 84% (224/267, as marked by the author); negative
statements in 8/8 categories; 21 tables with QUASI_ID; 15 hard-to-spot cases (as marked); 14/14 genre families;
12 scripts/languages; profiled-organisation cases 99 (47%); 8 other workplaces.

## Agreement (details in AGREEMENT.md)

Before review: recall 0.809, precision 0.992 (label-sensitive), 643 disagreement items. Author decisions: 551
fixed, 92 kept (643/643 covered). After review: recall 0.967, precision 0.834, 617 disagreement items. The
after-review precision drop is concentrated: 519 of the 525 labeller-only items after review are OTHER_DATE
occurrences that the author annotates as hard negatives, in 5 cases (counts only; the labeller left these
unlabelled before review, when they were gold).

## Seal

`split-e\sealed\half-k.jsonl` (210 lines), sha256 `25f93e2d272bf8fc8dd5a5228579d775a88da64d3a2e008e87f0e31c083e4c7d`
(`sealed\half-k.sha256`). Byte-identical to `half-k\cases.jsonl` at sealing.

## Cost and time

Author USD 0.4563 (281 calls; 21.7M prompt tokens of which 16.8M cached, 0.43M completion); labeller USD 0.1804
(366 calls). Half K total USD 0.6367. Wall time 04:15:25-05:21:56 UTC (about 66 minutes, including labelling).

## Disclosures (process)

- Relay changes against split D's toolloop: an `append_to_file` tool (write long files in parts); Python 3.12 for
  the generator runs (the brief's version; split D used the venv's 3.13); orchestrator state kept outside the half
  folder (`split-e\orch-state\half-k\`); `review_items.md` read-only to the author; a blind status log.
- The review round had its own budget of 120 tool calls on top of the build's 250 (stated in the review message);
  99 were used.
- The provenance request carried the facts from the API log (model id, date) up front instead of correcting them
  afterwards. The author had already written a PROVENANCE.md during the build phase; it updated it on request.
- Review items were written mechanically by `make_review.py`; the orchestrator did not read them.
