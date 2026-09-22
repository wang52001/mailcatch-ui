"""强密码生成（FR-03 的密码部分）。

规则：默认 16 位，必须同时含小写/大写/数字/符号，且不含易混淆字符（0O1lI 等）。
生成后做一次自检，不满足字符集要求就重来——保证不会碰巧产出弱密码。
"""

from __future__ import annotations

import random
import secrets
import string

# 去掉易混淆字符：0 O o 1 l I 和容易打错的 ` ' " \
LOWER = "abcdefghjkmnpqrstuvwxyz"
UPPER = "ABCDEFGHJKMNPQRSTUVWXYZ"
DIGITS = "23456789"
SYMBOLS = "!@#$%^&*()-_=+[]{}:,.?"

AMBIGUOUS = set("0Oo1lI`'\"")


def generate_password(length: int = 16, symbols: bool = True,
                      rng: random.Random | None = None) -> str:
    """生成强密码。length < 8 会抛错，太短没有意义。"""
    if length < 8:
        raise ValueError(f"密码长度至少 8 位（给了 {length}）")

    choose = rng.choice if rng else secrets.choice
    pools = [LOWER, UPPER, DIGITS] + ([SYMBOLS] if symbols else [])
    alphabet = "".join(pools)

    for _ in range(100):
        # 每个字符池至少取一个，保证字符集齐全
        chars = [choose(p) for p in pools]
        chars += [choose(alphabet) for _ in range(length - len(chars))]
        # Fisher-Yates 打乱，避免前缀规律（如首字母永远是大写）
        for i in range(len(chars) - 1, 0, -1):
            j = (rng.randrange(i + 1) if rng else secrets.randbelow(i + 1))
            chars[i], chars[j] = chars[j], chars[i]
        pwd = "".join(chars)
        if is_strong(pwd, symbols):
            return pwd
    raise RuntimeError("连续 100 次都没生成合格密码，检查字符集配置")


def is_strong(pwd: str, require_symbols: bool = True) -> bool:
    """字符集自检：四类都要有，且没有易混淆字符。"""
    if not pwd or any(c in AMBIGUOUS for c in pwd):
        return False
    has = {
        "lower": any(c in string.ascii_lowercase for c in pwd),
        "upper": any(c in string.ascii_uppercase for c in pwd),
        "digit": any(c in string.digits for c in pwd),
        "symbol": any(c in SYMBOLS for c in pwd),
    }
    if require_symbols and not has["symbol"]:
        return False
    return has["lower"] and has["upper"] and has["digit"]


def random_name() -> tuple[str, str]:
    """随机姓名（first, last），与 addresses.py 共用同一份姓名库。"""
    from .addresses import FIRST, LAST
    r = secrets.choice
    return r(FIRST).capitalize(), r(LAST).capitalize()
