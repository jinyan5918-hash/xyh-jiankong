# 如何得到「员工解压即用」的 Windows 客户端压缩包

## 先说明三件事

1. **员工压缩包里是 Windows 的 `.exe`，只能在 Windows 上打包出来。**  
   你当前用的 Mac 无法在本机直接生成这个 exe（这是 PyInstaller 的限制，不是项目配置问题）。

2. **本项目已配置好「云端自动打包」**：代码放到 GitHub 后，由 **GitHub 在微软的 Windows 服务器上**替你打包，你只需在网页上点几下，**下载下来的就是 zip**，发给员工即可。  
   **不需要**你再找一台 Windows 电脑装 Python（除非你不想用 GitHub）。

3. **打客户端包不需要上传任何东西到腾讯云。**  
   客户端连的是你已经在跑的 API（`http://公网IP:8000`）。只有当你**改了服务端代码**时，才需要像之前那样用 `scp` 把文件同步到服务器。

---

## 用 GitHub 自动生成 zip（推荐）

### 第一步：把项目变成 Git 并推到 GitHub

**执行位置：你本机 Mac 的「终端」。**

```bash
cd /Users/admin/Documents/jiankong

git init
git add .
git commit -m "Initial commit"

# 在 github.com 新建一个空仓库（不要勾选添加 README），记下仓库地址，例如：
# https://github.com/你的用户名/jiankong.git

git remote add origin https://github.com/你的用户名/jiankong.git
git branch -M main
git push -u origin main
```

若 `git` 提示要登录，按 GitHub 网页说明配置 HTTPS 令牌或 SSH。

### 第二步：在网页上触发打包

1. 浏览器打开你的 GitHub 仓库。
2. 点 **Actions**。
3. 左侧选 **Build Enterprise Windows Client**。
4. 点 **Run workflow**。
5. 在 **api_base** 里填写你的服务端地址，例如：`http://119.45.44.95:8000`（**不要**加 `/admin`）。
6. 点绿色 **Run workflow**。

等待约 2～5 分钟，任务变绿后：

7. 点进这一次运行记录，页面下方 **Artifacts** 里的 zip **名称形如** `EnterpriseDouyinClient-v1.2.0-abc1234`（版本号 + 提交短 SHA），每次成功构建名字都不同，避免误下旧包。解压后可看 **build_info.txt** 核对 `git_sha` 是否与 GitHub 最新提交一致。
8. 点名字即可下载 **zip**。这个 zip 就是发给员工的包。

### 员工怎么用

解压 zip 后，**整个文件夹**放到任意位置，双击其中的 **EnterpriseDouyinClient-版本号.exe**（标题栏会显示 `v版本号`），用管理员在 `/admin` 里创建的账号登录即可。**不需要**安装 Python、**不需要**改配置。

---

## 若公司不允许使用 GitHub

只能在 **任意一台 Windows 电脑**上，进入项目根目录，按 `DEPLOY_AND_PACKAGE.md` 里的说明执行 `build_enterprise_client.bat`（需先装 Python 与依赖）。

---

## 常见问题

**Q：公网 IP 换了怎么办？**  
A：再在 Actions 里 **Run workflow** 一次，填新的 `api_base`，重新下载 zip 发给员工。

**Q：服务器上要上传这次新加的文件吗？**  
A：**不用。** `.github` 和说明文档只影响「如何打包」；你腾讯云上的 `uvicorn` 和已有代码不用为这个 zip 再改一遍，除非你同时改了 API 代码。
