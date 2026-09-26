"""Rewrite the Rust book membership from `exness_combined_strategies.BOOK`.

Touches the three places that must equal it -- `BOOK` in the strategy module,
`LIVE_STRATEGIES` in routing, and the membership test -- and adds any sleeve
that left to the parity test's RETIRED list so its fixture keeps running.
Variant names are derived from the key, the convention every sleeve uses.

    py -3 -m sandbox.research._set_rust_book
"""
import platform
platform._wmi = None
import re
from pathlib import Path

from sandbox.research import exness_combined_strategies as cs

SRC = Path(__file__).resolve().parents[2] / "live_trade" / "src"
DIR = SRC / "strategies" / "idk" / "exness_combined_07-09-2026"


def variant(key):
    sym, fam = key.split(":")
    return sym.capitalize() + "".join(w.capitalize() for w in fam.split("_"))


def main():
    book = list(cs.BOOK)
    ids = [k.replace(":", "_") for k in book]

    mod = DIR / "mod.rs"
    s = mod.read_text(encoding="utf-8")
    old_members = re.findall(r"Sleeve::(\w+),",
                             re.search(r"pub const BOOK: \[Sleeve; \d+\] = \[(.*?)\];", s, re.S).group(1))
    s = re.sub(r"pub const BOOK: \[Sleeve; \d+\] = \[.*?\];",
               f"pub const BOOK: [Sleeve; {len(book)}] = [\n"
               + "".join(f"    Sleeve::{variant(k)},\n" for k in book) + "];", s, count=1, flags=re.S)
    mod.write_text(s, encoding="utf-8")

    rt = SRC / "live" / "portfolio" / "routing.rs"
    s = rt.read_text(encoding="utf-8")
    s = re.sub(r"pub const LIVE_STRATEGIES: &\[&str\] = &\[.*?\];",
               "pub const LIVE_STRATEGIES: &[&str] = &[\n"
               + "".join(f'    "{i}",\n' for i in ids) + "];", s, count=1, flags=re.S)
    rt.write_text(s, encoding="utf-8")

    t = DIR / "tests.rs"
    s = t.read_text(encoding="utf-8")
    s = re.sub(r"(fn the_book_is_the_\w+_sealed_members\(\) \{.*?vec!\[).*?(\n\s*\])",
               lambda m: m.group(1) + "\n" + "".join(f'            "{i}",\n' for i in ids).rstrip("\n") + m.group(2),
               s, count=1, flags=re.S)
    t.write_text(s, encoding="utf-8")

    left = [v for v in old_members if v not in {variant(k) for k in book}]
    par = DIR / "parity.rs"
    s = par.read_text(encoding="utf-8")
    m = re.search(r"const RETIRED: \[Sleeve; (\d+)\] = \[(.*?)\];", s, re.S)
    retired = re.findall(r"Sleeve::(\w+),", m.group(2))
    retired += [v for v in left if v not in retired]
    s = s[:m.start()] + f"const RETIRED: [Sleeve; {len(retired)}] = [\n" + "".join(
        f"        Sleeve::{v},\n" for v in retired) + "    ];" + s[m.end():]
    par.write_text(s, encoding="utf-8")
    print(f"Rust book -> {len(book)} sleeves; retired now {len(retired)}: newly retired {left}")


if __name__ == "__main__":
    main()
