# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Dmitriy Dobrovolskiy dima@dobrovolskiy.com

"""Windows DPAPI wrapper (CryptProtectData / CryptUnprotectData) via ctypes.

Blob format is base64(CryptProtectData(utf8 text)) - identical to what install.ps1 writes,
so a key stored by the installer is readable by the plugin and vice versa.
On non-Windows platforms the value is stored base64-encoded (obfuscated, not encrypted).
"""
import base64
import ctypes
import sys
from ctypes import wintypes

_IS_WIN = sys.platform.startswith("win")


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob_to_bytes(blob):
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob.pbData)


def protect(text):
    raw = text.encode("utf-8")
    if not _IS_WIN:
        return base64.b64encode(raw).decode("ascii")
    buf = ctypes.create_string_buffer(raw, len(raw))
    blob_in = _DATA_BLOB(len(raw), buf)
    blob_out = _DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("CryptProtectData failed")
    return base64.b64encode(_blob_to_bytes(blob_out)).decode("ascii")


def unprotect(b64):
    raw = base64.b64decode(b64)
    if not _IS_WIN:
        return raw.decode("utf-8")
    buf = ctypes.create_string_buffer(raw, len(raw))
    blob_in = _DATA_BLOB(len(raw), buf)
    blob_out = _DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("CryptUnprotectData failed (written by another Windows user?)")
    return _blob_to_bytes(blob_out).decode("utf-8")
