# A small decision encoder: a helper, never a judge

**Status: design for v0.4. Not part of v0.3.** This page says how themis-shield will use a small, fast
multiple-choice model such as [Laya](https://huggingface.co/convaiinnovations/laya), and why it must **never be used
as a standalone privacy check**. The measurements behind it: [eval/ENCODER-RESULTS-2026-10-27.md](../eval/ENCODER-RESULTS-2026-10-27.md).

## Why it is tempting

A decision encoder answers short typed questions ("is this sentence about a real person, fiction, or general
policy?") in milliseconds on a CPU, with a probability for each option. themis-shield's slow part is the local LLM
finders on an 8 GB GPU. A model that could settle easy questions on the CPU, while the GPU does the hard ones, looks
like free speed and fewer false alarms.

## Why it must never decide alone

We fine-tuned one on synthetic data of our own. On sentence templates it had never seen, it answered 92.3% of
questions correctly, against 34.6% before fine-tuning. That is useful. But on the 7.7% it got wrong, its **median
confidence was 1.000**. It does not know when it is wrong, and the usual remedy (calibrating its scores on held-out
data) failed, because the data we could calibrate on shared templates with its training.

A privacy gate cannot rest on a model that is confidently wrong one time in thirteen. Any rule of the form "lift
the hold when the encoder is at least 99% sure" would release every one of those mistakes.

## The rule: it may raise suspicion on its own, never lower it on its own

The encoder sits at step 2, on the CPU, between the cheap checks (rules and PII-Tracer) and the GPU finders, and it
only ever acts in one of these roles:

| Role | What it may do | Why that is safe |
|---|---|---|
| **Recall booster** | Add an ASK for a sentence nothing else flagged, when it reads it as a sensitive statement (health, criminal history, union membership...) | A confidently wrong "yes" costs one human click. A wrong "no" is no worse than not having it. |
| **Second key** | Help lift a hold (for example "this sentence is fiction"), only when the Gemma prefill check independently agrees, and only for a hold it is the sole cause of | Two models from different families rarely make the same confident mistake. Marking, secret and injection holds are never lifted. |
| **Hint for the person** | Annotate an ASK ("looks like fiction", "looks like a public figure") | It informs; the person decides. |

And one role it may not take:

| Role | Why not |
|---|---|
| **Fast path** (deciding a document may skip the GPU finders) | A confidently wrong "clean" skips the very checks that would have caught the miss. Not until a calibrated encoder has passed a fresh test split, and even then only under a pass rule written in advance. |

## Practical notes

- It runs on the CPU, so it never competes with the GPU finders for the 8 GB of video memory.
- It is served by llama.cpp's own decision endpoint, under the same supervision as the finders: no second engine.
- Every answer is recorded beside the policy's decision, so each disagreement becomes a label for the catalogues.
- Each request sends its own question text. The fine-tuned checkpoint's stored question schema keeps only one
  example per family, so it is not a reusable schema.
