"""域名 DNS 预检：判断这块域名能不能开 Cloudflare Email Routing。

不需要任何凭据 —— 全是公开 DNS 查询。用来在动手改控制台之前先看清现状，
尤其是「域名上已经挂着别人家的 MX」这种会打断现有收信的冲突。

依赖系统的 dig（macOS / 主流 Linux 自带）。
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field

CF_NS_SUFFIX = "ns.cloudflare.com."
CF_MX_SUFFIX = "mx.cloudflare.net."
CF_SPF_INCLUDE = "_spf.mx.cloudflare.net"

# 高滥用顶级域：便宜、注册宽松，被大量垃圾邮件和一次性邮箱占用，
# 很多站点注册表单内置的黑名单会**直接判为「无效邮箱」**（不是发不出去，是根本不让提交）。
# 实测：cursor.com 的注册页对 xxx@mc.jdhsf.top 直接报「请提供有效的邮箱地址」。
RISKY_TLDS = {
    "top", "xyz", "click", "buzz", "loan", "work", "rest", "monster",
    "gq", "tk", "ml", "cf", "ga",  # Freenom 免费域，滥用率极高
}


def tld_of(domain: str) -> str:
    d = domain.strip().rstrip(".").lower()
    return d.rsplit(".", 1)[-1] if "." in d else ""


class DnsError(RuntimeError):
    pass


@dataclass
class DnsReport:
    domain: str
    ns: list[str] = field(default_factory=list)
    mx: list[str] = field(default_factory=list)
    txt: list[str] = field(default_factory=list)

    @property
    def ns_is_cloudflare(self) -> bool:
        return bool(self.ns) and all(n.lower().endswith(CF_NS_SUFFIX) for n in self.ns)

    @property
    def mx_is_cloudflare(self) -> bool:
        return bool(self.mx) and all(m.lower().endswith(CF_MX_SUFFIX) for m in self.mx)

    @property
    def spf_includes_cloudflare(self) -> bool:
        return any(
            t.lower().startswith("v=spf1") and CF_SPF_INCLUDE in t.lower() for t in self.txt
        )

    @property
    def risky_tld(self) -> bool:
        """顶级域是不是高滥用后缀（.top/.xyz/.click 之类）。"""
        return tld_of(self.domain) in RISKY_TLDS

    @property
    def ready(self) -> bool:
        return self.ns_is_cloudflare and self.mx_is_cloudflare and self.spf_includes_cloudflare

    def problems(self) -> list[str]:
        p: list[str] = []
        if not self.ns:
            p.append("查不到 NS 记录：域名可能不存在，或 DNS 还没生效")
        elif not self.ns_is_cloudflare:
            p.append(f"NS 不在 Cloudflare（当前：{', '.join(self.ns) or '无'}），"
                     "需要先把域名的 DNS 托管迁到 Cloudflare")
        if not self.mx:
            p.append("没有 MX 记录：Email Routing 启用后 Cloudflare 会自动写入三条 route*.mx.cloudflare.net")
        elif not self.mx_is_cloudflare:
            p.append(f"MX 不是 Cloudflare 的（当前：{', '.join(self.mx)}）。"
                     "邮件不会走 Email Routing，且切换会**打断现有收信**")
        if not self.spf_includes_cloudflare:
            p.append(f"SPF 未包含 {CF_SPF_INCLUDE}：转发的邮件容易被收件方判为垃圾邮件")
        return p

    def warnings(self) -> list[str]:
        """不阻塞转发，但会让真实站点拒收的隐患。"""
        w: list[str] = []
        if self.risky_tld:
            w.append(
                f"顶级域 .{tld_of(self.domain)} 属于高滥用后缀：很多站点的注册表单"
                "内置一次性邮箱黑名单，会**在前端直接判为无效邮箱**（提交都提交不了）。"
                "要拿真实站点练手，别用这个域名当收件地址。"
            )
        if self.mx and self.domain.count(".") > 1:
            w.append(
                "这是纯收信子域（只有 MX，没有 A 记录）。绝大多数校验器只看 MX，"
                "但少数会先查 A 记录，遇到这种可以给子域补一条 A 记录兜底。"
            )
        return w

    def as_dict(self) -> dict:
        return {
            "domain": self.domain,
            "tld": tld_of(self.domain),
            "ns": self.ns,
            "mx": self.mx,
            "txt": self.txt,
            "ns_is_cloudflare": self.ns_is_cloudflare,
            "mx_is_cloudflare": self.mx_is_cloudflare,
            "spf_includes_cloudflare": self.spf_includes_cloudflare,
            "risky_tld": self.risky_tld,
            "ready": self.ready,
            "problems": self.problems(),
            "warnings": self.warnings(),
        }


def _dig(domain: str, rrtype: str) -> list[str]:
    exe = shutil.which("dig")
    if not exe:
        raise DnsError("系统里没有 dig，先装一下（macOS 自带；Linux: apt install dnsutils）")
    try:
        out = subprocess.run(
            [exe, "+short", rrtype, domain],
            capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired as e:
        raise DnsError(f"DNS 查询超时（{rrtype} {domain}）") from e
    if out.returncode != 0:
        raise DnsError(f"dig 失败：{out.stderr.strip()}")
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def check_domain(domain: str) -> DnsReport:
    d = domain.strip().rstrip(".").lower()
    if not d or "@" in d:
        raise DnsError(f"不是合法域名：{domain!r}")
    ns = _dig(d, "NS")
    # 子域通常没有自己的 NS（未做委派），这是正常的——回退到父域的 NS 来判断是否托管在 Cloudflare
    if not ns and d.count(".") > 1:
        ns = _dig(d.split(".", 1)[1], "NS")
    return DnsReport(
        domain=d,
        ns=ns,
        mx=[mx.split(None, 1)[-1] for mx in _dig(d, "MX")],
        txt=[t.strip('"') for t in _dig(d, "TXT")],
    )


def render(report: DnsReport) -> str:
    mark = lambda ok: "✓" if ok else "✗"
    tld = tld_of(report.domain)
    tld_note = "（高滥用后缀，真实站点常直接拒收）" if report.risky_tld else "（常规后缀）"
    lines = [
        f"域名  {report.domain}",
        f"TLD   .{tld} {tld_note}" if tld else "TLD   (无法识别)",
        f"NS    {mark(report.ns_is_cloudflare)} {'、'.join(report.ns) or '(无)'}",
        f"MX    {mark(report.mx_is_cloudflare)} {'、'.join(report.mx) or '(无)'}",
        f"SPF   {mark(report.spf_includes_cloudflare)} "
        + (next((t for t in report.txt if t.lower().startswith("v=spf1")), "(无 SPF 记录)")),
        "",
    ]
    problems = report.problems()
    if not problems:
        lines.append("结论  已就绪，catch-all 转发应该能正常工作")
    else:
        lines.append("结论  还不能转发，需要处理：")
        lines += [f"      {i}. {p}" for i, p in enumerate(problems, 1)]

    warnings = report.warnings()
    if warnings:
        lines.append("")
        lines.append("提醒  不影响转发，但会影响真实站点注册：")
        lines += [f"      {i}. {w}" for i, w in enumerate(warnings, 1)]
    return "\n".join(lines)
