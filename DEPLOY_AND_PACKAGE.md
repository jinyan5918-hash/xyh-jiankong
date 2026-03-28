# 跨电脑访问后台 + Windows 安装包指南

## 1. 后台让其他电脑可访问

### 1) 启动服务端监听公网/局域网地址

在服务端机器执行（不是 127.0.0.1）：

```bash
cd server
source ../.venv-enterprise/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 2) 放通端口

- 局域网使用：放通本机防火墙 `8000`
- 公网使用：云服务器安全组放通 `8000`（建议后续改 443 + HTTPS）

### 3) 其他电脑访问

- 管理后台地址：`http://<服务器IP>:8000/admin`
- 员工客户端登录时，服务端填写：`http://<服务器IP>:8000`

---

## 2. 企业客户端打包给员工（Windows，推荐）

员工解压后**双击 exe 即可**，无需安装 Python。服务端地址在打包时写入 `config.json`。

**若你只有 Mac、没有 Windows**：请用仓库里的 **GitHub Actions** 在云端自动打出 zip，步骤见根目录 **[HOW_TO_GET_CLIENT_ZIP.md](HOW_TO_GET_CLIENT_ZIP.md)**。

**若有 Windows**：把项目 `jiankong` 拷到本机，在 **CMD** 中进入项目根目录执行下面步骤。

### 2.1 首次准备（只做一次）

在 **CMD** 中 `cd` 到项目根目录 `jiankong`，执行：

```bat
python -m venv .venv-client
.venv-client\Scripts\activate
pip install -r client\requirements-client.txt pyinstaller
```

### 2.2 打包（每次换服务器地址时重做）

仍在项目根目录，**激活** `.venv-client` 后双击运行 `build_enterprise_client.bat`，或在 CMD 里执行（把 IP 换成你的公网 IP；若地址含特殊字符请加引号）：

```bat
.venv-client\Scripts\activate
build_enterprise_client.bat http://你的公网IP:8000
```

产物目录：

- `dist\EnterpriseDouyinClient\`（内含 `EnterpriseDouyinClient.exe`、`config.json`、依赖文件等）

将整个 **`EnterpriseDouyinClient` 文件夹**打成 zip，发给员工；员工解压后运行 `EnterpriseDouyinClient.exe`，用你在 `/admin` 里创建的账号登录。

---

## 2b. 旧版：本机监控 GUI 打包（douyin_monitor_gui）

在 Windows 机器项目目录执行：

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt pyinstaller
build_windows_monitor.bat
```

产物路径：

- `dist\DouyinLikeMonitor\DouyinLikeMonitor.exe`

把整个 `dist\DouyinLikeMonitor` 文件夹发给员工即可运行。

### 方式B：做正式安装包（MSI）

推荐后续用 Inno Setup / WiX 把 EXE 封装为安装程序，并加：

- 公司代码签名证书
- 自动更新
- 开机自启（可选）

---

## 3. 当前登录方式

- 监控软件打开后，必须先输入管理员发放的账号密码登录，才能开始监控。
- 支持在界面填写服务端地址（方便连接公司服务器）。
