"""Record the OpenRouter key's total usage (US$, no secret in it): python keyusage.py start|end|now"""
import json
import sys
import time

import common as C
import llm

p = C.ROOT / "state" / "key_usage.json"
d = C.jload(p, {"before_this_job_at_2026-11-12T21:45Z": C.KEY_USAGE_AT_START_BEFORE_US})
u = llm.or_key_usage()
if len(sys.argv) > 1 and sys.argv[1] in ("start", "end"):
    d[sys.argv[1]] = {"usage_usd": u, "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    C.jsave(p, d)
print("key usage US$", u, "| limit", C.KEY_USAGE_LIMIT, "| this job's own sum US$", llm.or_cost_total())
