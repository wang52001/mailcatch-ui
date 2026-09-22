"""身份生成器 · 本地小页面（一次一个）

给「我自己手动注册一个账号」这个场景用的：
1. 生成一个随机收件地址 + 强密码 + 随机姓名
2. 你去目标站点手动填
3. 回来点「等验证码」→ 这里轮询 IMAP 把验证码显示给你

刻意的设计：
- **一次只出一个**，没有批量参数（批量场景不是这个小工具的用途）
- 只读写你自己的邮箱，**完全不碰任何目标站点**
- 只监听 127.0.0.1，外网访问不到

跑法：
    python cli.py ui                    # http://127.0.0.1:8123
    python cli.py ui --port 9000
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import Mailbox, config as cfgmod
from .addresses import random_address
from .passwords import generate_password, random_name

PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>身份生成器</title>
<style>
  :root{--bg:#f7f7f8;--card:#fff;--line:#e5e5e7;--text:#1a1a1a;--muted:#6b6b70;
        --ok:#0a7f3f;--bad:#c0392b;--brand:#2563eb}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);
       font:14px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
  .wrap{max-width:640px;margin:0 auto;padding:28px 20px 60px}
  h1{font-size:19px;margin:0 0 4px}
  .sub{color:var(--muted);margin-bottom:18px;font-size:13px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px;margin-bottom:14px}
  .row{display:flex;gap:10px;align-items:center;margin:10px 0}
  .row .k{width:64px;color:var(--muted);font-size:12px;flex:none}
  .row input{flex:1;padding:9px 10px;border:1px solid var(--line);border-radius:6px;
             font:13px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;background:#fbfbfc;color:var(--text)}
  .row select{flex:1;padding:9px 10px;border:1px solid var(--line);border-radius:6px;
              font:13px/1.4 system-ui,-apple-system,sans-serif;background:#fbfbfc;color:var(--text)}
  button{background:var(--brand);color:#fff;border:0;border-radius:6px;padding:9px 16px;
         font-size:14px;cursor:pointer}
  button.ghost{background:#fff;color:var(--text);border:1px solid var(--line)}
  button:disabled{opacity:.5;cursor:not-allowed}
  button.sm{padding:5px 10px;font-size:12px}
  .hint{color:var(--muted);font-size:12px;margin-top:8px}
  .code{font-size:30px;letter-spacing:4px;font-weight:600;color:var(--ok);margin:6px 0}
  .bad{color:var(--bad)}
  .note{background:#fff8e6;border:1px solid #f2dfae;color:#7a5b12;border-radius:8px;
        padding:10px 12px;font-size:12px;margin-bottom:14px}
  table{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}
  th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}
  th{color:var(--muted);font-weight:600;font-size:12px}
  td.mono{font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;word-break:break-all}
</style></head><body><div class="wrap">
<h1>身份生成器</h1>
<div class="sub">一次出一个：生成地址和密码 → 你去手动注册 → 回来取验证码。</div>

<div class="note">页面只读写你自己的邮箱（IMAP），<b>不碰任何目标站点</b>：没有自动填表、
没有批量生成、没有任何验证码识别。批量场景不是它的用途。</div>

<div class="card">
  <div class="row"><span class="k">域名</span><select id="domain"></select></div>
  <div class="row"><span class="k">邮箱</span><input id="email" placeholder="点生成，或手动粘贴已有账号"><button class="sm ghost" onclick="cp('email')">复制</button></div>
  <div class="row"><span class="k">密码</span><input id="password" placeholder="点生成，或手动填已有密码"><button class="sm ghost" onclick="cp('password')">复制</button></div>
  <div class="row"><span class="k">姓名</span><input id="name" placeholder="选填"><button class="sm ghost" onclick="cp('name')">复制</button></div>
  <div class="row" style="margin-top:14px">
    <button id="gen">生成一个</button>
    <button id="wait" class="ghost">等验证码</button>
    <span class="hint" id="state" style="margin-left:auto">新注册点「生成一个」；已有账号直接填上面两栏</span>
  </div>
</div>

<div class="card">
  <b>验证码</b>
  <div class="code" id="code">——</div>
  <div class="hint" id="codelsg">点上面的「等验证码」后，这里会自动显示收到的验证码</div>
</div>

<div class="card">
  <b>账号池</b>
  <div class="hint">注册成功之后，把这个账号存下来。（本地 SQLite，只在你这台机器上）</div>
  <div class="row"><span class="k">站点</span><input id="site" placeholder="比如 cursor.com"></div>
  <div class="row"><span class="k">备注</span><input id="note" placeholder="选填"></div>
  <div class="row" style="margin-top:12px">
    <button id="save">存入账号池</button>
    <a href="/api/export?format=csv"><button class="ghost" type="button">导出 CSV</button></a>
    <a href="/api/export?format=json"><button class="ghost" type="button">导出 JSON</button></a>
    <span class="hint" id="savemsg" style="margin-left:auto">邮箱和密码填好就能存</span>
  </div>
  <table>
    <thead><tr><th>站点</th><th>邮箱</th><th>密码</th><th>状态</th><th>时间</th><th></th></tr></thead>
    <tbody id="rows"></tbody>
  </table>
  <div class="hint">密码是明文存的 —— 这个文件别同步到网盘，别进 git。</div>
  <div class="hint">托管版（Render 免费实例）重启或休眠唤醒后本地文件会重置，<b>存完记得导出 CSV</b>。</div>
</div>
</div>
<script>
const $ = s => document.querySelector(s);
function cp(id){ const el=$('#'+id); el.select(); document.execCommand('copy'); }
async function poll(addr, sec, round){
  round = round || 1;
  // 单轮最多 90 秒：托管在 Cloudflare Worker 后面时，平台会在 ~100 秒切断请求
  const r = await fetch('/api/wait?address='+encodeURIComponent(addr)+'&timeout='+sec)
             .then(r=>r.json());
  if(r.code){ $('#code').textContent = r.code;
              $('#codelsg').textContent = '来自：' + (r.subject || '(无主题)');
              $('#wait').disabled = false; $('#state').textContent = '已收到'; return; }
  if(round < 3){ $('#state').textContent = `第 ${round} 轮没等到，再等一轮（第 ${round+1}/3 轮）…`;
                 return poll(addr, sec, round + 1); }
  $('#wait').disabled = false;
  $('#state').textContent = '三轮都没等到，去邮箱看看是不是被拦了';
  $('#code').textContent = '——';
}
$('#gen').onclick = async () => {
  const d = await fetch('/api/identity', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({domain: $('#domain').value})}).then(r=>r.json());
  if(d.error){ alert(d.error); return; }
  $('#email').value = d.email; $('#password').value = d.password; $('#name').value = d.name;
  $('#code').textContent = '——'; $('#codelsg').textContent = '点「等验证码」后这里显示';
  $('#wait').disabled = false; $('#save').disabled = false;
  $('#state').textContent = '已生成，去注册吧';
  $('#savemsg').textContent = '注册成功后再点存入';
};
(async function loadDomains(){
  const r = await fetch('/api/domains').then(r=>r.json()).catch(()=>({domains:[]}));
  const sel = $('#domain');
  if(!r.domains || !r.domains.length){
    sel.innerHTML = '<option value="">（没配域名，见 config.json）</option>'; return;
  }
  sel.innerHTML = r.domains.map((d,i)=>`<option value="${d}">${d}</option>`).join('');
  // 每个域名用一次再换下一个，测试多域名时省得手动点
  sel.onchange = () => { localStorage.setItem('mc_domain', sel.value); };
  const saved = localStorage.getItem('mc_domain');
  if(saved && r.domains.includes(saved)) sel.value = saved;
})();

$('#wait').onclick = () => {
  const a = $('#email').value; if(!a) return;
  $('#wait').disabled = true; $('#code').textContent = '…';
  $('#state').textContent = '正在轮询邮箱…';
  poll(a, 90);
};

// ---- 账号池 ----
const esc2 = s => String(s ?? '').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
async function loadPool(){
  const rows = await fetch('/api/pool').then(r=>r.json());
  $('#rows').innerHTML = rows.length ? rows.map(r=>`<tr>
    <td>${esc2(r.site || '—')}</td>
    <td class="mono">${esc2(r.email)}</td>
    <td class="mono">${esc2(r.password)}</td>
    <td>${esc2(r.status)}</td>
    <td class="hint">${esc2(r.created_at)}</td>
    <td><button class="sm ghost" onclick="del(${r.id})">删</button></td></tr>`).join('')
    : '<tr><td colspan="6" class="hint">还没有存过账号</td></tr>';
}
async function del(id){
  if(!confirm('确定删除这条记录？')) return;
  await fetch('/api/pool/'+id, {method:'DELETE'});
  loadPool();
}
$('#save').onclick = async () => {
  const body = {email:$('#email').value, password:$('#password').value,
                name:$('#name').value, site:$('#site').value.trim(),
                note:$('#note').value.trim(), status:'registered'};
  if(!body.email || !body.password){ $('#savemsg').textContent = '先生成一个身份'; return; }
  const r = await fetch('/api/pool', {method:'POST', headers:{'Content-Type':'application/json'},
                                      body: JSON.stringify(body)}).then(r=>r.json());
  $('#savemsg').textContent = r.error ? ('失败：'+r.error) : ('已存入：'+(body.site || body.email));
  if(!r.error){ $('#site').value=''; $('#note').value=''; }
  loadPool();
};
loadPool();
</script>
</body></html>
"""

_POOL_LOCK = threading.Lock()
POOL_DB = Path(__file__).resolve().parent.parent / "pool.db"

POOL_SCHEMA = """
CREATE TABLE IF NOT EXISTS pool (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    email      TEXT NOT NULL UNIQUE,
    password   TEXT NOT NULL,
    name       TEXT,
    site       TEXT,
    note       TEXT,
    status     TEXT DEFAULT 'registered',
    created_at TEXT NOT NULL
);
"""


def pool_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(POOL_DB), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(POOL_SCHEMA)
    conn.commit()
    return conn


def pool_add(rec: dict[str, Any]) -> dict[str, Any]:
    with _POOL_LOCK:
        conn = pool_conn()
        conn.execute(
            """INSERT INTO pool(email, password, name, site, note, status, created_at)
               VALUES(:email,:password,:name,:site,:note,:status,:created_at)
               ON CONFLICT(email) DO UPDATE SET
                 password=excluded.password, name=excluded.name, site=excluded.site,
                 note=excluded.note, status=excluded.status""",
            {**rec, "created_at": time.strftime("%Y-%m-%d %H:%M:%S")},
        )
        conn.commit()
        row = conn.execute("SELECT * FROM pool WHERE email=?", (rec["email"],)).fetchone()
        out = dict(row)
        conn.close()
    return out


def pool_list(limit: int = 200) -> list[dict[str, Any]]:
    conn = pool_conn()
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM pool ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    ]
    conn.close()
    return rows


def pool_delete(pid: int) -> None:
    with _POOL_LOCK:
        conn = pool_conn()
        conn.execute("DELETE FROM pool WHERE id=?", (pid,))
        conn.commit()
        conn.close()


# 每个地址的轮询任务：{address: {"code":..., "subject":...}}
_WAITERS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


def _new_identity(domain: str | None = None) -> dict[str, str]:
    cfg = cfgmod.load_config()
    domains = cfg.domains or ([cfg.domain] if cfg.domain else [])
    if not domains:
        raise ValueError(
            "mailcatch 配置里没有 domain / domains，"
            "先在 config.json 填上你的 catch-all 域名"
        )
    chosen = (domain or "").strip().lower().rstrip(".") or domains[0]
    if chosen not in domains:
        raise ValueError(
            f"域名 {chosen!r} 不在配置里。可用：{', '.join(domains)}"
        )
    first, last = random_name()
    return {
        "email": random_address(chosen, cfg.address_style, cfg.address_prefix),
        "password": generate_password(16),
        "name": f"{first} {last}",
    }


def _start_wait(cfg, address: str, timeout: int, interval: int = 5) -> None:
    def worker() -> None:
        try:
            with Mailbox(cfg.imap) as mb:
                hit = mb.wait_for_code(address=address, timeout=timeout, interval=interval)
            with _LOCK:
                if hit:
                    _WAITERS[address] = {"code": hit[1], "subject": hit[0].subject}
                else:
                    _WAITERS[address] = {"code": "", "subject": ""}
        except Exception as e:  # pragma: no cover
            with _LOCK:
                _WAITERS[address] = {"code": "", "subject": "", "error": str(e)}

    threading.Thread(target=worker, daemon=True).start()


def build_handler():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj: dict) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       "application/json")

        def _safe(self, fn) -> None:
            """兜底：未捕获异常会让连接直接关闭，反向代理只看到 502，线上没法排查。"""
            try:
                fn()
            except (BrokenPipeError, ConnectionResetError):
                raise
            except Exception:
                import traceback
                tb = traceback.format_exc()
                print("[error] " + tb, flush=True)
                try:
                    self._json(500, {"error": "服务器内部错误", "trace": tb.splitlines()[-1]})
                except Exception:
                    pass

        def do_GET(self) -> None:
            self._safe(self._get)

        def _get(self) -> None:
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)

            if u.path == "/healthz":
                # 给平台健康检查用的：不带任何数据，也不需要登录
                self._json(200, {"ok": True, "service": "mailcatch-ui"})
                return

            if u.path == "/api/domains":
                try:
                    cfg = cfgmod.load_config()
                    domains = cfg.domains or ([cfg.domain] if cfg.domain else [])
                except Exception as e:
                    self._json(400, {"error": f"{type(e).__name__}: {e}"})
                    return
                self._json(200, {"domains": domains, "count": len(domains)})
                return

            if u.path == "/api/selftest":
                # 线上排障用：配置到底加载成功没有
                import os
                import sys
                info: dict[str, Any] = {
                    "python": sys.version.split()[0],
                    "cwd": os.getcwd(),
                    "has_config_json_env": bool(os.environ.get("MAILCATCH_CONFIG_JSON")),
                }
                p = os.environ.get("MAILCATCH_CONFIG")
                info["config_path"] = p or ""
                info["config_file_exists"] = bool(p) and Path(p).is_file()
                try:
                    cfg = cfgmod.load_config()
                    info["config_ok"] = True
                    info["domain"] = cfg.domain
                    info["imap_host"] = cfg.imap.host
                    info["imap_user_set"] = bool(cfg.imap.user)
                except Exception as e:
                    info["config_ok"] = False
                    info["config_error"] = f"{type(e).__name__}: {e}"
                self._json(200, info)
                return

            if u.path == "/":
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
                return

            if u.path == "/api/wait":
                # 这里会阻塞到超时或收到为止（页面那边已经在转圈了）
                address = (q.get("address") or [""])[0]
                timeout = int((q.get("timeout") or ["90"])[0])
                if not address:
                    self._json(400, {"error": "缺 address"})
                    return
                try:
                    cfg = cfgmod.load_config()
                except Exception as e:
                    self._json(400, {"error": str(e)})
                    return
                started = time.time()
                try:
                    with Mailbox(cfg.imap) as mb:
                        hit = mb.wait_for_code(address=address, timeout=timeout, interval=5)
                except Exception as e:
                    self._json(500, {"error": f"IMAP 失败：{e}"})
                    return
                waited = int(time.time() - started)
                if hit:
                    self._json(200, {"code": hit[1], "subject": hit[0].subject, "waited": waited})
                else:
                    self._json(200, {"code": "", "waited": waited})
                return

            if u.path == "/api/pool":
                self._json(200, pool_list())
                return

            if u.path == "/api/export":
                fmt = (q.get("format") or ["csv"])[0].lower()
                rows = pool_list(limit=100000)
                stamp = time.strftime("%Y%m%d-%H%M%S")
                if fmt == "json":
                    body = json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header(
                        "Content-Disposition",
                        f'attachment; filename="accounts-{stamp}.json"',
                    )
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                import csv
                import io

                buf = io.StringIO()
                w = csv.writer(buf)
                w.writerow(["站点", "邮箱", "密码", "姓名", "状态", "备注", "时间"])
                for r in rows:
                    w.writerow([
                        r.get("site") or "", r.get("email") or "", r.get("password") or "",
                        r.get("name") or "", r.get("status") or "", r.get("note") or "",
                        r.get("created_at") or "",
                    ])
                # Excel 打开中文不乱码
                out = ("\ufeff" + buf.getvalue()).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header(
                    "Content-Disposition", f'attachment; filename="accounts-{stamp}.csv"'
                )
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
                return

            self._json(404, {"error": "not found"})

        def do_POST(self) -> None:
            self._safe(self._post)

        def _post(self) -> None:
            if self.path.startswith("/api/identity"):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    domain = json.loads(raw or b"{}").get("domain") or None
                except json.JSONDecodeError:
                    self._json(400, {"error": "body 不是合法 JSON"})
                    return
                try:
                    ident = _new_identity(domain)
                except Exception as e:
                    self._json(400, {"error": f"{type(e).__name__}: {e}"})
                    return
                self._json(200, ident)
                return

            if self.path.startswith("/api/pool"):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw or b"{}")
                except json.JSONDecodeError:
                    self._json(400, {"error": "body 不是合法 JSON"})
                    return
                email = str(body.get("email") or "").strip()
                password = str(body.get("password") or "").strip()
                if not email or not password:
                    self._json(400, {"error": "email 和 password 都不能为空"})
                    return
                saved = pool_add({
                    "email": email,
                    "password": password,
                    "name": str(body.get("name") or ""),
                    "site": str(body.get("site") or ""),
                    "note": str(body.get("note") or ""),
                    "status": str(body.get("status") or "registered"),
                })
                saved["password"] = "***"  # 回包不回显密码
                self._json(200, saved)
                return

            self._json(404, {"error": "not found"})

        def do_DELETE(self) -> None:
            self._safe(self._delete)

        def _delete(self) -> None:
            if self.path.startswith("/api/pool/"):
                pid = self.path.rsplit("/", 1)[-1]
                if not pid.isdigit():
                    self._json(400, {"error": "id 不合法"})
                    return
                pool_delete(int(pid))
                self._json(200, {"ok": True})
                return
            self._json(404, {"error": "not found"})

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8123) -> None:
    srv = ThreadingHTTPServer((host, port), build_handler())
    print(f"身份生成器已启动： http://{host}:{port}   （Ctrl+C 停止）", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


__all__ = ["serve", "build_handler"]
