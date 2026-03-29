# 抖音点赞抓取：抗风控与持续迭代

目标：在抖音频繁改版、加强风控的前提下，**尽量维持监控可用**，并把「换策略」做成工程习惯，而不是一次性改死代码。

> 下列分层按「对真实浏览器/网络环境的依赖程度」排列，上层通常更耐 403，但部署与运维更重。

## 能力分层（建议同时保留多条，按环境变量切换）

| 层级 | 手段 | 作用 |
|------|------|------|
| **S** | Playwright + 真 Chromium（`DOUYIN_USE_PLAYWRIGHT=1`） | TLS/JS/渲染与真实用户最接近；实现上在**独立子进程**跑 Sync API，避免与 Uvicorn 的 asyncio 冲突 |
| **A** | 住宅/移动网出口代理（`DOUYIN_PROXY_POOL` / `DOUYIN_PLAYWRIGHT_PROXY`） | 换 ASN 与 IP 信誉，对抗机房 IP 黑名单 |
| **B** | 高活 Cookie（`DOUYIN_COOKIE`） | 复用已建立的风控会话；需定期更换 |
| **C** | UA/Referer/移动端首跳、403 自动换 UA（`douyin_fetch.py`） | 低成本基线，易被封仍值得保留作 fallback |

## 迭代流程（抖音一更新就按此走）

1. **看指标**：监控日志里 403/验证页/解析失败比例；按任务、按时间段统计。
2. **留证据**：对失败 URL 保存响应片段或截图（仅内部分析），对比是「封 IP」「要登录」「改版 JSON 结构」还是「短链失效」。
3. **改一层**：优先动「最外层」——换代理、换 Cookie、开关 Playwright；再考虑改解析正则/JSON 路径。
4. **版本化**：解析规则与策略开关尽量配置化（环境变量或将来接远程配置），避免每次改代码发版才能试。
5. **回滚**：新策略上线后观察 30～60 分钟，变差则关掉开关即回旧路径。

## 服务器部署 Playwright（Ubuntu 示例）

```bash
cd /home/ubuntu/jiankong/server
source ../.venv-enterprise/bin/activate
pip install playwright
playwright install chromium
# 云服务器「最小镜像」几乎必做：否则 Chromium 启动报缺 .so（见下方排错）
sudo playwright install-deps chromium
```

systemd 中增加：

```ini
Environment=DOUYIN_USE_PLAYWRIGHT=1
```

然后 `daemon-reload` + `restart jiankong-api`。

### 排错：`libatk-1.0.so.0` / `Target page, context or browser has been closed`

日志或 `douyin_fetch_playwright.py --pw-child` 若出现：

`error while loading shared libraries: libatk-1.0.so.0: cannot open shared object file`

说明 **系统依赖未装全**（与 Python 代码无关）。在服务器执行：

```bash
cd /home/ubuntu/jiankong/server
source ../.venv-enterprise/bin/activate
sudo playwright install-deps chromium
```

若 `install-deps` 不可用或仍报错，可手动装 ATK（Ubuntu/Debian 示例）：

```bash
sudo apt-get update
sudo apt-get install -y libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libgbm1 \
  libasound2t64 libasound2 libxcomposite1 libxdamage1 libxfixes3 libxrandr2
```

（部分发行版包名为 `libasound2` 而非 `libasound2t64`，以 `apt-cache search libasound` 为准。）

装完后再次执行：

`printf '%s' 'https://v.douyin.com/你的短链/' | python3 douyin_fetch_playwright.py --pw-child`

应返回 `{"ok": true, "likes": ...}` 或解析类错误，而不应是 `libatk` / `exitCode=127`。

## 环境变量一览（与代码对齐）

| 变量 | 用途 |
|------|------|
| `DOUYIN_USE_PLAYWRIGHT` | 非空且为 1/true/yes 时优先加载 `douyin_fetch_playwright.py` |
| `DOUYIN_COOKIE` | 浏览器复制的 Cookie，HTTP 与 Playwright 路径均可用 |
| `DOUYIN_PROXY_POOL` | 逗号分隔代理，`douyin_fetch.py` 随机选用 |
| `DOUYIN_PLAYWRIGHT_PROXY` | 单条代理 URL，仅 Playwright 启动浏览器时使用 |
| `DOUYIN_PREFER_MOBILE_UA` | 设为 0 可关闭 HTTP 路径默认移动 UA |

## 后续可增强方向（按需加 issue / PR）

- 多解析器：除正则外，解析 `__NEXT_DATA__` / 指定 `script type="application/json"`。
- 浏览器侧：持久化 `storage_state`、登录态由专人定期刷新。
- 策略编排：同一任务 HTTP 失败 N 次后自动切 Playwright 一次（冷却后再回 HTTP）。
- 与官方/开放平台能力对齐：若有合规接口可并行校验，降低纯爬依赖。
