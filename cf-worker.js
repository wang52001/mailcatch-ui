/**
 * Cloudflare Worker：把 mail.jdhsf.top 反代到 Render 上的 mailcatch-ui
 *
 * 为什么需要它：Render 免费账户自定义域名上限 2 个，已经被 new-api-gateway
 * （new-api.jdhsf.top / new-api-admin.jdhsf.top）占满，加不上去。
 * 直接在 DNS 里 CNAME 到 onrender.com 也不行 —— Render 收到不认识的 Host 会返回 404。
 * 所以让 Worker 在边缘把 Host 改掉再转发。
 *
 * 附带一个保活定时器：Render 免费实例 15 分钟没流量就休眠，下次访问要等 30~60 秒冷启动。
 * 加了 scheduled 之后 Cloudflare 每 10 分钟替你戳一下，实例一直醒着。
 *
 * 用法：Cloudflare 后台 → Workers 和 Pages → 创建 → 创建 Worker → 粘贴本文件 → 部署
 *      1）设置 → 触发器 → 路由 → 添加 mail.jdhsf.top/*
 *      2）设置 → 触发器 → Cron 触发器 → 添加 */10 * * * *     ← 保活开关，别漏
 */

const UPSTREAM = "mailcatch-ui.onrender.com";
const PUBLIC_HOST = "mail.jdhsf.top";

export default {
  async fetch(request) {
    const url = new URL(request.url);
    url.protocol = "https:";
    url.hostname = UPSTREAM;
    url.port = "";

    const headers = new Headers(request.headers);
    // 关键：不覆盖 Host 的话，Render 认不出这个站点，直接 404
    headers.set("Host", UPSTREAM);

    const hasBody = request.method !== "GET" && request.method !== "HEAD";
    const body = hasBody ? await request.arrayBuffer() : undefined;

    const resp = await fetch(url.toString(), {
      method: request.method,
      headers,
      body,
      redirect: "manual", // 自己处理跳转，免得把用户甩回 onrender.com
    });

    const out = new Headers(resp.headers);
    const loc = out.get("location");
    if (loc) {
      try {
        const u = new URL(loc, url);
        if (u.hostname === UPSTREAM) {
          u.hostname = PUBLIC_HOST;
          u.protocol = "https:";
          u.port = "";
          out.set("location", u.toString());
        }
      } catch { /* location 不是标准 URL 就原样放行 */ }
    }

    // 204/304 不能带 body
    if (resp.status === 204 || resp.status === 304) {
      return new Response(null, { status: resp.status, headers: out });
    }
    return new Response(resp.body, {
      status: resp.status,
      statusText: resp.statusText,
      headers: out,
    });
  },

  // 每 10 分钟由 Cron 触发一次：戳一下健康检查，让 Render 实例不睡
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(
      fetch(`https://${UPSTREAM}/healthz`, {
        headers: { "User-Agent": "keepalive-cron" },
      }).then(r => console.log("keepalive:", r.status))
    );
  },
};
