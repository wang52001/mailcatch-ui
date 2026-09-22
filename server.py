#!/usr/bin/env python3
"""Render 部署入口：把 mailcatch/ui.py 的身份生成器当成一个 Web Service 跑起来。

环境变量：
    APP_PASSWORD          访问口令。在 Render 上必须设置，否则拒绝启动。
                          登录方式：页面内登录表单（成功后发 30 天 cookie），
                          不再弹浏览器原生 Basic Auth 框；Basic Auth 仍兼容，方便 curl。
    MAILCATCH_CONFIG_JSON 完整的 mailcatch 配置（含 IMAP 授权码），JSON 字符串
    PORT                  Render 自动注入，默认回退 8123

只依赖 Python 标准库。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# 两种布局都支持：deploy/server.py（同级有 ../mailcatch）或 server.py 与 mailcatch/ 平级
PKG_ROOT = next(
    (c for c in (ROOT.parent, ROOT) if (c / "mailcatch" / "ui.py").is_file()),
    ROOT.parent,
)
sys.path.insert(0, str(PKG_ROOT))

from mailcatch.ui import build_handler          # noqa: E402

IS_RENDER = os.environ.get("RENDER") == "true" or bool(os.environ.get("RENDER_SERVICE_ID"))

COOKIE_NAME = "mc_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 天

LOGIN_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>登录 · 身份生成器</title>
<style>
  :root{--bg:#f7f7f8;--card:#fff;--line:#e5e5e7;--text:#1a1a1a;--muted:#6b6b70;--brand:#2563eb}
  *{box-sizing:border-box}
  body{margin:0;font:15px/1.6 system-ui,-apple-system,"PingFang SC",sans-serif;
       background:var(--bg);color:var(--text);display:flex;align-items:center;justify-content:center;
       min-height:100vh}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:28px;width:320px}
  h1{font-size:18px;margin:0 0 4px}
  .hint{color:var(--muted);font-size:12px;margin-bottom:16px}
  input{width:100%;padding:9px 10px;border:1px solid var(--line);border-radius:8px;font-size:14px}
  input:focus{outline:2px solid var(--brand);border-color:transparent}
  button{width:100%;margin-top:12px;padding:9px;border:0;border-radius:8px;background:var(--brand);
         color:#fff;font-size:14px;cursor:pointer}
  .err{color:#c0392b;font-size:12px;margin-top:10px;min-height:16px}
</style></head><body>
<div class="card">
  <h1>身份生成器</h1>
  <div class="hint">输一次口令，30 天内不用再输</div>
  <input id="pw" type="password" placeholder="访问口令" autofocus>
  <button onclick="go()">登录</button>
  <div class="err" id="err"></div>
</div>
<script>
const $ = s => document.querySelector(s);
async function go(){
  const r = await fetch('/api/login', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({password: $('#pw').value})});
  if(r.ok){ location.href = '/'; return; }
  $('#err').textContent = '口令不对，再试';
}
$('#pw').addEventListener('keydown', e => { if(e.key === 'Enter') go(); });
</script>
</body></html>
"""


def prepare_config() -> None:
    """Render 上没有 config.json，从环境变量里取。"""
    raw = os.environ.get("MAILCATCH_CONFIG_JSON")
    if not raw:
        if IS_RENDER:
            print("[boot] 缺 MAILCATCH_CONFIG_JSON，页面能起来但生成身份会报错", flush=True)
        return
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SystemExit(f"MAILCATCH_CONFIG_JSON 不是合法 JSON：{e}")
    tmp = Path(tempfile.gettempdir()) / "mailcatch-config.json"
    tmp.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    os.environ["MAILCATCH_CONFIG"] = str(tmp)
    print(f"[boot] 已加载邮件配置（domain={cfg.get('domain')}）", flush=True)


def build_authed_handler():
    base = build_handler()
    expected = os.environ.get("APP_PASSWORD", "")
    # 会话令牌只由口令派生：口令不变则重启后 cookie 依然有效
    session_token = hashlib.sha256(f"mc-session-v1:{expected}".encode()).hexdigest()

    class Handler(base):  # type: ignore[misc, valid-type]
        # ---- 认证 ----

        def _authed(self) -> bool:
            if not expected:
                return not IS_RENDER  # 本地没口令放行；Render 上必须配
            for part in self.headers.get("Cookie", "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == COOKIE_NAME and hmac.compare_digest(v, session_token):
                    return True
            # Basic Auth 仍然兼容：curl / 脚本调用方便
            header = self.headers.get("Authorization", "")
            if header.startswith("Basic "):
                try:
                    _, _, pwd = base64.b64decode(header[6:]).decode("utf-8").partition(":")
                except Exception:
                    return False
                return hmac.compare_digest(pwd, expected)
            return False

        def _send(self, code: int, body: bytes, ctype: str, extra=None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or []):
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _handle_login(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                pw = str(json.loads(raw or b"{}").get("password") or "")
            except json.JSONDecodeError:
                pw = ""
            if not expected or not hmac.compare_digest(pw, expected):
                self._send(401, '{"error": "口令不对"}'.encode("utf-8"), "application/json")
                return
            cookie = (
                f"{COOKIE_NAME}={session_token}; Path=/; Max-Age={COOKIE_MAX_AGE}; "
                "HttpOnly; SameSite=Lax; Secure"
            )
            self._send(200, b'{"ok": true}', "application/json",
                       extra=[("Set-Cookie", cookie)])

        # ---- 路由门 ----

        def do_GET(self):
            # 健康检查不带口令，否则平台探测不到存活就把进程判死
            if self.path.split("?")[0].startswith("/healthz") or self._authed():
                super().do_GET()
                return
            self._send(200, LOGIN_PAGE.encode("utf-8"), "text/html; charset=utf-8")

        def do_POST(self):
            if self.path.split("?")[0] == "/api/login":
                self._handle_login()
                return
            if not self._authed():
                self._send(401, b'{"error": "unauthorized"}', "application/json")
                return
            super().do_POST()

        def do_DELETE(self):
            if not self._authed():
                self._send(401, b'{"error": "unauthorized"}', "application/json")
                return
            super().do_DELETE()

    return Handler


def main() -> None:
    prepare_config()

    if IS_RENDER and not os.environ.get("APP_PASSWORD"):
        raise SystemExit(
            "拒绝启动：Render 上必须设置 APP_PASSWORD。"
            "这个页面能读整个收件箱并存储明文密码，公网裸奔等于送人。"
        )

    from http.server import ThreadingHTTPServer

    port = int(os.environ.get("PORT") or 8123)
    host = "0.0.0.0" if IS_RENDER else "127.0.0.1"
    srv = ThreadingHTTPServer((host, port), build_authed_handler())
    print(f"身份生成器已启动： http://{host}:{port}  （口令登录: {bool(os.environ.get('APP_PASSWORD'))}）",
          flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
