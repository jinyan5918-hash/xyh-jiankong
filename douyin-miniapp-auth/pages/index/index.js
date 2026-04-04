/**
 * 自有抖音号授权：ma.video.bind（视频数据查询）→ 服务端用 ticket 换 act.xxx
 * 文档：https://developer.open-douyin.com/docs/resource/zh-CN/mini-app/develop/api/open-interface/authorization/tt-show-douyin-open-auth/
 */
// TODO: 改为你们线上 HTTPS 接口；路径为 jiankong-api 的 POST /douyin/open-auth/ticket
// 域名须加入小程序「服务器域名」request 合法列表
const AUTH_BACKEND_URL = "";
// 与服务端 DOUYIN_OPENAUTH_CALLBACK_SECRET 一致时填写；不启用服务端校验则留空
const AUTH_BACKEND_BEARER_SECRET = "";

Page({
  data: {
    hint: "授权成功后 ticket 将发往服务端（需配置 AUTH_BACKEND_URL）。",
  },

  onRequestAuth() {
    const that = this;
    // 官方说明：scopes 为旧版参数仍可用；与 scopeList 同时传时 scopeList 优先（基础库 3.5.0+）
    // 若你们基础库较新，可改为 scopeList 形式（见官方文档「场景二」）
    tt.showDouyinOpenAuth({
      scopes: {
        "ma.video.bind": 2,
      },
      success(res) {
        const ticket = res.ticket || "";
        if (!ticket) {
          that.setData({
            hint: "未拿到 ticket，请查看 grantPermissions：" + JSON.stringify(res.grantPermissions || []),
          });
          return;
        }
        that.setData({ hint: "已获取 ticket，正在上报服务端…" });
        if (!AUTH_BACKEND_URL) {
          tt.showModal({
            title: "请配置后端地址",
            content:
              "在 pages/index/index.js 中设置 AUTH_BACKEND_URL。\n\n ticket 前 24 字符：\n" +
              ticket.substring(0, 24) +
              "…\n\n请勿把完整 ticket 发到公开渠道。",
            showCancel: false,
          });
          return;
        }
        const headers = { "content-type": "application/json" };
        if (AUTH_BACKEND_BEARER_SECRET) {
          headers.Authorization = "Bearer " + AUTH_BACKEND_BEARER_SECRET;
        }
        tt.request({
          url: AUTH_BACKEND_URL,
          method: "POST",
          header: headers,
          data: { ticket },
          success(r) {
            const body = r.data || {};
            const ok = body.ok !== false;
            that.setData({
              hint: ok ? "服务端已接收，可配置 jiankong-api 使用返回的 token。" : "服务端返回：" + JSON.stringify(body),
            });
            tt.showToast({ title: ok ? "已上报" : "上报失败", icon: ok ? "success" : "none" });
          },
          fail(e) {
            that.setData({ hint: "请求失败：" + (e.errMsg || String(e)) });
            tt.showToast({ title: "网络失败", icon: "none" });
          },
        });
      },
      fail(err) {
        const msg = (err && err.errMsg) || JSON.stringify(err || {});
        that.setData({ hint: "授权失败：" + msg });
        tt.showToast({ title: "授权失败", icon: "none" });
      },
    });
  },
});
