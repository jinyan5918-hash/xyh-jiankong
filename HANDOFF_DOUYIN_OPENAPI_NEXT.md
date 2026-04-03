# 交接：抖音开放平台 + 小程序 + jiankong（明天续做）

**日期**：对话收尾时仓库 `main` 与 GitHub 一致。

---

## 当前进度

| 项 | 状态 |
|----|------|
| 企业已注册抖音开放平台 | 有 |
| 抖音小程序 AppID | `tt950ee3b30b6835d201`（已写入 `douyin-miniapp-auth/project.config.json`，提交 `b6975bb`） |
| 小程序工程 | 目录 **`douyin-miniapp-auth/`**：首页 + `tt.showDouyinOpenAuth`（`ma.video.bind`）+ 可选 `ticket` POST 到 `AUTH_BACKEND_URL` |
| 小程序备案 | **审核中**（上线/体验版以控制台为准） |
| 服务端 OpenAPI 优先抓取 | **`douyin_openapi.py`** + **`server/app/scheduler.py`** 先 OpenAPI，再 Playwright/HTTP |
| 对接说明 | **`docs/DOUYIN_OPENAPI_INTEGRATION.md`** |
| 环境变量示例 | **`server/deploy/douyin.env.example`**（含 `DOUYIN_USE_OPENAPI` 等） |
| 腾讯云仓库 | **`/home/ubuntu/jiankong`**，已与 `origin/main` 对齐（当时为 `b6975bb`） |
| 腾讯云 Git | **`~/.ssh/config`** 已配 `github.com` → `IdentityFile ~/.ssh/id_ed25519_github`，`git pull` 无需 `GIT_SSH_COMMAND` |
| Playwright/ Cookie 排查 | 曾出现验证码中间页、`DOUYIN_COOKIE` 为空；见 **`HANDOFF_DOUYIN_PLAYWRIGHT_COOKIE.md`**（若仍存在） |

---

## 明天建议优先顺序

1. **后端换票接口（关键缺口）**  
   - HTTPS 接收小程序 POST 的 **`ticket`**，用 **`client_key` + `client_secret`** 调开放平台 **`/oauth/access_token/`**（`ticket` 作文档中的 **`code`**），得到 **`act.xxx`、`open_id`、`refresh_token`**，安全存储并供 **`jiankong-api`** 使用。  
   - 可在仓库 **`server/app`** 增加路由（环境变量读密钥，不写死）。  
   - 小程序 **`pages/index/index.js`** 里配置 **`AUTH_BACKEND_URL`**，控制台配置 **request 合法域名**。

2. **控制台**  
   - **`ma.video.bind`（视频数据查询）** 等能力审核通过情况。  
   - 服务器域名、隐私协议等按提示补齐。

3. **备案通过后（或体验版可用时）**  
   - 真机授权联调 → 确认 **`ticket` 到后端** → 写入 **`/etc/jiankong/douyin.env`**：`DOUYIN_USE_OPENAPI=1`、`DOUYIN_OPENAPI_USER_ACCESS_TOKEN`、`DOUYIN_OPENAPI_OPEN_ID`、`DOUYIN_OPENAPI_ITEM_MAP_JSON` → **`systemctl restart jiankong-api`**。

4. **ITEM 映射**  
   - 每个监控 **`video_url`**（规范化后）→ 开放平台 **`open item_id`**（`@...`），填入 **`DOUYIN_OPENAPI_ITEM_MAP_JSON`**。

---

## 关键路径速查

- 小程序示例：`douyin-miniapp-auth/README.md`  
- OpenAPI 集成：`docs/DOUYIN_OPENAPI_INTEGRATION.md`  
- 开放平台文档（视频数据）：  
  https://developer.open-douyin.com/docs/resource/zh-CN/mini-app/develop/server/basic-abilities/video-id-convert/user-video-data/video-data  
- 授权：`tt.showDouyinOpenAuth`  
  https://developer.open-douyin.com/docs/resource/zh-CN/mini-app/develop/api/open-interface/authorization/tt-show-douyin-open-auth/

---

今天辛苦了；明天从 **「换票 HTTPS 接口 + 小程序 AUTH_BACKEND_URL」** 接着做即可。
