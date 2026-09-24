> 从旧版本升级请先阅读 UPGRADE.md。必须同时上传新增 static/ 目录。

# 部署说明（Ubuntu / Debian + nginx + systemd）

本文档说明如何把本系统从零部署到一台 Linux 服务器，并通过 HTTPS 对外提供服务。
（本机实际部署环境：**Ubuntu 24.04 + nginx 1.24 + Python 3.12 + SQLite**）

---

## 一、环境要求

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Ubuntu 22.04 / 24.04 或同系 Debian |
| Python | 3.10+（系统自带 `python3` 即可） |
| 数据库 | SQLite（Python 内置 `sqlite3`，**无需安装**） |
| Web 服务器 | nginx（用于反向代理 + HTTPS） |
| 域名与证书 | 一个解析到本机的域名 + 对应 SSL 证书（crt / key） |

---

## 二、源码目录结构

```
vivian-src/
├── app.py                      # 后端：Flask 应用（全部 REST API + SQLite 建表/迁移）
├── templates/
│   └── index.html              # 前端：单页应用（HTML + CSS + 原生 JS + 本地 Bootstrap）
├── schema.sql                  # 数据库结构说明（运行时由 app.py 自动建表，本文件供查阅）
├── requirements.txt            # 依赖清单（apt / pip 两种安装方式）
├── README.md                   # 功能说明与使用手册
├── DEPLOY.md                   # 本文件：部署说明
└── deploy/
    ├── vivian.service          # systemd 服务模板（复制到 /etc/systemd/system/）
    └── nginx-vivian.conf       # nginx 反向代理模板（复制到 /etc/nginx/conf.d/）
```

> 运行时会在源码目录下自动创建 `data/vivian.db`（SQLite 数据库文件）。
> **该文件包含全部业务数据，请勿提交到代码仓库、也请勿随源码分发。**

---

## 三、部署步骤

### 步骤 1：上传源码

把源码放到目标目录，例如 `/home/ubuntu/vivian`：

```bash
sudo mkdir -p /home/ubuntu/vivian
# 用 scp / rsync / git 把 vivian-src 里的内容上传到该目录
sudo chown -R ubuntu:ubuntu /home/ubuntu/vivian
```

### 步骤 2：安装依赖

```bash
sudo apt-get update
sudo apt-get install -y python3-flask python3-gunicorn python3-openpyxl
```

验证：

```bash
python3 -c "import flask, openpyxl; print('ok')"
python3 -m gunicorn --version
```

> 也可用 pip：`python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt`
> 注意 Ubuntu 24.04 有 PEP 668 限制，系统级 pip 安装需要额外参数，**推荐直接用 apt**。

### 步骤 3：配置 systemd 服务

**先生成一个随机密钥**（务必替换模板里的占位值）：

```bash
python3 -c "import secrets;print(secrets.token_hex(32))"
```

复制模板并编辑：

```bash
sudo cp deploy/vivian.service /etc/systemd/system/vivian.service
sudo nano /etc/systemd/system/vivian.service
```

需要修改的内容：

| 环境变量 | 说明 |
| --- | --- |
| `VIVIAN_SECRET` | **必填**。会话签名密钥，填上面生成的随机串 |
| `VIVIAN_ADMIN_PASSWORD` | **必填**。管理员初始密码（首次启动创建 `admin` 账号时使用） |
| `VIVIAN_INITIAL_PASSWORD` | 选填，默认 `123456`。管理员新建用户时的初始密码 |
| `VIVIAN_COOKIE_SECURE` | 走 HTTPS 时填 `1`；纯 HTTP 调试填 `0` |
| `VIVIAN_ADMIN_PHONE` | 选填，默认 `admin`。管理员登录账号 |
| `VIVIAN_ADMIN_NAME` | 选填，默认 `系统管理员` |

同时把 `User` / `Group` / `WorkingDirectory` 改成你的实际值与源码路径。

### 步骤 4：配置 nginx 与证书

1. 把域名证书放到 `/etc/nginx/ssl/`（例如 `example.com_bundle.crt`、`example.com.key`）；
2. 复制模板并替换域名与证书路径：

```bash
sudo cp deploy/nginx-vivian.conf /etc/nginx/conf.d/vivian.conf
sudo nano /etc/nginx/conf.d/vivian.conf      # 把 example.com 换成你的域名
sudo nginx -t                                # 语法检查
```

### 步骤 5：启动并验证

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now vivian            # 启动并设置开机自启
sudo systemctl reload nginx

# 查看状态与日志
sudo systemctl status vivian
sudo journalctl -u vivian -f

# 本地验证（后端）
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/

# 外网验证（域名）
curl -s -o /dev/null -w "%{http_code}\n" https://你的域名/
```

首次启动时 `app.py` 会**自动建表**并创建管理员账号，无需手动执行 SQL。

---

## 四、日常运维

```bash
sudo systemctl restart vivian        # 重启后端（改完 app.py 或 templates/index.html 后执行）
sudo systemctl status vivian         # 查看状态
sudo journalctl -u vivian -f         # 实时日志
sudo nginx -t && sudo systemctl reload nginx   # 重载 nginx

# 备份数据库（重要！建议加进 crontab 每天执行）
cp /home/ubuntu/vivian/data/vivian.db ~/vivian-backup-$(date +%F).db

# 恢复数据库
sudo systemctl stop vivian
cp ~/vivian-backup-2026-01-01.db /home/ubuntu/vivian/data/vivian.db
sudo systemctl start vivian
```

---

## 五、数据库结构

首次运行自动创建以下表（详见 `schema.sql`）：

| 表 | 用途 |
| --- | --- |
| `users` | 用户（姓名、手机号、密码哈希、部门、职位、是否管理员、是否部门负责人） |
| `projects` | 项目（编码、名称、类别、起止时间、任务概述、状态、优先级） |
| `project_progress` | **月度进度**（按 项目×年×月，存进度 + 本期完成事项 + 未完成事项） |
| `tasks` | 任务（所属项目、任务名、负责人、状态、优先级、截止日期、任务进度） |
| `milestones` | 里程碑（所属项目、名称、截止日期、是否完成） |
| `settings` | 系统设置（绩效核算权重：市场 / 自研） |

> 表结构如有升级，`app.py` 里的 `migrate_add_columns()` 会在启动时**自动补列**，
> 旧数据不受影响；配合 gunicorn `--preload` 避免多进程并发迁移冲突。

---

## 六、常见问题

**Q：访问站点返回 502？**
后端没起来。看 `sudo journalctl -u vivian -n 50`，常见原因是依赖没装或端口被占用。

**Q：改了代码不生效？**
Flask 在非调试模式下会缓存模板，请 `sudo systemctl restart vivian`。

**Q：页面样式错乱？**
前端用了 Bootstrap CDN（`cdn.jsdelivr.net`）。如果服务器/客户端处于内网无法访问外网 CDN，
可以把 Bootstrap 的 CSS/JS 下载到本地，改成相对路径引用。

**Q：怎么修改监听端口？**
改 `deploy/vivian.service` 的 `ExecStart` 里的 `--bind 127.0.0.1:8000`，
同时同步修改 nginx 的 `proxy_pass`。

**Q：数据库文件在哪？会不会随源码一起发布？**
在源码目录下的 `data/vivian.db`。**打包/分发源码时务必排除 `data/` 目录**，里面有真实业务数据。
