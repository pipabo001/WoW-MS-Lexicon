#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build Microsoft Pinyin custom-phrase lexicon for World of Warcraft.

Merges curated slang (source/wow_words.py) with encyclopedic / community
translation bulk (source/bulk_wow.txt). Curated entries come first so custom
pinyin triggers (e.g. naide→奶德) keep candidate slots.
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "source"))
sys.path.insert(0, str(ROOT / "tools"))

from pypinyin import Style, lazy_pinyin
from wow_words import WOW

BULK_TXT = ROOT / "source" / "bulk_wow.txt"

# Drop obvious non-game IME-vendor brand phrases if any remain in bulk.
# Built via codepoints so public tree has no vendor-name literals.
BRAND_BLOCKLIST = tuple(
    "".join(map(chr, codes))
    for codes in (
        (0x641C, 0x72D7),
        (0x7EC6, 0x80DE, 0x8BCD, 0x5E93),
        (115, 111, 117, 103, 111, 117),
        (115, 111, 103, 111, 117),
    )
)


def to_pinyin(word: str) -> str:
    parts = lazy_pinyin(word, style=Style.NORMAL)
    s = "".join(parts).lower()
    return re.sub(r"[^a-z]", "", s)


def is_brand_junk(phrase: str, pinyin: str = "") -> bool:
    blob = f"{phrase}\t{pinyin}".lower()
    return any(b.lower() in blob for b in BRAND_BLOCKLIST)


def valid_entry(pinyin: str, phrase: str) -> tuple[bool, str]:
    if not phrase:
        return False, "empty phrase"
    if len(phrase) > 64:
        return False, "phrase >64"
    if not pinyin:
        return False, "empty pinyin"
    if len(pinyin) > 32:
        return False, "pinyin >32"
    if pinyin[0] in ("u", "v"):
        return False, "pinyin starts with u/v"
    if not re.fullmatch(r"[a-z]+", pinyin):
        return False, "pinyin not a-z"
    if is_brand_junk(phrase, pinyin):
        return False, "brand junk"
    return True, ""


def collect(raw, tag):
    seen = set()
    out = []
    skipped = []
    for py_override, phrase in raw:
        phrase = phrase.strip()
        py = (py_override or to_pinyin(phrase)).lower().strip()
        py = re.sub(r"[^a-z]", "", py)
        ok, reason = valid_entry(py, phrase)
        if not ok:
            skipped.append((phrase, py, reason))
            continue
        key = (py, phrase)
        if key in seen:
            continue
        seen.add(key)
        out.append((py, phrase, tag))
    if skipped:
        print(f"[{tag}] skipped {len(skipped)}:")
        for row in skipped[:15]:
            print("  ", row)
    return out


def load_bulk(path: Path, tag: str = "bulk"):
    raw = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        py, phrase = parts[0], parts[1]
        raw.append((py, phrase))
    return collect(raw, tag)


def assign_positions(entries):
    by_py = defaultdict(list)
    order = []
    for py, phrase, tag in entries:
        if py not in by_py:
            order.append(py)
        if any(p == phrase for p, _ in by_py[py]):
            continue
        by_py[py].append((phrase, tag))
    result = []
    overflow = 0
    for py in order:
        items = by_py[py]
        for i, (phrase, tag) in enumerate(items[:9]):
            result.append((py, i + 1, phrase, tag))
        if len(items) > 9:
            overflow += len(items) - 9
    if overflow:
        print(f"warning: {overflow} dropped (>9/pinyin)")
    return result


def write_pinyinlex_txt(path, rows):
    path.write_text(
        "\n".join(f"{py}\t{pos}\t{phrase}" for py, pos, phrase, _ in rows) + "\n",
        encoding="utf-8",
    )


def build_dat(rows, out_dat):
    from mschxudp_file import MschxudpFileBuilder

    b = MschxudpFileBuilder(win10="1703")
    for py, pos, phrase, _ in rows:
        b.add_phrase((pos, 6), py, phrase)
    b.save(str(out_dat))


def main():
    curated = collect(WOW, "wow")
    bulk = load_bulk(BULK_TXT, "bulk") if BULK_TXT.exists() else []

    seen_keys = {(py, ph) for py, ph, _ in curated}
    curated_phrases = {ph for _, ph, _ in curated}
    added = []
    for py, ph, tag in bulk:
        if (py, ph) in seen_keys:
            continue
        seen_keys.add((py, ph))
        added.append((py, ph, tag))

    merged = curated + added
    rows = assign_positions(merged)

    write_pinyinlex_txt(ROOT / "wow-microsoft-pinyin.txt", rows)
    write_pinyinlex_txt(ROOT / "wow.txt", rows)
    build_dat(rows, ROOT / "wow-microsoft-pinyin.dat")
    build_dat(rows, ROOT / "wow.dat")
    text = (ROOT / "wow-microsoft-pinyin.txt").read_text(encoding="utf-8")
    (ROOT / "wow-microsoft-pinyin-utf8bom.txt").write_bytes(
        b"\xef\xbb\xbf" + text.encode("utf-8")
    )

    tags = defaultdict(int)
    for *_, tag in rows:
        tags[tag] += 1

    bulk_new_phrases = {ph for _, ph, _ in added if ph not in curated_phrases}
    bulk_new_entries = len(added)

    stats = {
        "wow_entries": len(rows),
        "wow_phrases": len({r[2] for r in rows}),
        "unique_pinyin": len({r[0] for r in rows}),
        "curated_entries": tags.get("wow", 0),
        "bulk_raw_valid": len(bulk),
        "bulk_entries_added": bulk_new_entries,
        "bulk_new_phrases": len(bulk_new_phrases),
        "bulk_overlap_entries_skipped": len(bulk) - bulk_new_entries,
    }
    (ROOT / "STATS.txt").write_text(
        "\n".join(f"{k}={v}" for k, v in stats.items()) + "\n", encoding="utf-8"
    )
    print("STATS:", dict(stats))
    for name in [
        "wow.txt",
        "wow.dat",
        "wow-microsoft-pinyin.txt",
        "wow-microsoft-pinyin.dat",
        "wow-microsoft-pinyin-utf8bom.txt",
        "STATS.txt",
    ]:
        p = ROOT / name
        print(f"  {p.name} ({p.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
