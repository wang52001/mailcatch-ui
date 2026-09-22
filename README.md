# mailcatch 身份生成器（部署版）

一次性生成一个收件地址 + 强密码 + 姓名；注册后回来点「等验证码」，从 IMAP 把验证码取回来。
注册成功的账号可以存进账号池，支持导出 CSV / JSON。

只依赖 Python 标准库。

## 环境变量（必填）

| 变量 | 说明 |
|---|---|
| `APP_PASSWORD` | 访问口令（HTTP Basic Auth，用户名随便填，密码填这个）。**在 Render 上不设则拒绝启动** |
| `MAILCATCH_CONFIG_JSON` | mailcatch 完整配置的 JSON 字符串（domain / imap 凭据） |

```jsonc
{
  "domain": "mc.jdhsf.top",
  "imap": {
    "host": "imap.qq.com", "port": 993, "ssl": true,
    "user": "1655316141@qq.com", "password": "授权码", "mailbox": "INBOX"
  },
  "address_style": "word"
}
```

## 本地跑

```bash
python server.py          # 127.0.0.1:8123，未设 APP_PASSWORD 时本地放行
```

## 注意

- 账号池写在 SQLite（`pool.db`），**Render 免费实例磁盘是临时的**，重启/重新部署会丢。
  要持久保存就用导出功能，或挂 Render Disk。
- 密码明文存储（为了能复制）。数据库文件别进 git、别同步网盘。
- 这个页面能读整个收件箱 —— 公网部署必须设 `APP_PASSWORD`。
