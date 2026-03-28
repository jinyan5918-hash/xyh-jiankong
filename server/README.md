# 服务端（登录鉴权 + 设备绑定 + 任务管理）

## 启动

```bash
cd server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 默认管理员

- 账号: `admin`
- 密码: `Admin@123456`

首次启动会自动创建。上线前请立即修改密码并替换 JWT 密钥。

## 主要接口

- `POST /auth/login` 登录（含设备绑定）
- `GET /tasks` 查看当前账号任务
- `POST /tasks` 新建任务
- `PATCH /tasks/{id}` 更新任务
- `DELETE /tasks/{id}` 删除任务
- `GET /admin/users` 管理员查看账号列表
- `POST /admin/users` 管理员创建员工账号
- `PATCH /admin/users/{id}` 管理员重置密码/禁用账号

## 快速生成员工账号（脚本）

```bash
cd server
source .venv/bin/activate
python create_user.py --username staff02 --password "Staff@123456" --max-devices 2
```
