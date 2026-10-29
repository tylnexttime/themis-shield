# themis-shield roadmap

What we plan to build next, why, and how we will know whether it worked. It turns the gaps in
[What it does not do yet](../README.md#what-it-does-not-do-yet) into a plan. It was written on 27/10/2026 from four
pieces of research (typed-decision models, pipeline prior art, a sweep of the
[awesome-pii-tools](https://github.com/henrikaronsson/awesome-pii-tools) list, and evaluation data), all listed in
[SOURCES.md](SOURCES.md#roadmap-research-27102026). Dates are day-first.

## Where we are (30/10/2026)

- **v0.3.0 shipped on 27/10/2026 and failed its frozen test** (split D: 28 released spans, sensitive-statement recall
  0.740, friction 0.248 against a 0.15 rule; [results](../eval/RESULTS-v0.3.md)). Its friction changes worked; detection
  on new authors' text is what failed. Split D is now development data.
- **0.3.1 (30/10/2026):** [vetting records](VETTING-RECORD.md) (`themis vet`), which give the people who sign off a data
  release one value-free record of what was done, and the evaluation-tooling fixes in the [changelog](../CHANGELOG.md).
- **Next:** the v0.4 detection fixes below, in order, then v0.4's "keep the work possible" design.
- **Debt** is kept short, in [DEV-DEBT.md](DEV-DEBT.md); two debt items (DD-05, DD-06) are scheduled with the
  detection fixes because they change the finder capture.

## The rule we keep

**Misses teach the gate, never the exam. The exam is written by a stranger, sat once, and the pass mark only goes
up.**

- **Three pools, one-way flow.** Operator-confirmed misses and false positives go to *training* and to a
  *regression set*, never to a held-out test split. A test split is opened once; after that it becomes development
  data, as v0.1's set did in v0.2.
- **Two gates per release.** Every regression case must pass (a floor that only rises), *and* the release must pass
  a pass rule written before a fresh test split is opened, against a negative control.
- **Ratchet.** Thresholds in a frozen plan never loosen between releases. A candidate is no worse than the previous
  release on every safety measure (released leaks, recall, sensitive recall) and better on the one it targets, or it
  is not adopted.
- **Three families, three roles.** The test author, the gate's own models and the labeller come from different model
  families. Every result says which.

## v0.2.1: security hardening (27/10/2026)

Invisible Unicode characters (zero-width joiners, soft hyphens, variation selectors, tag characters) and full-width
letters could hide a security marking, the injection signal or a rule-pack value from the detectors. A document marked
`SECRET` with one zero-width joiner inside the word was not seen as marked. Found by reading our own code during the
v0.3 research, confirmed by test, fixed in [0.2.1](../CHANGELOG.md). The LLM finders and PII-Tracer still read the raw
text (their prompts are hashed for replay); moving them onto the same view is a v0.3 item.

## v0.3: less friction, same safety, and a gate that learns

**Status: shipped 27/10/2026 (0.3.0); frozen test FAIL.** Built as planned: items 1 to 5, the restore-safety tests
from item 6, and split D. The typed tier ships off by default with three question families (two were rejected on
development data). Still open from item 6: the finders reading the detection view (DD-06) and per-finding
explanations in the audit tables. The plan below is kept as written.

v0.2 released no leaks on a fresh split but held 19 of 95 clean documents for a human (0.200 against our 0.15 rule).
v0.3 aims to bring friction under the rule **on a split written by an author outside the Claude and Gemma families**,
without losing recall, and to add the parts that let a deployment improve from its own misses.

### The pipeline v0.3 is aiming for

```
 text ─► markings (raw + detection view) ─► T1: rules + PII-Tracer ─► T1.5: typed decisions ─► T2: Gemma finders
     ─► decision ─► substitute ─► FINAL SWEEP (known-miss register) ─► Operator approval
     ─► [optional] cloud trip-wire ─► the task ─► restore locally
                          │ flag                               ▲
                          └─► job stops ─► Operator ─► known-miss register ─► regression case ─► next release
```

### 1. Deterministic friction fixes (all of v0.2's known friction causes that a rule can fix)

- References to a secret manager and template placeholders (`${VAULT:…}`, `<set …>`, `{{ secret }}`) are not secrets.
  The value they point to is never in the text.
- Role mailboxes (`info@`, `support@`, `noreply@`, …) and Australian inbound numbers (13, 1300, 1800) of organisations
  no profile describes are still substituted, but they no longer count as evidence that holds a document.
- Context on checksum-valid numbers: next to "invoice", "order" or "ref", a nine-digit number that happens to pass
  the TFN check is relabelled as a generic identifier (still substituted). Today about 1 in 11 random nine-digit
  numbers would be called a TFN.
- Rule-pack completeness: a Luhn rule for payment cards (the label exists, the rule doesn't), an ACN validator as
  company evidence, and a cross-test of our Australian validators against Presidio's on synthetic vectors.
- Role words and titles are never quasi-identifier candidates in the table pass; a title is reported beside a name,
  never inside it.

### 2. A typed-decision tier (T1.5): fast yes / no / unsure

A small model answers short multiple-choice questions about what the cheaper tiers found, instead of searching the
text again. Idea from Chris, and from TypeSafe's Jev interface and its open alternatives; shape from
[jev-pii-checker](https://github.com/coo-quack/jev-pii-checker) (candidate, then judge).

| Question | Asked about | Allowed effect |
|---|---|---|
| Is this sentence about a real person, fiction or an example, general policy, or not sensitive? | each sentence a finder tagged as a sensitive statement | may lift that hold only |
| A person's own contact, or an organisation's mailbox or switchboard? | tracer-only emails and phones | may lift an empty-finder hold only |
| Date of birth, another date, a duration, or a number? | tracer-only dates and numbers | as above |
| A private individual, or a public figure in a public role? | tracer-only names in news-like text | as above |
| Union membership, criminal history, ethnicity, health, religion, sexuality, family violence, or none? | sentences no detector touched | may **add** a hold, never remove one |

- **One question, one forward pass.** The answer is the probability of each option's letter, read in a single pass
  with no generation. We checked this works with llama-server and Gemma 4: with thinking off and the answer started as
  `Answer:`, the option letters carried all the probability, at 66-183 ms per question for Gemma 4 E4B on our laptop.
- **Uncertainty goes to a human.** A narrow margin, disagreement between two option orders, or label mass under 0.9
  is "unsure", and an unsure answer never lifts a hold.
- **Safety property.** In v0.2 every missed span sat in a document that was held anyway. A lifted hold on a document
  with a finder miss becomes a released leak, so the tier may lift only holds it is the *sole* cause of, it can never
  lift an injection or marking hold, and released leaks stay the binding condition.
- **Two candidates, tested against each other:** the Gemma 4 26B finder we already run, asked prefill-only (no new
  model), and a small fine-tuned encoder such as [Laya](https://huggingface.co/convaiinnovations/laya) (Apache-2.0,
  421M), served by llama.cpp's decision endpoint. Laya is near chance without fine-tuning (0.362), so it is trained on
  a new training generator of ours, never on any test split.
- **First fine-tune (27/10/2026, development only, not part of v0.3):** trained on 35,851 synthetic examples, it
  answered 92.3% of questions correctly on sentence templates it had never seen (the untuned model: 34.6%, at chance
  on the sensitive-statement family), and it converts to GGUF and serves through llama-server's decision endpoint
  unchanged. But it is **confidently wrong**: its 33 mistakes out of 428 came with a median confidence of 1.000, and
  its temperature could not be fitted because the calibration data shared templates with training. So the encoder
  waits for v0.4, with calibration on whole templates held out from both training and scoring, early stopping before
  the training loss reaches zero, and a fresh test split. And no version of the typed tier trusts a confidence score
  on its own: the structural rules above (sole-cause lifts only, two option orders, never a marking or injection
  hold) are what keep it safe.
- **Where a small encoder fits: step 2, asymmetric** ([full explanation](SMALL-ENCODER.md), [measurements](../eval/ENCODER-RESULTS-2026-10-27.md)). It runs on the CPU between tier 1 and the GPU finders, and it
  may raise suspicion on its own but never lower it on its own:
  - *recall booster:* it may add an ASK for a sentence nothing else flagged (a confidently wrong "yes" costs a click;
    a wrong "no" is no worse than without it);
  - *second key:* it may help lift a hold only when the Gemma prefill check agrees, and only for a hold it is the sole
    cause of (two families rarely make the same confident mistake);
  - *hints for the person* reviewing an ASK ("looks like fiction", "looks like a public figure"), never an action;
  - *not the fast path:* deciding that a document may skip the finders waits for a calibrated encoder proven on a
    fresh split, because a confident wrong "clean" there skips the checks that would have caught the miss.
- The model's answer and the policy's decision are recorded separately, with the reason. Every disagreement is a free
  label for the catalogues below.

### 3. The known-miss register and a final sweep (Chris's design)

Anything the gate missed, once an Operator confirms it, is added to a register that every later job is checked
against after substitution, as the last step before a document is called clean. "The same miss cannot pass twice",
not "nothing is ever missed again".

- **It never stores the values it protects.** Entries are rules (a pattern or format), context shapes ("the only …
  on the night shift") and, for high-entropy strings only, keyed fingerprints (HMAC-SHA-256 under a key held by the
  vault protector). A date of birth or a phone number becomes a rule, never a fingerprint: a keyed hash of a
  low-entropy value can be reversed by anyone holding the key, and the key has to sit on the gate.
- A hit routes the job to ASK with the finding shown. A stale register copy means ASK; an unreadable one means BLOCK.
  The clean label records the register version.
- Each confirmed entry gets a synthetic regression case written fresh, and a release must pass all of them.
- Closest prior art: Microsoft Purview's Exact Data Match (salted hashes of known values, looked up only for
  candidates a pattern surfaced). We found nothing published that combines a false-negative register, keyed
  fingerprints and a regression release gate.

### 4. A reviewed false-positive catalogue (the mirror image)

Look-alikes the gate keeps flagging (a product code, a switchboard, a committee name) can be proposed from clean runs
and accepted by a person. Entries are scoped (organisation, label, context), expire, and come in two strengths: the
default only stops the finding from holding the document (it is still substituted); full allow-listing is the
stronger, rarer choice. No in-document ignore markers, ever: text under test must never be able to switch the gate
off. Ideas from uktrade's pii-secret-check-hooks, detect-secrets baselines and gitleaks' ignore fingerprints.

### 5. An optional cloud trip-wire (Chris's design)

After the Operator approves the de-identified text, and before the real task, the cloud model is asked to list any
personal information it can still see, by placeholder or span, and to stop if it finds any. It sees exactly what it
was going to see anyway, so there is no new exposure. **It detects, it does not prevent**: a hit means that
document's miss has already left the machine. The job stops, the Operator decides, and a confirmed hit enters the
known-miss register. It is prompted to flag residual identifiers, never to guess who someone is, and its flag
precision is tracked so we know whether it earns its round trip. Never on BLOCK or local-only work.

### 6. Smaller items

- Restore safety (done in v0.3): tests that a placeholder one character away from another never restores the wrong
  value (fuzzy restore is how a related project could restore the wrong person's name).
- The LLM finders and PII-Tracer read the detection view too (needs a new finder capture).
- Per-finding explanations in the audit tables: original and final score, checksum result, supporting context,
  cancellation reason.

### How v0.3 will be tested

- **Split D, written blind by two authors from different families**: half by Qwen 3.8 (an open-weight family the gate
  does not use) and half by a fresh Claude model, each from the same one-page brief, the taxonomy, the case format and
  a new fictional organisation of its own. Neither saw the gate, the earlier splits or this roadmap's friction list.
  The halves are reported separately, which measures how much the author's model family matters instead of assuming
  it.
- A **DeepSeek** model re-labels both halves blind against the taxonomy; disagreements go back to each author once;
  agreement is published with the plan.
- Split D is sealed by sha256 in the frozen plan before any v0.3 code meets it, and published with the results.
- **Arms:** v0.2.1 (control); + T1.5 on the resident Gemma 26B; + T1.5 on a fine-tuned small encoder. **Negative
  control:** the encoder untuned, which must fail.
- **Reported separately, never pooled:** human-annotated public text from the
  [Text Anonymization Benchmark](https://github.com/NorskRegnesentral/text-anonymization-benchmark) (MIT, court
  judgments with direct and quasi-identifiers), a small human-written slice when we have one, and
  jev-pii-checker's English cases as an out-of-distribution smoke test.

## v0.4, first: detection on new authors' text

From split D's leaks and misses. Each is measured on development data (split D included) with the held-by-reason
split, and the release is tested once on a fresh split.

1. **Column-aware identifier propagation.** When a table column's header names a personal identifier (licence,
   member, account, ID number), every cell in it is protected. 21 of split D's 28 released spans were bare licence
   numbers in such a column.
2. **Finders list each distinct value once** (DD-05), so long tables stop overflowing the answer. This lands *after*
   item 1, never before it: 274 of split D's unprotected-but-held spans were held only because the finder failed on
   such a table.
3. **Finders read the detection view** (DD-06), in the same new finder capture.
4. **Indirect sensitive statements:** a sentence-level check that also asks about sentences containing only a name
   (the misses sat in sentences a detector had already touched).

## v0.4 (design): keep the work possible

A gate that hides everything scores perfectly on leaks and is useless. Ask a cloud model for an expense summary or
for better deals on a pile of de-identified statements and contracts, and today it loses four things: time
(transaction dates become placeholders, so there is no monthly breakdown), renewal and contract dates, who "you" are
among the placeholders, and the pricing factors (age, area, vehicle) that comparisons depend on.

### Contextual toggles and a semi-synthetic dictionary (Chris's design)

The story behind this part, with its sources: [A codebook, and data that is partly real](CODEBOOK-AND-SYNTHETIC-DATA.md).

- **Per-task profiles** decide, class by class, what passes, what is generalised and what is hidden: an expense
  summary keeps months and amounts; deal advice keeps an age band, a region and contract months; everything else stays
  opaque.
- **Typed placeholders by default.** Every placeholder says what kind of value it hides, and a short legend travels
  with the request: `⟦I:3fa9c1⟧` is a bank account number, `⟦D:91b2e0⟧` a date of birth, `⟦P:0c44a7⟧` a person. The
  model learns the type, never the value, and is asked to answer in the IDs so restoration stays exact. (Today the
  placeholder carries only a coarse class letter.)
- **A semi-synthetic dictionary, opt-in.** For tasks that read better with real-looking text, values are replaced by
  realistic surrogates, and some classes (transaction dates, for example) pass through unchanged. The cloud model is
  not told which values are real. Each surrogate carries its ID (`Mara Quinlan ⟦P:3fa9c1⟧`), and the model is asked
  to keep the ID wherever it refers to that value. This is partially synthetic data, a long-studied technique of
  statistical disclosure control, and the legend is the codebook that comes with any shared confidentialised dataset.
- **Randomness, not secrecy.** themis-shield is open source, so a rule such as "dates always pass through" would tell
  everyone that every date the cloud sees is real. Which values are real can only stay unknown through per-job
  randomness: dates are shifted by a random per-job offset (whole weeks keep weekdays; intervals, durations and
  month-to-month patterns survive), as clinical de-identification does, and the decoder shifts them back. Amounts can
  take a consistent per-job scale (ratios and trends survive), opt-in, because some tasks need exact sums.
- **Restoration uses the IDs only.** A surrogate that comes back without its ID is never fuzzy-matched; it is flagged
  for the person to check. This answers the reason an earlier design rejected surrogates (models shorten, misspell and
  merge fake names, so exact reversal fails and fuzzy reversal risks the wrong person): the ID, not the name, is what
  gets restored. Surrogates are drawn so they collide with nothing in the document.
- **Generalised attributes:** a date of birth becomes an age band, an address a state and postcode region, a vehicle
  keeps make, model and year while plate and VIN are hidden, and the requester can mark themselves as "self".
- **The cost is stated, not hidden:** every kept or generalised attribute adds re-identification risk, because together
  they become quasi-identifiers. All toggles are off by default, and a risky combination holds the document for ASK.

### A utility measurement next to the leak measurement

The same task runs twice through a cloud model, once on the original documents and once on the gate's output, and a
judge scores how much worse the de-identified answer is. Reported per task and per profile, next to released leaks:
privacy and usefulness on one page.

## Later

- **A tier-1 bake-off** of small span finders: GLiNER PII checkpoints (Apache-2.0 ones only; the original GLiNER
  weights are non-commercial), [DataFog](https://github.com/DataFog/datafog-python), OpenAI Privacy Filter.
- **A fast path:** a per-chunk category check decides which tiers run, evaluated before it is offered.
- **Measured re-identification risk** for tables: [ARX](https://arx.deidentifier.org/) run offline to calibrate the
  table pass's quasi-identifier rule.
- **File inputs** (PDF, Word, images) through [OCRmyPDF](https://github.com/ocrmypdf/OCRmyPDF) as an external tool,
  with extraction that cannot fail silently: a PDF whose text layer is glyph codes rather than words, or whose page
  count and extracted text disagree, goes to OCR or to a person, never to the detectors as if it were complete. OCR
  engines drop whole lines without saying so, so OCR text is checked against line spacing (a gap-fill pass) before it
  is trusted. Images that carry personal data without text (a signature, a face, a photo of a card's back) are held,
  because a text gate cannot see them.
- **A real-data evaluation protocol** before `synthetic_only` can ever be switched off.
- **A human-written test slice**: 20-40 documents written by people, in their own words.
- Linux and macOS runs, a native Anthropic Messages leg, a central register service.

## What we looked at and decided against

- **Presidio as a dependency or lifted code.** Its Australian checksums agree with ours (we are stricter on the
  Medicare issue number); it would duplicate our rules and add vendored code. Its in-text encryption operator puts
  ciphertext, and so value length, in front of the cloud.
- **Fake-value substitution and fuzzy restore** (LLM Guard, now archived): opaque random tokens restored exactly are
  safer.
- **Hosted decision APIs** (Jev, and hosted "OpenJev" endpoints): the gate stays local.
- **Datasets we can't publish against:** ai4privacy's 300k/400k sets (no redistribution), i2b2/n2c2 (data use
  agreement), Piiranha (non-commercial), and models without a licence file (HydroXai pii-masker).
- **Synthea and SDV** as test authors: structured, US-centric, or learned from real tables we don't have.

## Watching

- [awesome-pii-tools](https://github.com/henrikaronsson/awesome-pii-tools): one commit so far
  (`9cb0602`, 19/07/2026); we check it for new entries.
- llama.cpp's decision models (`/v1/systemone`), Laya fine-tuning and GGUF conversion of custom checkpoints.
- Presidio releases, REDACT (gated research set with disclosed/undisclosed sensitive statements), Meddies-PII
  (multi-family, unreleased).
