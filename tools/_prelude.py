"""Disarm the WMI lookup that hangs pandas on this machine. Import this FIRST.

WMI is broken on this box: `Get-CimInstance Win32_OperatingSystem` times out
after fifteen seconds even though the Winmgmt service reports Running. Python
3.14's `platform.win32_ver()` asks WMI, and `pandas/compat/_constants.py` calls
`platform.machine()` at import time -- so **importing pandas blocks forever**,
with no error and no CPU use. pyarrow imports pandas lazily inside `pa.array()`
and the dataset API, which is why `pq.read_table` hangs too.

Setting `platform._wmi = None` makes `_wmi_query` raise `OSError("not
supported")` immediately, and `_win32_ver` already catches that and falls back
to the registry. Nothing else changes.

WHY A SEPARATE MODULE, AND WHY IT MUST BE THE FIRST IMPORT.

`sandbox/parquet_store.py` and `tools/parquet_writer.py` do the same thing at
the top of themselves, which is enough for anything whose first heavy import is
one of those. It is NOT enough for a script that imports pandas directly:
imports run in source order, `import pandas` sorts before `import
parquet_writer`, and the shim would then run several minutes too late -- which
is to say never, because the process is already wedged.

So a module that sorts first, does one thing, and says why:

    import _prelude  # noqa: F401  -- must precede pandas; see the module

Repairing WMI (`winmgmt /salvagerepository`, needs admin) would make all of
this unnecessary and has not been attempted.
"""

import platform

platform._wmi = None
