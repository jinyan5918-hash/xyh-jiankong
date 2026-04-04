# 抖音开放平台「查询特定视频的视频数据」对接说明（jiankong）

官方文档：  
[查询特定视频的视频数据](https://developer.open-douyin.com/docs/resource/zh-CN/mini-app/develop/server/basic-abilities/video-id-convert/user-video-data/video-data)

接口摘要：

- **URL**：`POST https://open.douyin.com/api/apps/v1/video/query/`
- **Scope**：`ma.video.bind`（控制台申请「视频数据查询」等能力）
- **鉴权头**：`access-token`：用户授权后的用户口令（文档示例 `act.xxx`）
- **Query**：`open_id`（授权用户唯一标识）
- **Body**：`{"item_ids": ["@……"]}`（开放平台 **open item_id**，不是浏览器地址栏里的普通数字 id）

## 必须先明确的业务边界（官方限制）

文档写明：`item_ids` **仅能查询当前 `access_token` 对应抖音用户、且为该用户上传的视频**。

因此：

- **适合**：监控**已授权、且视频属于该抖音号**的公开作品（企业自有矩阵号、已签约达人授权等）。
- **不适合**：在未获平台/权利人授权的情况下，把本接口用于**任意第三方/友商**视频；若仍要监控外链，需继续依赖现有抓取路径或寻求合规数据合作。

## 授权与 token 获取（概要）

1. 在 [抖音开放平台控制台](https://developer.open-douyin.com/console) 创建**抖音小程序**（或符合文档要求的应用类型）。
2. 申请 **用户数据能力 / 视频数据查询** 等权限。
3. 在小程序内引导用户完成 **`tt.showDouyinOpenAuth`** 授权。  
   本仓库提供最小示例工程：**`douyin-miniapp-auth/`**（见该目录 `README.md`，需用**抖音开发者工具**打开、**真机**调试）。
4. 服务端按文档用 `code` 换 **`/oauth/access_token/`** 得到 **`access-token`（act.…）** 与 **`open_id`**，并做好**刷新/续期**（过期需重新授权或 refresh，以官方文档为准）。

### jiankong-api 内置换票接口（已实现）

小程序拿到 **`ticket`** 后，向你们已部署的 **HTTPS** `jiankong-api` 上报（需把域名加入小程序 **request 合法域名**）：

- **路径**：`POST /douyin/open-auth/ticket`
- **Header**：`Content-Type: application/json`  
  若服务端设置了 **`DOUYIN_OPENAUTH_CALLBACK_SECRET`**，还须：  
  `Authorization: Bearer <与密钥相同的字符串>`
- **Body**：`{"ticket":"用户授权返回的 ticket"}`

**响应 JSON**（成功时 `ok: true`）：`access_token`、`open_id`、`expires_in`、`refresh_token` 等。请仅在 **HTTPS** 下使用，并将 `access_token` / `open_id` 写入 **`/etc/jiankong/douyin.env`** 中的 `DOUYIN_OPENAPI_USER_ACCESS_TOKEN`、`DOUYIN_OPENAPI_OPEN_ID`，然后 **`systemctl restart jiankong-api`**。

换票所需环境变量见 **`server/deploy/douyin.env.example`**：`DOUYIN_OPEN_PLATFORM_CLIENT_KEY`、`DOUYIN_OPEN_PLATFORM_CLIENT_SECRET`。

小程序示例工程 **`douyin-miniapp-auth/pages/index/index.js`** 中配置 **`AUTH_BACKEND_URL`**（完整 URL，例如 `https://你的域名/douyin/open-auth/ticket`）及可选 **`AUTH_BACKEND_BEARER_SECRET`**。

`videoid` 与开放平台 `item_id` 的转换见：  
[videoid 转换 itemid](https://developer.open-douyin.com/docs/resource/zh-CN/mini-app/develop/server/basic-abilities/video-id-convert/video-id-to-open-item-id)  
（多用于小程序侧 `videoId` 与开放能力打通；若你从分享链只有数字 `aweme_id`，需按控制台能力用官方转换链路拿到 **open item_id**，再填入下方映射。）

## jiankong 服务端配置（环境变量）

在 `jiankong-api` 使用的 `EnvironmentFile`（如 `/etc/jiankong/douyin.env`）或 `override.conf` 中增加：

| 变量 | 说明 |
|------|------|
| `DOUYIN_USE_OPENAPI=1` | 启用开放平台优先路径 |
| `DOUYIN_OPENAPI_USER_ACCESS_TOKEN` | 用户口令 `act.xxx`（注意保密与轮换） |
| `DOUYIN_OPENAPI_OPEN_ID` | 授权用户的 `open_id` |
| `DOUYIN_OPENAPI_ITEM_MAP_JSON` | 分享链接 → open `item_id` 的 JSON 对象（见下） |

`DOUYIN_OPENAPI_ITEM_MAP_JSON` 示例（键为任务里保存的 **完整分享 URL**（建议与客户端/API 规范化后一致），值为文档中的 **item_id** 字符串）：

```json
{
  "https://v.douyin.com/xxxxx/": "@8hxdhauTCMppanGnM4ltGM780mDqPP+KPpR0qQOmLVAXb/T060zdRmYqig357zEBq6CZRp4NVe6qLIJW/V/x1w=="
}
```

也支持用 **数字 aweme_id** 做键（若与 `douyin_fetch` 从 URL 解析出的 id 一致）：

```json
{
  "7622236514542551475": "@8hxdhauTCMppanGnM4ltGM780mDqPP+KPpR0qQOmLVAXb/T060zdRmYqig357zEBq6CZRp4NVe6qLIJW/V/x1w=="
}
```

配置后：`sudo systemctl daemon-reload && sudo systemctl restart jiankong-api`。

## 调度器行为

- 若 `DOUYIN_USE_OPENAPI=1` 且 token、`open_id`、映射齐全：对该任务优先调用 **`douyin_openapi.query_video_statistics`**，从返回的 `statistics` 读取 `digg_count`、`comment_count`。
- 若某链接**不在映射中**：返回 `None`，自动回退 **Playwright**（`DOUYIN_USE_PLAYWRIGHT=1`）再 **HTTP**。
- **最新一条评论** 本接口不提供，该字段在走 OpenAPI 成功时仍为 `None`；若需要评论内容需另接官方「评论列表」等能力并单独实现。

## 代码入口

- 实现：`douyin_openapi.py`（仓库根目录）
- 调度：`server/app/scheduler.py` 中 `_load_openapi_fetch_metrics_optional` 与 `_run_task` 优先调用顺序
- 换票路由：`server/app/douyin_openauth.py`（`main.py` 已 `include_router`）
