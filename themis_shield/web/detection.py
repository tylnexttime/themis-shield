"""Detection admission and deadlines without unsafe thread cancellation.

A timed-out worker is quarantined: its result is discarded and it retains
exclusion until it actually exits. This module never runs cloud or substitution
inside the deadline worker.
"""

from __future__ import annotations

from functools import wraps
import math
from threading import Event, Lock, Thread

from themis_shield.router import GateFailure


class DetectionControl:
    def __init__(self, maintenance, *, lock_timeout=120, detection_timeout=600):
        for value in (lock_timeout, detection_timeout):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError("invalid detection deadline")
        self.maintenance = maintenance
        self.detection = Lock()
        self.lock_timeout, self.detection_timeout = lock_timeout, detection_timeout

    def maintenance_active(self):
        return self.maintenance.locked()

    def begin_maintenance(self):
        if not self.detection.acquire(False):
            return False
        if not self.maintenance.acquire(False):
            self.detection.release()
            return False
        return True

    def end_maintenance(self):
        self.maintenance.release()
        self.detection.release()

    def wrap(self, function):
        @wraps(function)
        def guarded(*args, **kwargs):
            if self.maintenance_active():
                raise GateFailure("CALIBRATION_MAINTENANCE")
            if not self.detection.acquire(timeout=self.lock_timeout):
                raise GateFailure("GATE_BUSY")
            if self.maintenance_active():
                self.detection.release()
                raise GateFailure("CALIBRATION_MAINTENANCE")
            done, abandoned, custody = Event(), Event(), {}
            custody_lock = Lock()

            def worker():
                try:
                    result = function(*args, **kwargs)
                    with custody_lock:
                        if not abandoned.is_set():
                            custody["result"] = result
                except BaseException as failure:
                    with custody_lock:
                        if not abandoned.is_set():
                            custody["failure"] = failure
                finally:
                    self.detection.release()
                    done.set()

            try:
                Thread(target=worker, name="themis-detection", daemon=True).start()
            except BaseException:
                self.detection.release()
                raise
            if not done.wait(self.detection_timeout):
                with custody_lock:
                    abandoned.set()
                    custody.clear()
                raise GateFailure("DETECTION_TIMEOUT")
            with custody_lock:
                failure = custody.pop("failure", None)
                if failure is not None:
                    raise failure
                return custody.pop("result")
        return guarded
