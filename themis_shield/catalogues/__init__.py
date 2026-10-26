"""Operator-reviewed catalogues that let a deployment learn from its own runs (v0.3, all off by default).

- :mod:`.known_miss`: the known-miss register and the final sweep over outbound, de-identified text.
- :mod:`.false_positives`: the reviewed false-positive catalogue (``no_hold`` and ``allow`` entries).
- :mod:`.tripwire`: the optional cloud trip-wire, run after Operator approval and before the task turn.
- :mod:`.gate`: how the router applies them; with every feature off it changes nothing.

None of these stores ever holds an original value. See docs/ARCHITECTURE.md.
"""
