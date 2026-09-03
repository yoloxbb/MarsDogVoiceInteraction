"""Normalization helpers for recognized speech text."""

from __future__ import annotations

import re


_DIGIT_VALUES = {
    "零": 0,
    "〇": 0,
    "幺": 1,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_SMALL_UNITS = {"十": 10, "百": 100, "千": 1000}
_LARGE_UNITS = {"万": 10_000, "亿": 100_000_000}
_DIGIT_CHARS = "零〇幺一二两三四五六七八九0-9"
_INTEGER_CHARS = f"{_DIGIT_CHARS}十百千万亿"
_NUMBER_PATTERN = re.compile(
    rf"负?[{_INTEGER_CHARS}]+(?:点[{_DIGIT_CHARS}]+)?"
)
_PERCENT_PATTERN = re.compile(
    rf"百分之(?P<number>负?[{_INTEGER_CHARS}]+"
    rf"(?:点[{_DIGIT_CHARS}]+)?)"
)

# A single Chinese digit is ambiguous in normal speech (for example, the
# ``一`` in ``等一下``). Convert it only where surrounding text clearly marks a
# numeric value. Multi-character forms such as ``三百二十一`` are unambiguous.
_SINGLE_NUMBER_PREFIXES = ("第", "编号", "号码", "星期", "礼拜", "周")
_SINGLE_NUMBER_SUFFIXES = (
    "毫秒",
    "分钟",
    "点钟",
    "摄氏度",
    "公里",
    "千米",
    "厘米",
    "毫米",
    "公斤",
    "年",
    "月",
    "日",
    "号",
    "时",
    "秒",
    "岁",
    "元",
    "块",
    "角",
    "米",
    "斤",
    "克",
    "度",
)
_AMBIGUOUS_NUMBER_WORDS = frozenset({
    "万一",
    "一一",
    "一五一十",
    "三三两两",
    "七七八八",
    "千千万万",
})


def _digit_text(value: str) -> str:
    """Convert a sequence of Chinese/Arabic digit characters digit by digit."""
    return "".join(
        str(_DIGIT_VALUES[char]) if char in _DIGIT_VALUES else char
        for char in value
    )


def _parse_unit_integer(value: str) -> int:
    """Parse a Chinese integer containing 十/百/千/万/亿 units."""
    total = 0
    section = 0
    pending_digits = ""

    for char in value:
        if char in _DIGIT_VALUES:
            pending_digits += str(_DIGIT_VALUES[char])
            continue
        if char.isascii() and char.isdigit():
            pending_digits += char
            continue
        if char in _SMALL_UNITS:
            multiplier = int(pending_digits) if pending_digits else 1
            section += multiplier * _SMALL_UNITS[char]
            pending_digits = ""
            continue

        section += int(pending_digits) if pending_digits else 0
        pending_digits = ""
        if char == "万":
            total += section * _LARGE_UNITS[char]
        else:
            # Multiplying the accumulated high section also handles ``一万亿``.
            total = (total + section) * _LARGE_UNITS[char]
        section = 0

    return total + section + (int(pending_digits) if pending_digits else 0)


def _has_single_number_context(text: str, start: int, end: int) -> bool:
    if start == 0 and end == len(text):
        return True
    prefix = text[:start]
    suffix = text[end:]
    return prefix.endswith(_SINGLE_NUMBER_PREFIXES) or suffix.startswith(
        _SINGLE_NUMBER_SUFFIXES
    )


def _convert_number_token(
    token: str,
    *,
    force: bool,
    text: str = "",
    start: int = 0,
    end: int = 0,
) -> str:
    negative = token.startswith("负")
    unsigned = token[1:] if negative else token
    integer_part, separator, fractional_part = unsigned.partition("点")
    chinese_chars = [char for char in unsigned if char in _DIGIT_VALUES]
    has_unit = any(
        char in _SMALL_UNITS or char in _LARGE_UNITS
        for char in integer_part
    )
    has_numeric_context = _has_single_number_context(text, start, end)

    if (
        not force
        and unsigned in _AMBIGUOUS_NUMBER_WORDS
        and not has_numeric_context
    ):
        return token

    # Leave an isolated, context-free numeral untouched so command phrases
    # such as ``等一下`` and ``一起玩`` retain their lexical meaning.
    if (
        not force
        and len(integer_part) == 1
        and not separator
        and not negative
        and not has_numeric_context
    ):
        return token

    if has_unit:
        integer_text = str(_parse_unit_integer(integer_part))
    else:
        integer_text = _digit_text(integer_part)

    if separator:
        fractional_text = _digit_text(fractional_part)
        normalized = f"{integer_text}.{fractional_text}"
    else:
        normalized = integer_text

    # A token made only of existing Arabic digits requires no mutation.
    if not chinese_chars and not has_unit and not separator and not negative:
        return token
    return f"-{normalized}" if negative else normalized


def normalize_chinese_numbers(text: str) -> str:
    """Convert numeric Chinese expressions in recognized text to digits.

    Examples include ``三百二十一`` -> ``321``, ``二零二六`` -> ``2026``,
    ``负三点五`` -> ``-3.5`` and ``百分之三十`` -> ``30%``.
    """
    if not isinstance(text, str) or not text:
        return ""

    normalized = _PERCENT_PATTERN.sub(
        lambda match: (
            _convert_number_token(match.group("number"), force=True) + "%"
        ),
        text,
    )

    def replace(match: re.Match[str]) -> str:
        return _convert_number_token(
            match.group(0),
            force=False,
            text=normalized,
            start=match.start(),
            end=match.end(),
        )

    return _NUMBER_PATTERN.sub(replace, normalized)
