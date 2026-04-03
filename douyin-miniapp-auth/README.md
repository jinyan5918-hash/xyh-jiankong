# 抖音小程序 — 自有号开放平台授权（jiankong 配套）

用于在 **抖音 App 真机** 内拉起 **`tt.showDouyinOpenAuth`**，拿到 **`ticket`**（服务端换用户 `access_token` 时作为 **`code`** 使用，以[官方文档](https://developer.open-douyin.com/docs/resource/zh-CN/mini-app/develop/api/open-interface/authorization/tt-show-douyin-open-auth/)为准）。

## 你需要先完成的（控制台）

1. [抖音开放平台](https://developer.open-douyin.com/console) → 创建 **抖音小程序**，记下 **AppID（tt 开头）**、**App Secret**。  
2. **能力** → **用户数据能力** → **抖音账号数据** → 开通 **`ma.video.bind`（视频数据查询）** 等（与你要调用的 OpenAPI 一致）。  
3. 配置 **服务器域名**（request 合法域名填你们 HTTPS 接口域名）、**隐私协议** 等（控制台提示缺什么补什么）。  
4. 下载安装 **抖音开发者工具**，用本目录作为项目根目录打开（或把 `pages/`、`app.*` 合并进官方模板项目）。

## 真机调试注意

- **`tt.showDouyinOpenAuth` 在开发者工具模拟器里可能不可用**，必须用 **抖音 App → 扫码预览 / 体验版** 测。  
- 每次授权 **同一场景**；`ma.video.bind` 属于「抖音视频数据」场景（见官方 scope 表）。

## 与服务端对接

1. 用户点击「授权」→ `success` 里拿到 **`ticket`**。  
2. 小程序 `tt.request` 把 **`ticket`** POST 到你们后端（需 HTTPS、域名已在控制台配置）。  
3. 后端用 **`ticket` 作为 `code`**，加上 `client_key`、`client_secret` 调开放平台 **`/oauth/access_token/`**，得到 **`access_token`（act.xxx）**、**`open_id`**、**`refresh_token`**（若有），写入安全配置给 `jiankong-api` 使用。  

具体字段名以 [获取 user access_token](https://developer.open-douyin.com/docs/resource/zh-CN/mini-app/develop/server/interface-request-credential/user-authorization/get-user-access-token) 最新文档为准。

## 配置后端地址

修改 `pages/index/index.js` 顶部：

```javascript
const AUTH_BACKEND_URL = 'https://你的域名/api/douyin/open-auth/callback';
```

（该接口需由你们在 `jiankong` 或其它服务中实现，本仓库仅提供小程序侧示例。）
