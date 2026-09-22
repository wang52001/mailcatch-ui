"""随机收件地址生成。

四种风格：
    word    -> "quiet-lake-8271@example.com"    可读、像人起的别名（自有域名 catch-all）
    name    -> "e.moore0912@example.com"        姓氏+名字首字母+日期
    uuid    -> "3f9a1c2e@example.com"           纯随机、最不显眼
    subaddr -> "yourname+quiet-lake-827@qq.com" 主流邮箱的 + 子地址，无需自有域名

⚠️ subaddr 只对支持 plus addressing 的服务商有效（Gmail / Proton / Fastmail /
Yahoo / mail.com）。QQ、163、126、Outlook、iCloud 都不支持 —— 发过去会被判
「查无此人」直接退信，所以 config.py 里做了硬校验，别踩这个坑。

另外：自有域名 catch-all 虽然最灵活，但**如果顶级域是 .top / .xyz / .click
这类高滥用后缀，注册表单的前端校验会直接判为无效邮箱**（很多站点内置了
一次性邮箱黑名单）。要拿真实站点练手，优先用 subaddr + Gmail。
"""

from __future__ import annotations

import random
import string
import time

ADJECTIVES = [
    "quiet", "brisk", "amber", "nordic", "calm", "swift", "lucid", "cobalt",
    "hazel", "crisp", "mellow", "feral", "silent", "vivid", "plaid", "solar",
]
NOUNS = [
    "lake", "ridge", "harbor", "maple", "ember", "canyon", "lantern", "pebble",
    "falcon", "willow", "mesa", "compass", "orchid", "tundra", "beacon", "quartz",
]
FIRST = [
    "emma", "liam", "noah", "ava", "ethan", "mia", "lucas", "zoe",
    "kai", "iris", "owen", "nina", "theo", "ruby", "levi", "alma",
]
LAST = [
    "moore", "chen", "rivera", "novak", "hughes", "tanaka", "silva", "weber",
    "olsen", "patel", "klein", "rossi", "larsen", "dupont", "hayes", "fischer",
]


def _rand_digits(n: int) -> str:
    return "".join(random.choice(string.digits) for _ in range(n))


def _rand_hex(n: int) -> str:
    return "".join(random.choice("0123456789abcdef") for _ in range(n))


def random_localpart(style: str = "word", prefix: str = "") -> str:
    if style == "subaddr":
        # 形如 "<prefix>+quiet-lake-827"；prefix 必须是主邮箱 @ 前面那一段
        if not prefix:
            raise ValueError(
                "subaddr 风格需要在 config.json 里把 address_prefix 设成主邮箱的本地部分"
                "（例如主邮箱是 you@gmail.com，就填 \"you\"）"
            )
        return f"{prefix}+{random.choice(ADJECTIVES)}-{random.choice(NOUNS)}-{_rand_digits(3)}"
    if style == "name":
        local = f"{random.choice(FIRST)}.{random.choice(LAST)}{time.strftime('%m%d')}"
    elif style == "uuid":
        local = _rand_hex(10)
    else:  # word
        local = f"{random.choice(ADJECTIVES)}-{random.choice(NOUNS)}-{_rand_digits(3)}"
    if prefix:
        local = f"{prefix}{local}"
    return local


def random_address(domain: str, style: str = "word", prefix: str = "") -> str:
    """生成 `随机串@域名`。

    style=subaddr 时 domain 应填**收件邮箱自己的域名**（如 qq.com / gmail.com），
    不需要 catch-all，全靠 + 子地址把同一邮箱变出无限多个地址。
    """
    if not domain:
        raise ValueError("domain 为空：先在 config.json 里填好你的域名")
    domain = domain.lstrip("@")
    if style == "subaddr" and "+" in domain:
        raise ValueError(f"subaddr 风格的 domain 不该带 +（收到 {domain!r}），请只填邮箱域名")
    return f"{random_localpart(style, prefix)}@{domain}"


def is_valid_localpart(local: str) -> bool:
    """本地部分合法性检查（RFC 5321 的保守子集）。"""
    if not local or len(local) > 64 or ".." in local:
        return False
    if local.startswith(".") or local.endswith("."):
        return False
    allowed = set(string.ascii_letters + string.digits + "._-+!'#$%&*/=?^`{|}~")
    return all(c in allowed for c in local)
