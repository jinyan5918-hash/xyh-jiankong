# 企业版MVP（进行中）

当前已完成第一阶段骨架：

- `server/`：登录鉴权、设备绑定、任务管理 API
- `client/`：Windows 客户端雏形（登录 + 任务列表 + 新增/更新/删除任务）
- 调度器：随机轮询 + 失败退避 + 熔断冷却（服务端）

## 快速启动（本机联调）

### 1) 启动服务端

```bash
cd server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

默认管理员：

- 用户名：`admin`
- 密码：`Admin@123456`

### 2) 启动客户端

```bash
cd client
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python windows_client.py
```

### 3) 启动调度器（管理员）

登录拿到 token 后调用：

```bash
curl -X POST http://127.0.0.1:8000/scheduler/start \
  -H "Authorization: Bearer <ADMIN_TOKEN>"
```

### 4) 管理后台网页

服务端启动后，浏览器打开：

```text
http://127.0.0.1:8000/admin
```

可在页面里完成：

- 管理员登录
- 创建员工账号 / 重置密码 / 禁用账号 / 调整设备上限
- 设备管理（强制下线 / 恢复）
- 启停调度器并查看运行状态
- 最近检测记录查看（成功/失败/点赞）
- 检测记录筛选（按用户ID/任务ID/成功状态/时间范围）
- 统计看板（成功率、失败原因Top、按小时检测量）

查看状态：

```bash
curl http://127.0.0.1:8000/scheduler/status \
  -H "Authorization: Bearer <ADMIN_TOKEN>"
```

停止调度器：

```bash
curl -X POST http://127.0.0.1:8000/scheduler/stop \
  -H "Authorization: Bearer <ADMIN_TOKEN>"
```

## 下一步计划

1. 管理后台（账号、设备、封禁）
2. 采集结果持久化（历史曲线/告警记录）
3. Windows 安装包与自动更新
4. 集中通知通道（企业微信/邮件/系统通知）
