"""Step 4 entry point: strip the markup into halves/<x>/cases.jsonl (offsets by code, case_level derived, notes empty,
generator = half letter, split test-g, organisation set) and run the validator with --profile --quotas --half <X>,
the denylist scan and the extra quotas. The parsing itself lives in markup.py (parser), validate.py (case building and
checks) and pipeline.py (ingest of each round); this wrapper rebuilds cases.jsonl from the pipeline state and checks it.

    python parse.py R
"""
import sys

import common as C
import seal

if __name__ == "__main__":
    cases, ok, out = seal.build_and_check(C.Half(sys.argv[1]))
    print(out)
    sys.exit(0 if ok else 1)
