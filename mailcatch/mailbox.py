"""IMAP 收信层：连接、检索、轮询等待、按原始收件人过滤。

catch-all 关键点：目标站点发给 `abc@你的域名`，Cloudflare 转发到你的真实邮箱后，
原始收件人通常保留在 To / Delivered-To / X-Original-To / X-Forwarded-To 之一，
也可能只出现在正文里。这里四个头都查，最后再退回正文匹配。
"""

from __future__ import annotations

import email
import imaplib
import re
import time
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from typing import Iterable, Iterator, Sequence

from .code import extract_code, strip_html
from .config import ImapConfig

# 存放「原始收件人」的候选头，各家转发服务不一致，全查一遍
FORWARD_HEADERS = ("X-Original-To", "X-Forwarded-To", "Delivered-To", "Envelope-To", "To", "Cc")


class MailboxError(RuntimeError):
    pass


@dataclass
class Message:
    uid: str
    subject: str
    sender: str
    recipients: list[str]
    date: str
    body: str

    @property
    def code(self) -> str | None:
        return extract_code(self.subject, self.body)

    def as_dict(self) -> dict:
        return {
            "uid": self.uid,
            "subject": self.subject,
            "from": self.sender,
            "to": self.recipients,
            "date": self.date,
            "code": self.code,
        }


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:
        return value.strip()


class Mailbox:
    def __init__(self, cfg: ImapConfig):
        self.cfg = cfg
        self._conn: imaplib.IMAP4 | None = None

    # ---- 连接 ----
    def connect(self) -> "Mailbox":
        if self._conn is not None:
            return self
        try:
            cls = imaplib.IMAP4_SSL if self.cfg.ssl else imaplib.IMAP4
            self._conn = cls(self.cfg.host, self.cfg.port)
        except OSError as e:
            raise MailboxError(f"无法连接 {self.cfg.host}:{self.cfg.port} —— {e}") from e
        try:
            self._conn.login(self.cfg.user, self.cfg.password)
        except imaplib.IMAP4.error as e:
            raise MailboxError(
                "IMAP 登录失败。常见原因：未开启 IMAP / 用的是登录密码而不是**应用专用密码** / 账号被风控拦截。\n"
                f"服务端返回：{e}"
            ) from e
        return self

    def close(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.close()
        except Exception:
            pass
        try:
            self._conn.logout()
        except Exception:
            pass
        self._conn = None

    def __enter__(self) -> "Mailbox":
        return self.connect()

    def __exit__(self, *exc) -> None:
        self.close()

    def check(self) -> dict:
        """连通性自检：返回邮箱状态和消息总数。"""
        self.connect()
        assert self._conn is not None
        typ, data = self._conn.select(self.cfg.mailbox, readonly=True)
        if typ != "OK":
            raise MailboxError(f"无法选择邮箱 {self.cfg.mailbox}：{data}")
        typ, data = self._conn.search(None, "ALL")
        count = len(data[0].split()) if data and data[0] else 0
        return {"mailbox": self.cfg.mailbox, "messages": count}

    # ---- 检索 ----
    def _search(self, *criteria: str) -> list[str]:
        assert self._conn is not None
        typ, data = self._conn.search(None, *criteria)
        if typ != "OK":
            return []
        raw = data[0] if data and data[0] else b""
        return [u.decode() for u in raw.split()]

    def _fetch(self, uid: str) -> Message | None:
        assert self._conn is not None
        typ, data = self._conn.fetch(uid.encode(), "(RFC822)")
        if typ != "OK" or not data or not isinstance(data[0], tuple):
            return None
        msg = email.message_from_bytes(data[0][1])
        body = self._plain_body(msg)

        recipients: list[str] = []
        for h in FORWARD_HEADERS:
            for v in msg.get_all(h, []):
                for part in re.split(r"[,\s]+", _decode(v)):
                    addr = part.strip("<>").strip().lower()
                    if "@" in addr and addr not in recipients:
                        recipients.append(addr)

        date_raw = _decode(msg.get("Date"))
        try:
            date_iso = parsedate_to_datetime(date_raw).isoformat()
        except Exception:
            date_iso = date_raw
        if not date_iso:
            # 少数转发链路会丢 Date 头，退回服务端的 INTERNALDATE
            try:
                _, idata = self._conn.fetch(uid.encode(), "(INTERNALDATE)")
                raw = idata[0].decode() if idata and idata[0] else ""
                date_iso = raw.split('"')[1] if '"' in raw else ""
            except Exception:
                date_iso = ""

        return Message(
            uid=uid,
            subject=_decode(msg.get("Subject")),
            sender=_decode(msg.get("From")),
            recipients=recipients,
            date=date_iso,
            body=body,
        )

    @staticmethod
    def _plain_body(msg: email.message.Message) -> str:
        if msg.is_multipart():
            parts: list[tuple[str, str]] = []
            for part in msg.walk():
                if part.get_content_maintype() == "multipart":
                    continue
                ctype = part.get_content_type()
                try:
                    payload = part.get_payload(decode=True) or b""
                    charset = part.get_content_charset() or "utf-8"
                    text = payload.decode(charset, errors="replace")
                except Exception:
                    continue
                parts.append((ctype, text))
            for ctype, text in parts:
                if ctype == "text/plain":
                    return text
            for ctype, text in parts:
                if ctype == "text/html":
                    return strip_html(text)
            return ""
        try:
            payload = msg.get_payload(decode=True) or b""
            text = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            return ""
        return strip_html(text) if msg.get_content_type() == "text/html" else text

    def list_messages(self, limit: int = 20, since: str | None = None) -> list[Message]:
        """列出最近的邮件，新的在前。since 用 IMAP 日期格式 '01-Jan-2026'。"""
        self.connect()
        assert self._conn is not None
        self._conn.select(self.cfg.mailbox, readonly=True)
        criteria = ["ALL"] if not since else ["SINCE", since]
        uids = self._search(*criteria)
        uids = uids[-limit:] if limit else uids
        out = []
        for uid in reversed(uids):
            m = self._fetch(uid)
            if m:
                out.append(m)
        return out

    def _search_for(self, address: str, since: str | None) -> list[str]:
        """先走服务端 SEARCH，找不到再退回本地扫描最近 50 封。"""
        base = ["SINCE", since] if since else ["ALL"]
        found: list[str] = []
        for header in ("TO", "CC"):
            try:
                uids = self._search(*base, "HEADER", header, address)
            except imaplib.IMAP4.error:
                uids = []
            found.extend(uids)
        for header in FORWARD_HEADERS:
            try:
                uids = self._search(*base, "HEADER", header, address)
            except imaplib.IMAP4.error:
                continue
            found.extend(uids)
        uniq = list(dict.fromkeys(found))
        if uniq:
            return uniq

        # 退回：扫最近 50 封，在头或正文里找这个地址
        recent = self._search(*base)[-50:]
        hits = []
        needle = address.lower()
        for uid in recent:
            m = self._fetch(uid)
            if not m:
                continue
            if needle in " ".join(m.recipients).lower() or needle in m.body.lower():
                hits.append(uid)
        return hits

    @staticmethod
    def matches(m: Message, address: str) -> bool:
        """确认这封邮件真的是发给 address 的。

        不能只信服务端的 SEARCH：部分 IMAP 实现（如 QQ 邮箱）对不存在的头做
        HEADER 搜索时会返回全部邮件，必须本地再验一次。
        """
        needle = address.strip().lower()
        if not needle:
            return True
        if any(needle == r.lower() for r in m.recipients):
            return True
        blob = f"{m.subject}\n{m.body}".lower()
        return needle in blob

    def find_for(self, address: str, since: str | None = None, limit: int = 5) -> list[Message]:
        """取发给某个 catch-all 地址的邮件。"""
        self.connect()
        assert self._conn is not None
        self._conn.select(self.cfg.mailbox, readonly=True)
        uids = self._search_for(address, since)[-limit:]
        return [m for m in (self._fetch(u) for u in uids) if m and self.matches(m, address)]

    def wait_for_code(
        self,
        address: str | None = None,
        subject_contains: str | None = None,
        timeout: int | None = None,
        interval: int | None = None,
        verbose: bool = False,
    ) -> tuple[Message, str] | None:
        """轮询等待验证码。返回 (邮件, 验证码)，超时返回 None。"""
        timeout = timeout or self.cfg.poll_timeout
        interval = max(1, interval or self.cfg.poll_interval)
        self.connect()
        assert self._conn is not None

        since = time.strftime("%d-%b-%Y")
        deadline = time.time() + timeout
        seen: set[str] = set()

        while time.time() < deadline:
            self._conn.select(self.cfg.mailbox, readonly=True)
            uids = self._search_for(address, since) if address else self._search("SINCE", since)
            for uid in uids:
                if uid in seen:
                    continue
                m = self._fetch(uid)
                if not m:
                    continue
                # 服务端 SEARCH 会误报，本地再确认这封确实是发给 address 的
                if address and not self.matches(m, address):
                    seen.add(uid)
                    continue
                if subject_contains and subject_contains.lower() not in m.subject.lower():
                    seen.add(uid)
                    continue
                code = m.code
                if code:
                    if verbose:
                        print(f"[hit] uid={uid} subject={m.subject!r} code={code}")
                    return m, code
                seen.add(uid)
            if verbose:
                left = int(deadline - time.time())
                print(f"[poll] 未命中，{left}s 后超时…", flush=True)
            time.sleep(interval)
        return None

    def iter_new(self, interval: int = 10) -> Iterator[Message]:
        """持续产出新邮件（生成器，用于长期挂机监听）。"""
        self.connect()
        assert self._conn is not None
        seen = set(self._search("ALL"))
        while True:
            self._conn.select(self.cfg.mailbox, readonly=True)
            for uid in self._search("ALL"):
                if uid in seen:
                    continue
                seen.add(uid)
                m = self._fetch(uid)
                if m:
                    yield m
            time.sleep(interval)


def since_today() -> str:
    return time.strftime("%d-%b-%Y")
