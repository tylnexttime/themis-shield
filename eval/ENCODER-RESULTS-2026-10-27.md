# A small decision encoder, fine-tuned: results (27/10/2026)

**Development experiment, not part of any release's pass rule.** It tests whether a small, fast classifier could
answer the typed-decision tier's multiple-choice questions ([ROADMAP.md](../docs/ROADMAP.md), section 2). How we use
such a model, and why never on its own: [docs/SMALL-ENCODER.md](../docs/SMALL-ENCODER.md).

## Setup

- **Model:** [Laya](https://huggingface.co/convaiinnovations/laya) (Apache-2.0), a ModernBERT-large decision model of
  about 421M parameters, fine-tuned with the publisher's `laya-train` (soft cross-entropy loss, shuffled options,
  3 epochs, effective batch 64, question budget 256 tokens, seed 0).
- **Training data:** a new synthetic generator of ours with its own templates and name pools, sharing no 8-gram with
  any published evaluation case. Five question families (is a sentence about a real person or fiction; a person's own
  contact or an organisation's; a date of birth, another date or a duration; a private person or a public figure;
  which sensitive category, if any). 40,000 rows, of which 71 whole sentence templates were held out, leaving 35,851
  rows for training.
- **Evaluation:** a calibration slice from the same generator with a different seed, split into rows whose templates
  the model trained on (3,572) and rows from the 71 held-out templates (428). The untuned model is the negative
  control. No published test split was used.
- **Hardware:** one 16 GB laptop GPU; peak memory 10.6 GB, training about one hour.

## Results

| Slice | Untuned (negative control) | Fine-tuned |
|---|---|---|
| Templates seen in training (3,572 rows) | 40.6% | 100% |
| **Templates never seen (428 rows)** | 34.6% | **92.3%** [95% CI 89.5-94.6] |

By family on the unseen templates (fine-tuned): sensitive category 93.0%, private person or public figure 92.5%,
real person or fiction 89.2%, contact owner 100% (one class only, 37 rows). The date family has no held-out templates,
so it has no unseen-template figure. The untuned model is at chance on the sensitive-category family (12.9% against
12.5%), so the negative control behaves as intended.

## The finding that matters: confidently wrong

- On the unseen templates the fine-tuned model is wrong **33 times out of 428**, and its **median confidence on those
  wrong answers is 1.000**. A confidence threshold would let every one of them through.
- Its confidence cannot be calibrated from this data: on the templates it trained on, every answer is right at
  confidence about 1.0, so a temperature fit has nothing to correct (it runs to the lower bound, and `laya-train`'s
  own fit stays at 1.0 with a warning). A diagnostic fit on the unseen templates alone wants a temperature of about
  5 to 6, which says how overconfident the raw scores are. It is not a number to use.
- The training loss reached zero partway through the first epoch: the model memorised the templates.

## Serving

The fine-tuned checkpoint converts with llama.cpp's own converter, unchanged, and serves through llama-server's
decision endpoint (`/v1/systemone`). On the CPU it matched the reference implementation on 46 of 46 rows checked
(untuned: worst probability difference 0.0017; fine-tuned: 0.00003, a weak check because its probabilities are
saturated). The server chooses a temperature by the number of options, so per-family temperatures would have to be
applied by the caller; at present there is no per-family temperature worth applying.

## What follows

- **Never on its own.** See [docs/SMALL-ENCODER.md](../docs/SMALL-ENCODER.md) for the roles it may and may not take.
- **Next fine-tune (v0.4):** calibrate on whole templates held out from both training and scoring; add held-out
  templates for the date family; stop training before the loss reaches zero (or use label smoothing); then test on a
  fresh split nobody has trained or tuned on.
