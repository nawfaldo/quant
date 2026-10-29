"""Push the named `data/parquet` tables to the Mac where they differ.

    py tools/twohost_sync_tables.py audjpy_1m fundednext_audjpy_1m ...

A table is `<name>.parquet` and/or `<name>/*.parquet`. Every file whose size
differs from the Mac's copy (or that the Mac lacks) goes over in one tar;
shards the Mac holds that the PC no longer has are removed there, so a table
the PC compacted does not end up stored twice. Used by `tools/twohost_rust.sh`
(`TWOHOST_TABLES`).
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "parquet"
MAC = os.environ.get("TWOHOST_MAC", "mac")


def local_files(name: str) -> dict[str, int]:
    out = {}
    single = DATA / f"{name}.parquet"
    if single.is_file():
        out[f"{name}.parquet"] = single.stat().st_size
    shard_dir = DATA / name
    if shard_dir.is_dir():
        for shard in shard_dir.glob("*.parquet"):
            out[f"{name}/{shard.name}"] = shard.stat().st_size
    return out


def main() -> int:
    names = sys.argv[1:]
    mine: dict[str, int] = {}
    for name in names:
        mine.update(local_files(name))
    # Listed by Python on the Mac: its login shell is zsh, which errors on a
    # glob that matches nothing.
    lister = """
import os, sys
for n in sys.argv[1:]:
    p = n + '.parquet'
    if os.path.isfile(p):
        print(os.path.getsize(p), p)
    if os.path.isdir(n):
        for f in os.listdir(n):
            if f.endswith('.parquet'):
                print(os.path.getsize(n + '/' + f), n + '/' + f)
"""
    listed = subprocess.run(
        ["ssh", MAC, "cd ~/quant/data/parquet && /usr/bin/python3 - " + " ".join(names)],
        input=lister, capture_output=True, text=True, check=True).stdout
    theirs = {}
    for row in listed.splitlines():
        size, _, path = row.partition(" ")
        theirs[path] = int(size)
    send = [path for path, size in mine.items() if theirs.get(path) != size]
    stale = [path for path in theirs if path not in mine]
    if stale:
        subprocess.run(["ssh", MAC, "cd ~/quant/data/parquet && rm -f " + " ".join(stale)], check=True)
    if not send:
        print(f"tables: {len(names)} in sync on the Mac")
        return 0
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for path in send:
            tar.add(DATA / path, arcname=path)
    print(f"tables: pushing {len(send)} files ({buffer.tell() / 1e6:.0f} MB), removing {len(stale)}")
    subprocess.run(["ssh", MAC, "mkdir -p ~/quant/data/parquet && cd ~/quant/data/parquet && tar xf -"],
                   input=buffer.getvalue(), check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
