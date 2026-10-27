# PROVENANCE — Half Q

## Author

Model: Qwen, model id `qwen/qwen3.8-2.4t-a95b` (reached through OpenRouter), acting as generator author for half Q.

## Date

27 October 2026 (the entire run — initial generation rounds 1–12 via text delivery, the tool-based engineering phase, and the review round — took place on this date).

## Files read

- `inputs/taxonomy.md` — label definitions, span rules, hard-negative kinds, case-level rules
- `inputs/format.md` — case JSON format, build-check requirements
- `inputs/profile-schema.md` — deployment profile schema
- `inputs/brief.md` — assignment: quotas, safety rules, deliverables
- `review_items.md` — 378 labeller disagreements (R1–R378) for the review round

No other files were read. No web access was used. No real organisation or person is referenced; every entity is invented.

## What was built

**profile.json** — Tarnwater Corporation, a fictional regional water utility in southeastern Australia. All phones in ACMA fiction ranges; all emails on reserved domains; ABN checksum-valid (mod 89). Institutional terms only.

**generate.py** — deterministic generator (seed 42, Python 3.12 stdlib only, no subprocess/socket/eval/exec/tempfile). Writes `cases.jsonl` (JSONL, LF line endings, UTF-8 bytes) and `manifest.json`. `--self-check` rebuilds in memory and confirms byte-identical output. Enforces all format.md rules: span/text agreement, checksums (TFN, Medicare, ABN, IBAN, Luhn), phone fiction-range scan, email reserved-domain scan, repeat-coverage, case-level agreement, injection validity, no duplicate ids/texts, long-doc bounds, no line-breaking characters.

**cases.jsonl** — 199 cases, ids prefixed `DQ-`, `"generator": "Q"`, `"split": "test-d"`.

**manifest.json** — counts by label/genre/organisation/injection position, seed, sha256 of cases.jsonl.

## Quota summary (validator-confirmed)

| Quota | Result |
|---|---|
| Cases | 199 (target 190–210) |
| Clean documents | 63 (≥ 60) |
| Every label ≥ 20 spans; UNION/CRIMINAL/ETHNICITY ≥ 30 | met |
| Indirect share of sensitive spans | 70% (≥ 60%) |
| Negative statements per category | 8/8 |
| Tables with QUASI_ID | 20 (≥ 20) |
| Hard-to-spot cases | 21 (≥ 15) |
| Injected cases | 24, positions 8/8/8, ≥ 5 clean |
| Long documents (8k–26k chars) | 4 (≥ 4) |
| Genre families | 14/14 |
| Clean-doc look-alike kinds | 9/9 |
| Scripts/languages | 7 (≥ 5) |
| Profiled-org share | 44% (40–60%) |
| Other workplaces | 6 (≥ 6) |

## Build phases

### Phase 1 — text delivery (rounds 1–12)
Delivered generate.py and profile.json as text; orchestrator executed them. Reached 196 cases with most quotas met.

### Phase 2 — tool-based engineering (this session, rounds 13+)
Used read_file, replace_in_file, write_file, run_build, run_validator iteratively:

1. **MANIFEST fix** — switched Path.write_text to write_bytes to prevent CRLF conversion; sha256 now matches.
2. **Indirect share** — raised indirect_bias to 0.82; corrected indirect flags on template pools (DI, EI, SI, FI entries).
3. **Negative statements** — added negative templates for family_violence, sexuality, disability; added forced-negative builder ensuring all 8 categories have at least one.
4. **Organisation field** — set `organisation` on all non-profiled cases so validator counts other workplaces.
5. **Profiled ratio** — adjusted prof/non-prof split to ~44%.
6. **Tables with QUASI_ID** — added g_quasi_table generator (16 cases).
7. **Scripts** — added g_multiscript generator cycling ja/ko/ar/th/he.
8. **Clean-doc look-alike kinds** — added public_news, policy_statement, public_date HNs to clean cases (g_clean, g_news, g_clean_news).
9. **Hard-to-spot** — g_hard with 10 format types; native_script cases cycle scripts deterministically.
10. **Case count tuning** — balanced base generators and supplement batch size to land in 190–210.

### Phase 3 — review round
A labeller from another model family re-labelled all 201 cases blind (378 disagreements, R1–R378). Decisions recorded in `review_decisions.md`. Key fixes to the generator:

- **render_sensitive()** helper: name rendered as separate PERSON span; predicate as sensitive span; dates inside predicates split out as OTHER_DATE per taxonomy.
- **Table generators**: staff IDs labelled INTERNAL_ID; start/completion dates labelled OTHER_DATE; access-log timestamps labelled OTHER_DATE.
- **Email From: addresses** labelled EMAIL.
- **AVO template** moved from CRIMINAL to FAMILY_VIOLENCE (taxonomy: "protection orders" under FAMILY_VIOLENCE).
- **"does not have a disability"** moved from HEALTH pool to DISABILITY pool.
- **Chat date stamps** and **multiscript start dates** labelled OTHER_DATE.
- **Kept as-is** (taxonomy-cited): USERNAME inside EMAIL/URL spans (spans never overlap, whole-address rule); "and her wife are expecting their first child" as single SEXUALITY span.

After fixes: 199 cases, validator exit 0, all quotas met, zero failures, zero warnings.

## Safety of values

- All phones in ACMA fiction ranges (0491 570 xxx–0491 579 xxx, (0x) 5550/7010 xxxx, 1300/1800 975 707–711)
- All emails on example.com/.net/.org or *.example
- TFN/Medicare/ABN/IBAN checksum-valid by generator's own code; payment cards from published test numbers only
- SSNs only 987-65-4320..4329; NINOs only QQ prefix
- All persons invented; famous people only as public_figure hard negatives in clearly public context
