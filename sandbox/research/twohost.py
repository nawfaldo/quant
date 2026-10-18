"""PC + Mac on one job list, no unit run twice.

A run is a list of UNITS (any string id, the same list on both hosts). Before
working a unit a host CLAIMS it with an atomic `mkdir` in the claim directory on
the Mac (`~/quant/.twohost/<run>/claims/<unit>`): the Mac does it locally, the
PC over ssh. Whoever gets the directory runs the unit; the other skips it, so a
fast host simply takes more units. A finished unit writes its result pickle on
its own host (`.twohost/<run>/results/<unit>.pkl`) and touches `<unit>/done`
in the claim directory.

The LEAD host (the PC) then `collect()`s: it pulls the Mac's result pickles over
ssh until every unit has one, and merges them. Re-running the same run id skips
units already done (a crashed claim without `done` must be cleared by hand:
`rm -r ~/quant/.twohost/<run>/claims/<unit>` on the Mac).

    from sandbox.research import twohost
    if twohost.enabled():
        if twohost.claim(unit):
            ... work ...
            twohost.finish(unit, result)
    rows = twohost.collect(all_units)      # lead only

Launch both hosts with `sandbox/research/twohost_run.sh` (see its header).
"""
from __future__ import annotations

import hashlib
import io
import os
import pickle
import subprocess
import sys
import tarfile
import time

RUN = os.environ.get("TWOHOST_RUN", "")
MAC = os.environ.get("TWOHOST_MAC", "mac")              # ssh alias of the Mac
MAC_ROOT = os.environ.get("TWOHOST_MAC_ROOT", "quant")  # repo dir on the Mac, from ~
IS_MAC = sys.platform == "darwin"
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def enabled():
    return bool(RUN)


def unit_id(*parts):
    """A short filesystem-safe id for a unit made of any values."""
    return hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:16]


def _local(*parts):
    return os.path.join(ROOT, ".twohost", RUN, *parts)


def _remote(*parts):
    return "/".join([MAC_ROOT, ".twohost", RUN, *parts])


def _ssh(cmd):
    return subprocess.run(["ssh", "-o", "ConnectTimeout=15", MAC, cmd],
                          capture_output=True, text=True)


def claim(unit):
    """True if this host now owns `unit` (and it is not done yet)."""
    if IS_MAC:
        claims = _local("claims")
        os.makedirs(claims, exist_ok=True)
        try:
            os.mkdir(os.path.join(claims, unit))
            return True
        except FileExistsError:
            return False
    for attempt in range(5):
        r = _ssh(f"mkdir -p {_remote('claims')} && mkdir {_remote('claims', unit)}")
        if r.returncode == 0:
            return True
        if "File exists" in r.stderr:
            return False
        time.sleep(2 * (attempt + 1))  # ssh hiccup: retry, never run unclaimed
    raise RuntimeError(f"twohost: cannot reach {MAC} to claim {unit}: {r.stderr.strip()}")


def finish(unit, result):
    """Store `unit`'s result on this host and mark it done on the claim board."""
    os.makedirs(_local("results"), exist_ok=True)
    tmp = _local("results", unit + ".tmp")
    with open(tmp, "wb") as handle:
        pickle.dump(result, handle, protocol=5)
    os.replace(tmp, _local("results", unit + ".pkl"))
    if IS_MAC:
        open(os.path.join(_local("claims", unit), "done"), "w").close()
    else:
        _ssh(f"touch {_remote('claims', unit, 'done')}")


def collect(units, poll=20):
    """Lead host: every unit's result, pulling the Mac's until all exist."""
    units = list(units)
    os.makedirs(_local("results"), exist_ok=True)
    while True:
        have = {u for u in units if os.path.exists(_local("results", u + ".pkl"))}
        missing = [u for u in units if u not in have]
        if not missing:
            break
        if not IS_MAC:
            r = subprocess.run(["ssh", "-o", "ConnectTimeout=15", MAC,
                                f"cd {_remote('results')} 2>/dev/null && tar cf - --exclude='*.tmp' ."],
                               capture_output=True)
            if r.returncode == 0 and r.stdout:
                with tarfile.open(fileobj=io.BytesIO(r.stdout)) as tar:
                    for member in tar.getmembers():
                        name = os.path.basename(member.name)
                        if member.isfile() and name.endswith(".pkl") \
                                and not os.path.exists(_local("results", name)):
                            data = tar.extractfile(member).read()
                            with open(_local("results", name), "wb") as handle:
                                handle.write(data)
            if len([u for u in units if os.path.exists(_local("results", u + ".pkl"))]) == len(units):
                continue
        print(f"twohost: waiting for {len(missing)} of {len(units)} units", flush=True)
        time.sleep(poll)
    out = {}
    for u in units:
        with open(_local("results", u + ".pkl"), "rb") as handle:
            out[u] = pickle.load(handle)
    return out
