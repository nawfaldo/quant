"""Cross-process serialization for the MetaTrader5 Python IPC extension.

The official extension talks to one terminal through a process-global IPC
channel.  Quant's execution bridge and read-only candle collector may share
that terminal, but overlapping extension calls intermittently fail with
``(-10001, "IPC send failed")``.  A Windows named mutex keeps each individual
extension call atomic across both Python processes.
"""

from __future__ import annotations

import ctypes
import os
import threading
from collections.abc import Callable
from typing import Any


WAIT_OBJECT_0 = 0
WAIT_ABANDONED = 0x80
WAIT_TIMEOUT = 0x102
DEFAULT_TIMEOUT_MS = 30_000
MUTEX_NAME = os.getenv("QUANT_MT5_IPC_MUTEX", r"Local\QuantMetaTrader5IPC")


class _NamedMutex:
    def __init__(self, name: str) -> None:
        self._fallback = threading.RLock()
        self._handle: int | None = None
        if os.name != "nt":
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.ReleaseMutex.argtypes = (ctypes.c_void_p,)
        kernel32.ReleaseMutex.restype = ctypes.c_bool
        handle = kernel32.CreateMutexW(None, False, name)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateMutexW failed")
        self._kernel32 = kernel32
        self._handle = int(handle)

    def __enter__(self) -> None:
        if self._handle is None:
            self._fallback.acquire()
            return
        result = self._kernel32.WaitForSingleObject(self._handle, DEFAULT_TIMEOUT_MS)
        if result in (WAIT_OBJECT_0, WAIT_ABANDONED):
            return
        if result == WAIT_TIMEOUT:
            raise TimeoutError("timed out waiting for shared MetaTrader5 IPC")
        raise OSError(ctypes.get_last_error(), "WaitForSingleObject failed")

    def __exit__(self, *_: object) -> None:
        if self._handle is None:
            self._fallback.release()
            return
        if not self._kernel32.ReleaseMutex(self._handle):
            raise OSError(ctypes.get_last_error(), "ReleaseMutex failed")


_SHARED_MUTEX = _NamedMutex(MUTEX_NAME)


def mt5_ipc_lock() -> _NamedMutex:
    """Return the process-local handle for the shared terminal mutex."""
    return _SHARED_MUTEX


class SynchronizedMt5:
    """Transparent module proxy that serializes callable MT5 attributes."""

    def __init__(self, module: Any) -> None:
        self._module = module
        self._mutex = _SHARED_MUTEX
        self._wrapped: dict[str, Callable[..., Any]] = {}

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._module, name)
        if not callable(attribute):
            return attribute
        if name not in self._wrapped:
            def synchronized(
                *args: object,
                _call: Callable[..., Any] = attribute,
                **kwargs: object,
            ) -> Any:
                with self._mutex:
                    return _call(*args, **kwargs)

            self._wrapped[name] = synchronized
        return self._wrapped[name]


def synchronized_mt5(module: Any) -> SynchronizedMt5:
    return SynchronizedMt5(module)
