# 项目管理系统

基于原研发项目绩效自评系统改造的 Flask + SQLite 项目管理应用。

## 功能

- **工作台**：项目状态、任务完成、逾期风险、里程碑和月度填报提醒。
- **项目中心**：创建、编辑项目；按关键字、项目编码、项目类别、状态组合筛选，支持重置；管理员建项目可指定负责人。
- **项目详情**：任务分配、优先级、截止日期、完成进度、里程碑及月度历史。
- **任务看板**：待处理 / 进行中 / 受阻 / 已完成，支持状态切换和编辑。
- **交付计划**：按日期汇总项目交付与里程碑，标记逾期。
- **月度绩效**：沿用原填报规则、权重、部门权限、全年逐月统计和 Excel 导出。
- 桌面双栏工作台，手机自适应布局和可横向滚动的任务看板。

任务完成率与月度填报进度相互独立；不会因为任务状态变化自动调整绩效。

## 本地运行

需要 Python 3.10+。在源码目录执行：

```bash
python -m venv .venv
# Windows：.venv\Scripts\activate
# Linux/macOS：source .venv/bin/activate
python -m pip install -r requirements.txt
```

启动前设置环境变量 `VIVIAN_SECRET`、`VIVIAN_ADMIN_PASSWORD`、`VIVIAN_INITIAL_PASSWORD` 为各自独立的值。
Windows PowerShell 示例（请填入你自己的值）：

```powershell
$env:VIVIAN_SECRET = "替换为随机长密钥"
$env:VIVIAN_ADMIN_PASSWORD = "替换为管理员密码"
$env:VIVIAN_INITIAL_PASSWORD = "替换为新用户初始密码"
python app.py
```

然后打开 http://127.0.0.1:8000 。初始管理员账号是 `admin`，密码为你设置的 `VIVIAN_ADMIN_PASSWORD`。
环境变量仅在创建管理员时设置其初始密码，已有账号密码不会因重启而重置。

默认数据库位置为 `data/vivian.db`；可用 `VIVIAN_DATA_DIR` 指向其他目录。
源码包不含数据库或演示账号。Bootstrap 静态资源已随包提供，无需外部 CDN。

## 回归测试

```bash
python -m unittest discover -s tests -v
```

测试使用临时数据库，不读取本地正式 data/。检查项目、任务、里程碑、权限、月度绩效、Excel 导出及重启数据完整性。

## 部署和升级

既有线上系统优先阅读 [UPGRADE.md](UPGRADE.md)，全新 Linux 部署参考 [DEPLOY.md](DEPLOY.md)。
升级时必须包含 `static/`，保留服务器 `data/`、原密钥及环境变量，不要覆盖已有业务数据。

原版功能说明归档于 [docs/ORIGINAL_README.md](docs/ORIGINAL_README.md)，其中旧界面、默认显示密码及 CDN 描述已由新版行为取代。
第三方 Bootstrap 许可位于 `static/vendor/LICENSE`；项目本身未擅自添加开源许可证。
