#!/usr/bin/env python3
"""Regenerate config/kws_keywords.txt from config/kws_keywords_raw.txt.

``kws_keywords_raw.txt`` is the human-editable source of truth: one
``词/短语 @COMMAND_KEY`` per line. ``kws_keywords.txt`` is the machine-read
sherpa-onnx KWS keyword file, where each Chinese keyword is expanded to its
phoneme tokens (initial + tone-diacritic final, surface form) and each English
keyword is mapped to ARPABET.

Usage::

    python scripts/generate_kws_keywords.py          # write kws_keywords.txt
    python scripts/generate_kws_keywords.py --check  # verify, write nothing
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
_RAW_PATH = _CONFIG_DIR / "kws_keywords_raw.txt"
_OUT_PATH = _CONFIG_DIR / "kws_keywords.txt"


# English keywords: plain phrase -> ARPABET phonemes (stress digits 0/1/2).
# There is no English G2P dependency in this project; extend this table by
# hand when adding an English keyword.
_ENGLISH_ARPABET = {
    "COME HERE": "K AH1 M HH IY1 R",
    "SHAKE HANDS": "SH EY1 K HH AE1 N D Z",
    "HIGH FIVE": "HH AY1 F AY1 V",
    "SIT": "S IH1 T",
    "LIE DOWN": "L AY1 D AW1 N",
    "STAND UP": "S T AE1 N D AH1 P",
    "WAIT": "W EY1 T",
    "FOLLOW ME": "F AA1 L OW0 M IY1",
    "ROLL OVER": "R OW1 L OW1 V ER0",
    "SPIN": "S P IH1 N",
    "COME BACK": "K AH1 M B AE1 K",
    "DROP IT": "D R AA1 P IH1 T",
    "PLAY DEAD": "P L EY1 D EH1 D",
}

# Polyphone words whose pypinyin default reading differs from the intended
# reading in a command context. Add any new ambiguous word here.
_POLYPHONE_OVERRIDES = {
    "转圈": "zh uàn q uān",  # 转 = zhuàn (rotate), not pypinyin default zhuǎn
}


def _is_chinese(text: str) -> bool:
    return any("㐀" <= char <= "鿿" for char in text)


def _pinyin_g2p(word: str) -> str:
    """Convert one Chinese word to sherpa-onnx KWS phoneme tokens."""
    from pypinyin import Style, pinyin

    tokens: list[str] = []
    for char in word:
        if not ("㐀" <= char <= "鿿"):
            tokens.append(char)
            continue
        full = pinyin(char, style=Style.TONE, heteronym=False)[0][0]
        initial = pinyin(char, style=Style.INITIALS, heteronym=False)[0][0]
        if initial:
            tokens.append(initial)
            final = full[len(initial):]
        elif full.startswith("w"):
            tokens.append("w")
            final = full[1:]
        elif full.startswith("y"):
            tokens.append("y")
            final = full[1:]
        else:
            final = full
        if final:
            tokens.append(final)
    return " ".join(tokens)


def g2p(word: str) -> str:
    """Return the phoneme token string for one raw keyword."""
    if _is_chinese(word):
        return _POLYPHONE_OVERRIDES.get(word) or _pinyin_g2p(word)
    arpabet = _ENGLISH_ARPABET.get(word.upper())
    if arpabet is None:
        raise ValueError(
            f"No ARPABET mapping for English keyword {word!r}; "
            "add it to _ENGLISH_ARPABET"
        )
    return arpabet


def generate() -> str:
    """Render kws_keywords.txt content from the raw file."""
    lines: list[str] = []
    for raw in _RAW_PATH.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.lstrip().startswith("#"):
            lines.append(raw)
            continue
        word, label = (part.strip() for part in raw.rsplit("@", 1))
        lines.append(f"{g2p(word)} @{label}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify generated content matches the current file, write nothing",
    )
    args = parser.parse_args(argv)

    rendered = generate()
    if args.check:
        current = _OUT_PATH.read_text(encoding="utf-8")
        if rendered == current:
            print(f"OK: {_OUT_PATH} is up to date")
            return 0
        print(f"DIFF: {_OUT_PATH} is stale; run without --check to regenerate")
        return 1

    _OUT_PATH.write_text(rendered, encoding="utf-8")
    print(f"Wrote {_OUT_PATH} ({rendered.count(chr(10))} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
