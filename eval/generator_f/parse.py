"""Step 4 entry point: strip the markup into cases.jsonl (offsets by code, case_level derived, notes empty,
generator O, split test-f, organisation set) and run the validator with --profile --quotas --half O, the denylist
scan and the extra quotas. The parsing itself lives in markup.py (parser), validate.py (case building and checks)
and author.py (ingest of each round); this wrapper rebuilds cases.jsonl from the pipeline state and checks it."""
import sys

import seal

if __name__ == "__main__":
    cases, ok, out = seal.build_and_check()
    print(out)
    sys.exit(0 if ok else 1)