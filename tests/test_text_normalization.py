import pytest

from marsdog_voice_interaction.utils.text_normalization import (
    normalize_chinese_numbers,
)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("三百二十一", "321"),
        ("编号三百二十一", "编号321"),
        ("二零二六年九月三日", "2026年9月3日"),
        ("零一零幺二三四五六七八", "01012345678"),
        ("温度负三点五度", "温度-3.5度"),
        ("百分之三十二点五", "32.5%"),
        ("第十二个", "第12个"),
        ("房间一二零三", "房间1203"),
        ("星期一上午三点钟", "星期1上午3点钟"),
        ("共12万三千二百一十", "共123210"),
        ("一万亿", "1000000000000"),
        ("万一出问题就停止", "万一出问题就停止"),
        ("等一下我们一起玩", "等一下我们一起玩"),
        ("我有点累", "我有点累"),
        ("擦一擦手", "擦一擦手"),
        ("2026年9月3日", "2026年9月3日"),
    ],
)
def test_normalize_chinese_numbers(source: str, expected: str) -> None:
    assert normalize_chinese_numbers(source) == expected
