"""One host's side of `tools/twohost_rust.sh`: work a run's units with N
parallel slots, claiming each first with an atomic mkdir on the Mac, exactly as
`sandbox/research/twohost.py` claims. Runs on both hosts.

    python tools/twohost_rust_worker.py <run> <bin> pc|mac <jobs>

The Mac walks the units from the end, the PC from the start. The PC side runs
the binary at IDLE priority (it is a gaming PC). A unit's stdout goes to
`.twohost/<run>/results/<unit>.txt` on the host that ran it.
"""
from __future__ import annotations

import os
import subprocess
import sys
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAC = os.environ.get("TWOHOST_MAC", "mac")


def unit_id(line: str) -> str:
    return f"{zlib.crc32(line.encode()):08x}"


def main() -> int:
    run, binary, host, jobs = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
    local = ROOT / ".twohost" / run
    results = local / "results"
    results.mkdir(parents=True, exist_ok=True)
    lines = [line.strip() for line in (local / "units.txt").read_text().splitlines() if line.strip()]
    if host == "mac":
        lines.reverse()
    exe = ROOT / "live_trade" / "target" / "release" / (binary + (".exe" if os.name == "nt" else ""))

    def remote(cmd: str) -> bool:
        return subprocess.run(["ssh", "-o", "ConnectTimeout=15", MAC, cmd],
                              capture_output=True).returncode == 0

    def claim(uid: str) -> bool:
        if host == "mac":
            (local / "claims").mkdir(parents=True, exist_ok=True)
            try:
                (local / "claims" / uid).mkdir()
                return True
            except FileExistsError:
                return False
        return remote(f"mkdir -p ~/quant/.twohost/{run}/claims && mkdir ~/quant/.twohost/{run}/claims/{uid}")

    def finish(uid: str) -> None:
        if host == "mac":
            (local / "claims" / uid / "done").mkdir(parents=True, exist_ok=True)
        else:
            remote(f"mkdir -p ~/quant/.twohost/{run}/claims/{uid}/done")

    def work(line: str) -> None:
        uid = unit_id(line)
        if not claim(uid):
            return
        print(f"[{host}] claimed: {line}", flush=True)
        flags = 0x00000040 if os.name == "nt" else 0  # IDLE_PRIORITY_CLASS
        with open(results / f"{uid}.txt", "w") as out, open(results / f"{uid}.err", "w") as err:
            subprocess.run([str(exe), *line.split()], cwd=ROOT / "live_trade",
                           stdout=out, stderr=err, creationflags=flags)
        finish(uid)
        print(f"[{host}] done: {line}", flush=True)

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        list(pool.map(work, lines))
    print(f"[{host}] no units left", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
