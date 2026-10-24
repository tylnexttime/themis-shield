"""CurrentUser DPAPI protection of the local user wrapping key.

No optional entropy or machine-wide flag is used. The caller supplies bytes;
this module does not read credential files.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from typing import Protocol


class ProtectionError(RuntimeError):
    pass


class KeyProtector(Protocol):
    def protect(self, value: bytes) -> bytes: ...
    def unprotect(self, value: bytes) -> bytes: ...


class NullProtector:
    """Identity protector: NOT protection. Tests only, behind insecure_test_only."""

    test_only = True

    def protect(self, value: bytes) -> bytes:
        return bytes(value)

    def unprotect(self, value: bytes) -> bytes:
        return bytes(value)


class DPAPIProtector:
    """Protect bytes with Windows DPAPI, scoped to CurrentUser."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise ProtectionError(
                "Windows CurrentUser DPAPI is unavailable on this platform; "
                "configure the passphrase protector instead"
            )

    @staticmethod
    def _call(value: bytes, *, decrypt: bool) -> bytes:
        if os.name != "nt":
            raise ProtectionError("Windows CurrentUser DPAPI is unavailable")

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [
                ("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
            ]

        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        protect = crypt32.CryptProtectData
        unprotect = crypt32.CryptUnprotectData
        blob_pointer = ctypes.POINTER(DATA_BLOB)

        protect.argtypes = [
            blob_pointer, wintypes.LPCWSTR, blob_pointer, ctypes.c_void_p,
            ctypes.c_void_p, wintypes.DWORD, blob_pointer,
        ]
        unprotect.argtypes = [
            blob_pointer, ctypes.POINTER(wintypes.LPWSTR), blob_pointer,
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, blob_pointer,
        ]
        protect.restype = unprotect.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p

        storage = (ctypes.c_ubyte * max(1, len(value)))()
        if value:
            ctypes.memmove(storage, value, len(value))
        incoming = DATA_BLOB(
            len(value), ctypes.cast(storage, ctypes.POINTER(ctypes.c_ubyte))
        )
        outgoing = DATA_BLOB()
        description = wintypes.LPWSTR()
        # CRYPTPROTECT_UI_FORBIDDEN; deliberately NOT LOCAL_MACHINE.
        flags = 0x1
        try:
            if decrypt:
                succeeded = unprotect(
                    ctypes.byref(incoming), ctypes.byref(description),
                    None, None, None, flags, ctypes.byref(outgoing),
                )
            else:
                succeeded = protect(
                    ctypes.byref(incoming), "themis-shield user wrapping key",
                    None, None, None, flags, ctypes.byref(outgoing),
                )
            if not succeeded:
                raise ProtectionError("Windows DPAPI operation failed")
            return ctypes.string_at(outgoing.pbData, outgoing.cbData)
        finally:
            ctypes.memset(storage, 0, ctypes.sizeof(storage))
            if outgoing.pbData:
                ctypes.memset(outgoing.pbData, 0, outgoing.cbData)
                kernel32.LocalFree(
                    ctypes.cast(outgoing.pbData, ctypes.c_void_p)
                )
            if description:
                kernel32.LocalFree(
                    ctypes.cast(description, ctypes.c_void_p)
                )

    def protect(self, value: bytes) -> bytes:
        return self._call(bytes(value), decrypt=False)

    def unprotect(self, value: bytes) -> bytes:
        return self._call(bytes(value), decrypt=True)
