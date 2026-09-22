"""本地邮件层：从目录里读 .eml，接口和 IMAP 版 Mailbox 一致。

用途是**离线跑通整条链路**：目标站点把验证邮件写成 .eml 丢进一个目录，
这里读出来、解析验证码。不需要真实 IMAP、不需要域名、不碰网络。

生产/真实场景仍然用 IMAP 那套（mailbox.Mailbox），两者接口相同，
上层（flowforge.mailhook）按 config 里 mailbox.kind 选一个。
"""

from __future__ import annotations

import email
import email.utils
import time
from email import policy
from pathlib import Path
from typing import Iterator

from .code import extract_code
from .mailbox import Mailbox, MailboxError, Message, _decode


class LocalMailbox:
    """目录即信箱。每个 .eml 一封邮件。"""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self._seen: set[str] = set()

    # ---- 连接（本地无连接，保持接口一致）----
    def connect(self) -> "LocalMailbox":
        self.path.mkdir(parents=True, exist_ok=True)
        return self

    def close(self) -> None:
        pass

    def __enter__(self) -> "LocalMailbox":
        return self.connect()

    def __exit__(self, *exc) -> None:
        self.close()

    def check(self) -> dict:
        self.connect()
        files = sorted(self.path.glob("*.eml"))
        return {"kind": "local", "path": str(self.path), "messages": len(files)}

    # ---- 读 ----
    def _files(self) -> list[Path]:
        if not self.path.is_dir():
            return []
        return sorted(self.path.glob("*.eml"), key=lambda p: p.stat().st_mtime)

    def _load(self, f: Path) -> Message | None:
        try:
            with f.open("rb") as fh:
                msg = email.message_from_binary_file(fh, policy=policy.default)
        except Exception:
            return None
        recipients = []
        for h in ("X-Original-To", "X-Forwarded-To", "Delivered-To", "To", "Cc"):
            v = msg.get(h)
            if v:
                recipients.extend(str(v).replace(",", " ").split())
        recipients = [r.strip("<>").strip() for r in recipients if r.strip()]
        return Message(
            uid=f.name,
            subject=_decode(msg.get("Subject")),
            sender=_decode(msg.get("From")),
            recipients=recipients,
            date=str(msg.get("Date") or ""),
            body=Mailbox._plain_body(msg),
        )

    def list_messages(self, limit: int = 20, since: str | None = None) -> list[Message]:
        self.connect()
        out = [m for m in (self._load(f) for f in self._files()) if m]
        return out[-limit:]

    def find_for(self, address: str, since: str | None = None, limit: int = 5) -> list[Message]:
        self.connect()
        hits = [m for m in (self._load(f) for f in self._files())
                if m and Mailbox.matches(m, address)]
        return hits[-limit:]

    def wait_for_code(
        self,
        address: str | None = None,
        subject_contains: str | None = None,
        timeout: int | None = None,
        interval: int | None = None,
        verbose: bool = False,
    ) -> tuple[Message, str] | None:
        """轮询目录等验证码。返回 (邮件, 验证码)，超时返回 None。"""
        timeout = timeout or 300
        interval = max(1, interval or 2)
        self.connect()
        deadline = time.time() + timeout
        seen: set[str] = set()

        while time.time() < deadline:
            for f in self._files():
                if f.name in seen:
                    continue
                m = self._load(f)
                if m is None:
                    seen.add(f.name)
                    continue
                if address and not Mailbox.matches(m, address):
                    seen.add(f.name)
                    continue
                if subject_contains and subject_contains.lower() not in m.subject.lower():
                    seen.add(f.name)
                    continue
                code = m.code
                if code:
                    if verbose:
                        print(f"[hit] {f.name} subject={m.subject!r} code={code}")
                    return m, code
                seen.add(f.name)
            if verbose:
                print(f"[poll] 未命中，{int(deadline - time.time())}s 后超时…", flush=True)
            time.sleep(interval)
        return None

    def iter_new(self, interval: int = 10) -> Iterator[Message]:
        self.connect()
        seen = {f.name for f in self._files()}
        while True:
            for f in self._files():
                if f.name in seen:
                    continue
                seen.add(f.name)
                m = self._load(f)
                if m:
                    yield m
            time.sleep(interval)


def write_eml(directory: str | Path, to: str, subject: str, body: str,
              sender: str = "noreply@example.com") -> Path:
    """投递一封 .eml —— 给靶站/测试当「发信服务」用。"""
    d = Path(directory).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time() * 1000)
    f = d / f"{stamp}-{abs(hash(to)) % 10000:04d}.eml"
    raw = (
        f"From: {sender}\r\n"
        f"To: {to}\r\n"
        f"Subject: {subject}\r\n"
        f"Date: {email.utils.formatdate(localtime=True)}\r\n"
        f"MIME-Version: 1.0\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"\r\n{body}\r\n"
    )
    f.write_text(raw, encoding="utf-8")
    return f


def open_mailbox(cfg) -> "Mailbox | LocalMailbox":
    """按配置挑一个邮件层实现。"""
    kind = getattr(cfg, "mailbox_kind", "imap")
    if kind == "local":
        path = getattr(cfg, "local_path", "") or ""
        if not path:
            raise MailboxError("mailbox.kind=local 但没给 mailbox.path")
        return LocalMailbox(path)
    return Mailbox(cfg.imap)
