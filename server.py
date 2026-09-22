#!/usr/bin/env python3
"""Render 部署入口：把 mailcatch/ui.py 的身份生成器当成一个 Web Service 跑起来。

环境变量：
    APP_PASSWORD          访问口令（Basic Auth 用户名随意，密码填这个）
                          在 Render 上必须设置，否则拒绝启动（页面能读整个收件箱）
    MAILCATCH_CONFIG_JSON 完整的 mailcatch 配置（含 IMAP 授权码），JSON 字符串
    PORT                  Render 自动注入，默认回退 8123

只依赖 Python 标准库。
"""

from __future__ import annotations

import base64
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

    class Handler(base):  # type: ignore[misc, valid-type]
        def _auth_ok(self) -> bool:
            if not expected:
                return not IS_RENDER  # 本地没口令放行；Render 上必须配
            header = self.headers.get("Authorization", "")
            if not header.startswith("Basic "):
                return False
            try:
                user, _, pwd = base64.b64decode(header[6:]).decode("utf-8").partition(":")
            except Exception:
                return False
            return pwd == expected

        def _challenge(self) -> None:
            body = b"unauthorized"
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="mailcatch"')
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._gate(super().do_GET)

        def do_POST(self):
            self._gate(super().do_POST)

        def do_DELETE(self):
            self._gate(super().do_DELETE)

        def _gate(self, fn):
            # 健康检查不带口令，否则平台探测不到存活就把进程判死
            if self.path.split("?")[0].startswith("/healthz"):
                fn()
                return
            if not self._auth_ok():
                self._challenge()
                return
            fn()

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
    print(f"身份生成器已启动： http://{host}:{port}  （Basic Auth: {bool(os.environ.get('APP_PASSWORD'))}）",
          flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
