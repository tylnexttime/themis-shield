"""Poll a batch every 60 s (up to ~9 minutes per call); on end, collect results and book the ledger.
Exit code 0 = ended and collected, 2 = still processing (run again)."""
import sys

import common as C

if __name__ == "__main__":
    sys.exit(0 if C.wait(sys.argv[1], max_wait=int(sys.argv[2]) if len(sys.argv) > 2 else 540) else 2)