"""mailcatch —— 自有域名 catch-all 邮箱层。

能力边界：地址生成、IMAP 收信、验证码解析。
不包含（也不会包含）：任何站点注册自动化、人机验证绕过、机器 ID 或会话令牌的写入。
"""

from .addresses import random_address, random_localpart, is_valid_localpart
from .code import extract_code, strip_html
from .config import (
    ADDRESS_STYLES,
    Config,
    ConfigError,
    ImapConfig,
    IMAP_PRESETS,
    NO_PLUS_ADDRESSING_PRESETS,
    PLUS_ADDRESSING_PRESETS,
    load_config,
)
from .dnscheck import RISKY_TLDS, DnsError, DnsReport, check_domain, render, tld_of
from .localbox import LocalMailbox, open_mailbox, write_eml
from .mailbox import Mailbox, MailboxError, Message
from .passwords import generate_password, is_strong, random_name

__all__ = [
    "random_address",
    "random_localpart",
    "is_valid_localpart",
    "extract_code",
    "strip_html",
    "ADDRESS_STYLES",
    "Config",
    "ConfigError",
    "ImapConfig",
    "IMAP_PRESETS",
    "PLUS_ADDRESSING_PRESETS",
    "NO_PLUS_ADDRESSING_PRESETS",
    "load_config",
    "RISKY_TLDS",
    "DnsError",
    "DnsReport",
    "check_domain",
    "render",
    "tld_of",
    "Mailbox",
    "MailboxError",
    "Message",
    "LocalMailbox",
    "open_mailbox",
    "write_eml",
    "generate_password",
    "is_strong",
    "random_name",
]

__version__ = "0.1.0"
