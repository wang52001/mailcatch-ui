"""邮件验证码提取。

策略（从准到松，命中即止）：
    1. 关键词邻域：在 验证码 / verification code / OTP 等词附近 30 字符内找 4-8 位数字
    2. code=123456 这类键值写法
    3. 回退：正文中孤立的 4-8 位数字，剔除年份（19xx / 20xx）和长数字串中的片段
"""

from __future__ import annotations

import html
import re

KEYWORDS = [
    r"verification\s*code", r"verify\s*code", r"confirmation\s*code",
    r"security\s*code", r"one[- ]?time\s*(?:code|password)", r"\bOTP\b",
    r"pass\s?code", r"activation\s*code", r"sign[- ]?in\s*code", r"\bcode\b",
    # 中文
    r"验证码", r"校验码", r"动态码", r"动态密码", r"确认码", r"激活码", r"安全码",
]

_KW_RE = re.compile("|".join(KEYWORDS), re.IGNORECASE)
_KV_RE = re.compile(
    r"(?:code|otp|token|验证码|校验码|动态码)\s*[:：=＝\s]{1,4}\s*([0-9]{4,8})",
    re.IGNORECASE,
)
_DIGITS_RE = re.compile(r"(?<![0-9])([0-9]{4,8})(?![0-9])")
_YEAR_RE = re.compile(r"^(?:19|20)[0-9]{2}$")
# 8 位且以 19/20 开头，基本是 YYYYMMDD 日期串，不是验证码
_DATELIKE_RE = re.compile(r"^(?:19|20)[0-9]{6}$")

_TAG_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")


def strip_html(raw: str) -> str:
    """去掉 HTML 标签/脚本，留纯文本。够用即可，不引第三方依赖。"""
    text = _TAG_SCRIPT_RE.sub(" ", raw)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    return text


def _score(token: str, distance: int | None) -> int:
    """给候选打分：越像验证码越高。"""
    s = 0
    if distance is not None:
        s += max(0, 60 - distance)          # 离关键词越近越好
    if len(token) == 6:
        s += 30                              # 6 位最常见
    elif len(token) in (4, 5, 7, 8):
        s += 10
    if _YEAR_RE.match(token):
        s -= 80                              # 年份噪声
    if _DATELIKE_RE.match(token):
        s -= 60                              # YYYYMMDD 日期串
    if len(set(token)) == 1:
        s -= 20                              # 111111 / 000000
    return s


def extract_code(subject: str = "", body: str = "") -> str | None:
    """从主题+正文里提取最可能的验证码，提取不到返回 None。"""
    text = strip_html(f"{subject or ''}\n{body or ''}")
    if not text.strip():
        return None

    # 1) 键值写法，最明确
    m = _KV_RE.search(text)
    if m and not _YEAR_RE.match(m.group(1)):
        return m.group(1)

    # 2) 关键词邻域
    best: tuple[int, str] | None = None
    for kw in _KW_RE.finditer(text):
        window = text[kw.end(): kw.end() + 40]
        for dm in _DIGITS_RE.finditer(window):
            token = dm.group(1)
            cand = (_score(token, dm.start()), token)
            if best is None or cand > best:
                best = cand
        # 关键词也可能在数字之后，如 "123456 is your code"
        back = text[max(0, kw.start() - 40): kw.start()]
        for dm in _DIGITS_RE.finditer(back):
            token = dm.group(1)
            cand = (_score(token, kw.start() - max(0, kw.start() - 40) - dm.end()), token)
            if best is None or cand > best:
                best = cand
    if best and best[0] > 0:
        return best[1]

    # 3) 回退：孤立数字。
    #    正文里压根没有验证码关键词时，只认 6 位——否则订单号、日期串会被误当验证码。
    has_keyword = bool(_KW_RE.search(text))
    lo, hi = (4, 8) if has_keyword else (6, 6)
    fallback: tuple[int, str] | None = None
    for dm in _DIGITS_RE.finditer(text):
        token = dm.group(1)
        if not (lo <= len(token) <= hi):
            continue
        cand = (_score(token, None), token)
        if fallback is None or cand > fallback:
            fallback = cand
    if fallback and fallback[0] > 0:
        return fallback[1]
    return None
