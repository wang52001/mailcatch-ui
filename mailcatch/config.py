"""配置加载。

查找顺序：
    1. 环境变量 MAILCATCH_CONFIG 指向的文件
    2. 当前目录 ./config.json
    3. ~/.config/mailcatch/config.json

配置里含邮箱应用专用密码，不要提交到 git（仓库已带 .gitignore）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ConfigError(RuntimeError):
    pass


# 常见收件邮箱的 IMAP 预设，省得手填
IMAP_PRESETS: dict[str, dict[str, Any]] = {
    "gmail": {"host": "imap.gmail.com", "port": 993, "ssl": True},
    "qq": {"host": "imap.qq.com", "port": 993, "ssl": True},
    "163": {"host": "imap.163.com", "port": 993, "ssl": True},
    "126": {"host": "imap.126.com", "port": 993, "ssl": True},
    "outlook": {"host": "outlook.office365.com", "port": 993, "ssl": True},
    "hotmail": {"host": "outlook.office365.com", "port": 993, "ssl": True},
    "icloud": {"host": "imap.mail.me.com", "port": 993, "ssl": True},
    "zoho": {"host": "imap.zoho.com", "port": 993, "ssl": True},
    "fastmail": {"host": "imap.fastmail.com", "port": 993, "ssl": True},
}


# 发信预设，只给 `cli.py probe` 做端到端自检用（自己发给自己）
SMTP_PRESETS: dict[str, dict[str, Any]] = {
    "gmail": {"host": "smtp.gmail.com", "port": 465},
    "qq": {"host": "smtp.qq.com", "port": 465},
    "163": {"host": "smtp.163.com", "port": 465},
    "126": {"host": "smtp.126.com", "port": 465},
    "outlook": {"host": "smtp-mail.outlook.com", "port": 587},
    "hotmail": {"host": "smtp-mail.outlook.com", "port": 587},
    "icloud": {"host": "smtp.mail.me.com", "port": 587},
    "zoho": {"host": "smtp.zoho.com", "port": 465},
    "fastmail": {"host": "smtp.fastmail.com", "port": 465},
}

# `user+tag@domain` 子地址：只有这些服务商支持，其余一律投不进去。
# 实测：往 <qq号>+tag@qq.com 投递，QQ SMTP 直接返回
#   550 The recipient may contain a non-existent account
# 所以 address_style=subaddr 时如果服务商不在白名单，直接报错而不是静默失败。
PLUS_ADDRESSING_PRESETS = {"gmail", "fastmail", "zoho", "proton", "yahoo", "mail"}

# 明确不支持 + 子地址的（给报错信息里点名用）
NO_PLUS_ADDRESSING_PRESETS = {"qq", "163", "126", "outlook", "hotmail", "icloud", "aol"}

# 地址风格取值
ADDRESS_STYLES = ("word", "name", "uuid", "subaddr")


@dataclass
class ImapConfig:
    host: str
    preset: str = ""
    port: int = 993
    ssl: bool = True
    user: str = ""
    password: str = ""
    mailbox: str = "INBOX"
    # 轮询参数
    poll_interval: int = 5
    poll_timeout: int = 300

    def require_credentials(self, context: str = "收信") -> None:
        missing = [n for n, v in (("imap.user", self.user), ("imap.password", self.password)) if not v]
        if missing:
            raise ConfigError(
                f"{context}需要 {' 和 '.join(missing)}，但配置里是空的。\n"
                f"密码要填邮箱的**应用专用密码**（不是登录密码）。可直接跑 `python cli.py setup` 生成配置。"
            )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ImapConfig":
        preset = d.get("preset")
        base = dict(IMAP_PRESETS.get(preset, {})) if preset else {}
        base.update({k: v for k, v in d.items() if k != "preset"})
        if not base.get("host"):
            raise ConfigError("imap.host 缺失（或用 imap.preset 指定服务商）")
        return cls(
            host=base["host"],
            preset=preset or "",
            port=int(base.get("port", 993)),
            ssl=bool(base.get("ssl", True)),
            user=base.get("user", ""),
            password=base.get("password", ""),
            mailbox=base.get("mailbox", "INBOX"),
            poll_interval=int(base.get("poll_interval", 5)),
            poll_timeout=int(base.get("poll_timeout", 300)),
        )


@dataclass
class Config:
    domain: str = ""
    imap: ImapConfig = field(default_factory=lambda: ImapConfig(host=""))
    # 本地地址生成
    address_style: str = "word"      # word | name | uuid | subaddr
    address_prefix: str = ""
    # 发信预设名，probe 用；不填则按 imap.preset 推断
    smtp_preset: str = ""
    # 邮件层实现：imap（真实收信，默认）｜ local（读本地 .eml 目录，离线跑通链路用）
    mailbox_kind: str = "imap"
    local_path: str = ""

    @property
    def smtp(self) -> dict[str, Any]:
        name = self.smtp_preset or getattr(self.imap, "preset", "") or ""
        return SMTP_PRESETS.get(name, {})

    @property
    def supports_plus_addressing(self) -> bool:
        """当前收件服务商支不支持 `user+tag@domain` 子地址。"""
        return (self.imap.preset or "").lower() in PLUS_ADDRESSING_PRESETS

    @property
    def mail_domain(self) -> str:
        """真正拿来拼地址的域名。

        subaddr 风格下不是自有 catch-all 域名，而是收件邮箱自己的域名
        （主邮箱 you@gmail.com -> gmail.com），从 imap.user 自动推导，
        省得再去 config 里改 domain 造成两边不一致。
        """
        if self.address_style == "subaddr":
            user = self.imap.user or ""
            if "@" in user:
                return user.split("@", 1)[1].strip().lower()
        return self.domain

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Config":
        mb = d.get("mailbox") or {}
        kind = str(mb.get("kind", "imap")).lower()
        if kind not in ("imap", "local"):
            raise ConfigError(f"mailbox.kind 只能是 imap / local，收到 {kind!r}")
        style = str(d.get("address_style", "word")).lower()
        if style not in ADDRESS_STYLES:
            raise ConfigError(
                f"address_style 只能是 {' / '.join(ADDRESS_STYLES)}，收到 {style!r}"
            )
        # local 模式不需要 imap 段，别因为缺 imap 配置就报错
        raw_imap = d.get("imap", {})
        if kind == "imap":
            imap = ImapConfig.from_dict(raw_imap)
        else:
            imap = ImapConfig(host=raw_imap.get("host", "") or "local")
        cfg = cls(
            domain=d.get("domain", ""),
            imap=imap,
            address_style=style,
            address_prefix=d.get("address_prefix", ""),
            smtp_preset=d.get("smtp_preset", ""),
            mailbox_kind=kind,
            local_path=str(mb.get("path", "")),
        )
        # 提前把 subaddr 的坑堵掉，别等到跑批才发现投不进去
        if style == "subaddr" and kind == "imap":
            preset = (imap.preset or "").lower()
            if preset in NO_PLUS_ADDRESSING_PRESETS:
                raise ConfigError(
                    f"address_style=subaddr 但 imap.preset={preset!r} 不支持 + 子地址。\n"
                    f"  实测往 {preset} 的 user+tag@ 投递会被直接退信（查无此人）。\n"
                    f"  支持 + 子地址的：{'、'.join(sorted(PLUS_ADDRESSING_PRESETS))}。\n"
                    f"  要么换成支持的服务商，要么把 address_style 改回 word 并用自有域名 catch-all。"
                )
            if not cfg.address_prefix:
                raise ConfigError(
                    "address_style=subaddr 时必须填 address_prefix，"
                    "值为主邮箱 @ 前面那一段（如 you@gmail.com 就填 \"you\"）。"
                )
        return cfg


def _candidate_paths() -> list[Path]:
    paths: list[Path] = []
    env = os.environ.get("MAILCATCH_CONFIG")
    if env:
        paths.append(Path(env).expanduser())
    paths.append(Path.cwd() / "config.json")
    paths.append(Path.home() / ".config" / "mailcatch" / "config.json")
    return paths


def load_config(path: str | None = None) -> Config:
    """加载配置，找不到文件就抛 ConfigError 并提示所有找过的位置。"""
    candidates = [Path(path).expanduser()] if path else _candidate_paths()
    for p in candidates:
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                raise ConfigError(f"配置文件不是合法 JSON：{p}（{e}）") from e
            cfg = Config.from_dict(data)
            cfg._path = p  # type: ignore[attr-defined]
            return cfg
    raise ConfigError(
        "未找到配置文件。已查找：\n  "
        + "\n  ".join(str(p) for p in candidates)
        + "\n\n复制 config.example.json 为 config.json 并填写，或设置 MAILCATCH_CONFIG 环境变量。"
    )
