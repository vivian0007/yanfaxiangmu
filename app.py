# -*- coding: utf-8 -*-
"""
研发项目绩效自评系统 —— Flask 后端
=========================================================
- 数据库：SQLite（文件保存在本服务器 data/vivian.db），所有电脑访问同一份数据。
- 密码：使用 werkzeug.security 哈希存储，绝不存明文。
- 登录态：Flask session（Cookie，由 nginx 以 HTTPS 提供服务）。
- 无任何短信验证码逻辑。
- 首次启动自动建表，并创建默认管理员：手机号 admin / 密码 admin123。

【月度进度规则】
- 每个项目按 (年, 月) 记录进度，可查看 1~12 月历史。
- 可填报月份 = 当前月份的上一个月（例：今天 2026-09-10 → 只能填报 2026 年 8 月）。
  其他月份只读，接口层也会拒绝写入。
- 绩效按"月"分别统计；管理员可按 人员 + 年 + 月 查看当月绩效，并查看全年 12 个月逐月评分。
"""

import os
import sqlite3
import json
from datetime import date, datetime
from functools import wraps
from io import BytesIO

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from flask import Flask, g, jsonify, render_template, request, send_file, session
from werkzeug.security import check_password_hash, generate_password_hash

# ------------------------------------------------------------------
# 基本路径与常量
# ------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("VIVIAN_DATA_DIR") or os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "vivian.db")

# 管理员账号 / 初始密码：均可用环境变量覆盖（生产环境请务必设置）
ADMIN_PHONE = os.environ.get("VIVIAN_ADMIN_PHONE", "admin")
ADMIN_PASSWORD = os.environ.get("VIVIAN_ADMIN_PASSWORD", "admin123")
ADMIN_NAME = os.environ.get("VIVIAN_ADMIN_NAME", "系统管理员")
INITIAL_PASSWORD = os.environ.get("VIVIAN_INITIAL_PASSWORD", "123456")  # 新建用户的初始密码

# ---- 枚举取值（项目生命周期 / 优先级 / 任务状态）----
PROJECT_STATUS = ("planning", "active", "paused", "done")   # 未开始/进行中/暂停/已完成
TASK_STATUS = ("todo", "doing", "done", "blocked")          # 待办/进行中/已完成/受阻
PRIORITIES = ("low", "normal", "high", "urgent")            # 低/普通/高/紧急

app = Flask(__name__)
# 生产环境请通过环境变量 VIVIAN_SECRET 设置一个随机密钥
app.secret_key = os.environ.get("VIVIAN_SECRET") or "vivian-dev-secret-please-change"
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # 部署在 HTTPS 后面时置为 1（systemd 里已设置）
    SESSION_COOKIE_SECURE=os.environ.get("VIVIAN_COOKIE_SECURE", "0") == "1",
)
# 让 jsonify 直接输出中文而不是 \uXXXX
try:
    app.json.ensure_ascii = False
except Exception:
    pass


# ------------------------------------------------------------------
# 数据库：建表脚本 + 连接管理
# ------------------------------------------------------------------
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  name                 TEXT    NOT NULL UNIQUE,          -- 姓名，唯一
  phone                TEXT    NOT NULL UNIQUE,          -- 手机号，登录账号
  password_hash        TEXT    NOT NULL,                 -- 密码哈希
  department           TEXT    NOT NULL DEFAULT '',      -- 部门
  department2          TEXT NOT NULL DEFAULT '',
  department3          TEXT NOT NULL DEFAULT '',
  manager_level        INTEGER NOT NULL DEFAULT 1,
  position             TEXT    NOT NULL DEFAULT '',      -- 职位
  is_admin             INTEGER NOT NULL DEFAULT 0,       -- 是否管理员
  is_manager           INTEGER NOT NULL DEFAULT 0,       -- 是否部门负责人（可看本部门项目与绩效）
  must_change_password INTEGER NOT NULL DEFAULT 0,       -- 是否强制改密
  created_at           TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS projects (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  shared_scope TEXT NOT NULL DEFAULT '',
  project_code  TEXT    NOT NULL,
  project_name  TEXT    NOT NULL,
  category      TEXT    NOT NULL CHECK (category IN ('market','self')),
  start_date    TEXT    NOT NULL,
  delivery_date TEXT    NOT NULL,
  tasks         TEXT    NOT NULL DEFAULT '',              -- 项目任务概述（填写栏）
  status        TEXT    NOT NULL DEFAULT 'active',        -- 项目状态：planning/active/paused/done
  priority      TEXT    NOT NULL DEFAULT 'normal',        -- 优先级：low/normal/high/urgent
  progress      INTEGER NOT NULL DEFAULT 0,              -- 兼容字段：保存最新月份进度
  created_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
  updated_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_projects_user_id ON projects(user_id);

-- 月度进度表：每个项目每个月一条记录
CREATE TABLE IF NOT EXISTS project_progress (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  year         INTEGER NOT NULL,                            -- 年份，如 2026
  month        INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),  -- 月份 1~12
  progress     INTEGER NOT NULL CHECK (progress BETWEEN 0 AND 100),
  done_items   TEXT    NOT NULL DEFAULT '',                 -- 本期完成事项
  undone_items TEXT    NOT NULL DEFAULT '',                 -- 未完成事项
  updated_at   TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
  UNIQUE (project_id, year, month)
);

CREATE INDEX IF NOT EXISTS idx_progress_project ON project_progress(project_id);
CREATE INDEX IF NOT EXISTS idx_progress_ym ON project_progress(year, month);

-- 任务表：项目下的具体任务（可指派负责人、设状态/优先级/截止日期）
CREATE TABLE IF NOT EXISTS tasks (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  title       TEXT    NOT NULL,
  detail      TEXT    NOT NULL DEFAULT '',
  assignee_id INTEGER REFERENCES users(id) ON DELETE SET NULL,  -- 任务负责人
  status      TEXT    NOT NULL DEFAULT 'todo',                  -- todo/doing/done/blocked
  priority    TEXT    NOT NULL DEFAULT 'normal',                -- low/normal/high/urgent
  due_date    TEXT    NOT NULL DEFAULT '',
  progress    INTEGER NOT NULL DEFAULT 0,                       -- 任务进度 0~100
  created_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
  updated_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee_id);

-- 里程碑表
CREATE TABLE IF NOT EXISTS milestones (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  name       TEXT    NOT NULL,
  due_date   TEXT    NOT NULL DEFAULT '',
  status     TEXT    NOT NULL DEFAULT 'pending',   -- pending/done
  done_at    TEXT    NOT NULL DEFAULT '',
  created_at TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_ms_project ON milestones(project_id);

-- 系统设置表（键值对）：目前用于存放绩效核算权重
CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_monthly_reports (
 task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 year INTEGER NOT NULL, month INTEGER NOT NULL,
 category TEXT NOT NULL, progress INTEGER NOT NULL CHECK(progress BETWEEN 0 AND 100),
 done_items TEXT NOT NULL DEFAULT '', undone_items TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(task_id,year,month)
);
"""


def get_db():
    """取得当前请求的数据库连接（每个请求一个）。"""
    if "_db" not in g:
        g._db = sqlite3.connect(DB_PATH)
        g._db.row_factory = sqlite3.Row
        g._db.execute("PRAGMA foreign_keys = ON")  # 开启外键级联删除
    return g._db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("_db", None)
    if db is not None:
        db.close()


def allowed_period():
    """
    可填报月份 = 当前月份的上一个月。
    例：今天是 2026-09-10 → 返回 (2026, 8)；今天是 2026-01-05 → 返回 (2025, 12)。
    """
    t = date.today()
    if t.month == 1:
        return t.year - 1, 12
    return t.year, t.month - 1


def period_label(year, month):
    return "%d年%d月" % (year, month)


def migrate_legacy_progress(con):
    """
    兼容老数据：把 projects.progress（单一进度）迁移成一条月度进度记录。
    归属月份取该项目 updated_at 的年月；已存在月度记录的项目会跳过。可重复执行。
    """
    rows = con.execute("SELECT id, progress, updated_at FROM projects").fetchall()
    for r in rows:
        exists = con.execute(
            "SELECT 1 FROM project_progress WHERE project_id = ? LIMIT 1", (r["id"],)
        ).fetchone()
        if exists:
            continue
        try:
            dt = datetime.strptime((r["updated_at"] or "")[:10], "%Y-%m-%d")
            y, m = dt.year, dt.month
        except Exception:
            t = date.today()
            y, m = t.year, t.month
        con.execute(
            "INSERT OR IGNORE INTO project_progress (project_id, year, month, progress) VALUES (?,?,?,?)",
            (r["id"], y, m, r["progress"] or 0),
        )


def migrate_add_columns(con):
    """给已存在的旧表补上后加的列（SQLite 不支持 IF NOT EXISTS ADD COLUMN）。
    注意：gunicorn 多 worker 会并发执行本函数，必须容忍"列已被另一个进程加上"，
    否则会抛 duplicate column name 导致服务启动失败（已修复）。"""
    def has_col(table, col):
        return any(r["name"] == col for r in con.execute("PRAGMA table_info(%s)" % table).fetchall())

    def add_col(table, col, ddl):
        if has_col(table, col):
            return
        try:
            con.execute("ALTER TABLE %s ADD COLUMN %s" % (table, ddl))
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise

    add_col("project_progress", "done_items", "done_items TEXT NOT NULL DEFAULT ''")
    add_col("project_progress", "undone_items", "undone_items TEXT NOT NULL DEFAULT ''")
    add_col("users", "department2", "department2 TEXT NOT NULL DEFAULT ''")
    add_col("users", "department3", "department3 TEXT NOT NULL DEFAULT ''")
    add_col("users", "manager_level", "manager_level INTEGER NOT NULL DEFAULT 1")
    add_col("users", "is_manager", "is_manager INTEGER NOT NULL DEFAULT 0")
    add_col("projects", "shared_scope", "shared_scope TEXT NOT NULL DEFAULT ''")
    add_col("projects", "tasks", "tasks TEXT NOT NULL DEFAULT ''")
    add_col("projects", "status", "status TEXT NOT NULL DEFAULT 'active'")
    add_col("projects", "priority", "priority TEXT NOT NULL DEFAULT 'normal'")


DEFAULT_SETTINGS = {
    "market_weight": "0.3",   # 市场项目绩效权重（30%）
    "self_weight": "0.3",     # 自研项目绩效权重（30%）
}


def load_weights(db, user_id=None):
    if user_id is not None:
        row = db.execute("SELECT market_weight,self_weight FROM person_weights WHERE user_id=?", (user_id,)).fetchone()
        if row is not None:
            return row["market_weight"], row["self_weight"]
    """读取绩效权重，返回小数形式的 (市场权重, 自研权重)，默认 0.30 / 0.30。"""
    d = {}
    try:
        for r in db.execute("SELECT key, value FROM settings").fetchall():
            d[r["key"]] = r["value"]
    except sqlite3.OperationalError:
        pass

    def pick(key):
        try:
            v = float(d.get(key, DEFAULT_SETTINGS[key]))
            return v if 0 <= v <= 1 else float(DEFAULT_SETTINGS[key])
        except (TypeError, ValueError):
            return float(DEFAULT_SETTINGS[key])

    return pick("market_weight"), pick("self_weight")


SHARE_SCHEMA = 'CREATE TABLE IF NOT EXISTS project_shares (user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE, year INTEGER NOT NULL, month INTEGER NOT NULL CHECK(month BETWEEN 1 AND 12), units INTEGER NOT NULL CHECK(units BETWEEN 0 AND 10000), PRIMARY KEY(user_id,project_id,year,month));'

def init_db():
    """首次启动：建表 + 补列 + 老数据迁移 + 创建默认管理员（幂等，可重复调用）。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        migrate_progress = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_progress'").fetchone() is None
        con.executescript(SCHEMA_SQL)
        con.execute('CREATE TABLE IF NOT EXISTS person_weights (user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, market_weight REAL NOT NULL CHECK(market_weight BETWEEN 0 AND 1), self_weight REAL NOT NULL CHECK(self_weight BETWEEN 0 AND 1));')
        con.execute("PRAGMA foreign_keys = ON")
        migrate_add_columns(con)
        if migrate_progress:
            migrate_legacy_progress(con)
        shares_new = con.execute("SELECT 1 FROM sqlite_master WHERE name='project_shares'").fetchone() is None
        con.execute(SHARE_SCHEMA)
        if shares_new:
            participants = con.execute("SELECT user_id,project_id,category,year,month FROM (SELECT p.user_id,p.id AS project_id,p.category,r.year,r.month FROM projects p JOIN project_progress r ON r.project_id=p.id WHERE p.shared_scope='' UNION SELECT r.user_id,r.project_id,r.category,r.year,r.month FROM task_monthly_reports r) ORDER BY user_id,category,year,month,project_id").fetchall()
            groups = {}
            for person in participants:
                groups.setdefault((person['user_id'],person['category'],person['year'],person['month']),[]).append(person['project_id'])
            for (uid,category,year,month), ids in groups.items():
                for index,pid in enumerate(ids):
                    con.execute('INSERT INTO project_shares VALUES(?,?,?,?,?)',(uid,pid,year,month,10000//len(ids)+(1 if index<10000%len(ids) else 0)))
        for k, v in DEFAULT_SETTINGS.items():          # 默认绩效权重
            con.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
        row = con.execute("SELECT id FROM users WHERE phone = ?", (ADMIN_PHONE,)).fetchone()
        if row is None:
            con.execute(
                "INSERT INTO users (name, phone, password_hash, is_admin, must_change_password) "
                "VALUES (?, ?, ?, 1, 0)",
                (ADMIN_NAME, ADMIN_PHONE, generate_password_hash(ADMIN_PASSWORD)),
            )
        con.commit()
    except sqlite3.IntegrityError:
        # 多进程同时初始化时可能撞车，忽略即可
        con.rollback()
    finally:
        con.close()


# ------------------------------------------------------------------
# 通用工具
# ------------------------------------------------------------------
def organization_path(person):
    """Read existing R&D hierarchy labels without rewriting saved personnel records."""
    root = (person['department'] or '').strip()
    second, third = person['department2'] or '', person['department3'] or ''
    if not second and not third and root.startswith('研发部-'):
        parts = root.split('-')
        if 2 <= len(parts) <= 3 and all(parts):
            return parts[0], parts[1], parts[2] if len(parts) == 3 else ''
    return root, second, third


def row_to_user(row):
    """把 users 行转成可安全返回给前端的字典（不含密码哈希）。"""
    if row is None:
        return None
    root, second, third = organization_path(row)
    return {
        "id": row["id"],
        "name": row["name"],
        "phone": row["phone"],
        "department": root,
        "department2": second,
        "department3": third,
        "manager_level": row["manager_level"],
        "position": row["position"] or "",
        "is_admin": bool(row["is_admin"]),
        "is_manager": bool(row["is_manager"]) if "is_manager" in row.keys() else False,
        "must_change_password": bool(row["must_change_password"]),
        "created_at": row["created_at"],
    }


def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    return get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            return jsonify(ok=False, error="未登录或登录已过期，请重新登录"), 401
        return fn(*args, **kwargs)

    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        u = current_user()
        if u is None:
            return jsonify(ok=False, error="未登录或登录已过期，请重新登录"), 401
        if not u["is_admin"]:
            return jsonify(ok=False, error="需要管理员权限"), 403
        return fn(*args, **kwargs)

    return wrapper


def fail(msg, code=400):
    return jsonify(ok=False, error=msg), code


def manager_required(fn):
    """部门负责人（或管理员）可访问：只能看自己部门的数据。"""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        u = current_user()
        if u is None:
            return jsonify(ok=False, error="未登录或登录已过期，请重新登录"), 401
        if not (u["is_admin"] or u["is_manager"]):
            return jsonify(ok=False, error="需要部门负责人或管理员权限"), 403
        return fn(*args, **kwargs)

    return wrapper


def my_dept():
    """当前用户所负责的部门 = 其个人资料里的「部门」。"""
    u = current_user()
    return (u["department"] or "").strip() if u else ""


def org_values(data, previous=None):
    if previous is not None:
        root, second, third = organization_path(previous)
        previous = {**dict(previous), "department": root, "department2": second, "department3": third}
    else:
        previous = {}
    values = {}
    for key in ('department', 'department2', 'department3'):
        value = data.get(key, previous.get(key, ''))
        if not isinstance(value, str):
            raise ValueError('部门名称必须为文本')
        values[key] = value.strip()
    raw = data.get('manager_level', previous.get('manager_level', 1))
    if isinstance(raw, bool) or str(raw) not in ('1', '2', '3'):
        raise ValueError('负责人管理层级必须为一级、二级或三级')
    values['manager_level'] = int(raw)
    if values['department3'] and not values['department2']:
        raise ValueError('请先填写二级部门')
    if values['department2'] and not values['department']:
        raise ValueError('请先填写一级部门')
    manager = data.get('is_manager', previous.get('is_manager', False))
    if manager and (not values['department'] or
                    (values['manager_level'] >= 2 and not values['department2']) or
                    (values['manager_level'] == 3 and not values['department3'])):
        raise ValueError('请完整填写负责人管理层级对应的部门')
    return values


def department_people(filtered=True):
    """Apply authorization before optional filters; never broaden via query parameters."""
    u = current_user()
    rows = get_db().execute('SELECT * FROM users WHERE is_admin = 0 ORDER BY name').fetchall()
    rows = [{**dict(r), 'department': organization_path(r)[0], 'department2': organization_path(r)[1], 'department3': organization_path(r)[2]} for r in rows]
    u = {**dict(u), 'department': organization_path(u)[0], 'department2': organization_path(u)[1], 'department3': organization_path(u)[2]}
    dept = u['department']
    level = u['manager_level'] if u['is_manager'] else 1
    rows = [r for r in rows if dept and r['department'].strip() == dept]
    if level >= 2:
        rows = [r for r in rows if u['department2'] and r['department2'] == u['department2']]
    if level >= 3:
        rows = [r for r in rows if u['department3'] and r['department3'] == u['department3']]
    if filtered:
        for key in ('department2', 'department3'):
            value = request.args.get(key, '').strip()
            if value:
                rows = [r for r in rows if r[key] == value]
        if request.args.get('scope') == 'direct':
            def directly_managed(person):
                if person['id'] == u['id']:
                    return False
                if person['is_manager']:
                    return person['manager_level'] == level + 1
                child_field = {1: 'department2', 2: 'department3'}.get(level)
                return child_field is None or not person[child_field]
            rows = [r for r in rows if directly_managed(r)]
    return rows


@app.get('/api/dept/filters')
@manager_required
def api_dept_filters():
    rows = department_people(False)
    selected = request.args.get('department2', '').strip()
    third_rows = [r for r in rows if not selected or r['department2'] == selected]
    return jsonify(ok=True, departments2=sorted({r['department2'] for r in rows if r['department2']}),
                   departments3=sorted({r['department3'] for r in third_rows if r['department3']}))


def calc_performance(rows, market_w=0.30, self_w=0.30):
    """
    绩效核算核心算法（rows 需含 category 与 progress 两个字段）：
      市场项目平均完成率 = 市场项目进度之和 / 市场项目数量   （无项目时该类别得分按满分，平均完成率仍显示 0%）
      自研项目平均完成率 = 自研项目进度之和 / 自研项目数量   （无项目时该类别得分按满分，平均完成率仍显示 0%）
      市场项目绩效得分   = Σ(本月项目占比 × 完成率) × 市场权重（默认 30%，可在「系统设置」中修改）
      自研项目绩效得分   = Σ(本月项目占比 × 完成率) × 自研权重（默认 30%，可在「系统设置」中修改）
      项目绩效总分       = 两者相加（默认满分 60 分 = 30 + 30）
    """
    rows = rows or []
    market = [p for p in rows if p["category"] == "market"]
    selfp = [p for p in rows if p["category"] == "self"]
    total = len(rows)

    market_avg = (sum(p["progress"] for p in market) / len(market)) if market else 0.0
    self_avg = (sum(p["progress"] for p in selfp) / len(selfp)) if selfp else 0.0
    market_score = sum(p['progress']*(p['share'] if 'share' in p.keys() else 1/len(market)) for p in market)*market_w if market else 100*market_w
    self_score = sum(p['progress']*(p['share'] if 'share' in p.keys() else 1/len(selfp)) for p in selfp)*self_w if selfp else 100*self_w
    overall_avg = (sum(p["progress"] for p in rows) / total) if total else 0.0

    return {
        "total": total,
        "market_weighted_progress": round(market_score/market_w,2) if market_w else 0,
        "self_weighted_progress": round(self_score/self_w,2) if self_w else 0,
        "market_count": len(market),
        "self_count": len(selfp),
        "market_avg": round(market_avg, 2),
        "self_avg": round(self_avg, 2),
        "overall_avg": round(overall_avg, 2),
        "market_score": round(market_score, 2),
        "self_score": round(self_score, 2),
        "total_score": round(market_score + self_score, 2),
        "market_weight": round(market_w * 100, 2),     # 当前市场权重（%）
        "self_weight": round(self_w * 100, 2),         # 当前自研权重（%）
        "full_score": round((market_w + self_w) * 100, 2),  # 当前满分
    }


def monthly_performance(db, user_id, year, month):
    """
    统计某研发人员在指定年月的绩效。
    只统计"该月有填报进度"的项目（看板会同时显示该项目数，便于核对）。
    """
    rows = db.execute(
        "SELECT p.id AS project_id,p.category AS category, pp.progress AS progress "
        "FROM projects p JOIN project_progress pp ON pp.project_id = p.id "
        "WHERE p.user_id = ? AND p.shared_scope = '' AND pp.year = ? AND pp.month = ?",
        (user_id, year, month),
    ).fetchall()
    rows=list(rows)+[dict(r) for r in db.execute('SELECT project_id,category, AVG(progress) AS progress FROM task_monthly_reports WHERE user_id=? AND year=? AND month=? GROUP BY project_id,category',(user_id,year,month)).fetchall()]
    allocations={x['project_id']:x['units']/10000 for x in db.execute('SELECT project_id,units FROM project_shares WHERE user_id=? AND year=? AND month=?',(user_id,year,month)).fetchall()}
    rows=[dict(x,share=allocations.get(x['project_id'],0)) for x in map(dict,rows)]
    market_w, self_w = load_weights(db, user_id)
    return calc_performance(rows, market_w, self_w)


def year_monthly_performance(db, user_id, year):
    """某研发人员某一年 1~12 月的逐月绩效。"""
    return {m: monthly_performance(db, user_id, year, m) for m in range(1, 13)}


def fetch_progress_map(db, project_ids):
    """批量取月度进度：{project_id: {"YYYY-MM": progress}}，避免 N+1 查询。"""
    if not project_ids:
        return {}
    placeholders = ",".join("?" * len(project_ids))
    rows = db.execute(
        "SELECT project_id, year, month, progress FROM project_progress WHERE project_id IN (%s)" % placeholders,
        list(project_ids),
    ).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["project_id"], {})["%04d-%02d" % (r["year"], r["month"])] = r["progress"]
    return out


def fetch_month_rows(db, project_ids, year, month):
    """取指定年月各项目的整条月度记录（含完成事项 / 未完成事项）。"""
    if not project_ids:
        return {}
    ph = ",".join("?" * len(project_ids))
    rows = db.execute(
        "SELECT * FROM project_progress WHERE year = ? AND month = ? AND project_id IN (%s)" % ph,
        [year, month] + list(project_ids),
    ).fetchall()
    return {r["project_id"]: r for r in rows}


def fetch_task_stats(db, project_ids):
    """批量取各项目的任务统计：{project_id: (总数, 已完成数)}"""
    if not project_ids:
        return {}
    ph = ",".join("?" * len(project_ids))
    rows = db.execute(
        "SELECT project_id, COUNT(*) AS total, "
        "SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS done "
        "FROM tasks WHERE project_id IN (%s) GROUP BY project_id" % ph,
        list(project_ids),
    ).fetchall()
    return {r["project_id"]: (r["total"], r["done"] or 0) for r in rows}


def task_to_dict(row):
    project=get_db().execute('SELECT * FROM projects WHERE id=?',(row['project_id'],)).fetchone()
    user=current_user()
    manage=project_can_manage(project)
    own=row['assignee_id']==user['id']
    can_edit=manage or (own and shared_project_visible(project,user))
    year,month=allowed_period()
    monthly=get_db().execute('SELECT * FROM task_monthly_reports WHERE task_id=? AND year=? AND month=?',(row['id'],year,month)).fetchone()
    return {
        "project_category": project["category"],
        "can_edit": can_edit,
        "can_manage": manage,
        "can_edit_details": manage or (own and bool(project['shared_scope']) and can_edit),
        "shared": bool(project['shared_scope']),
        "monthly": dict(monthly) if monthly and (manage or own) else None,
        "id": row["id"],
        "project_id": row["project_id"],
        "title": row["title"],
        "detail": row["detail"] or "",
        "assignee_id": row["assignee_id"],
        "assignee_name": (row["assignee_name"] if "assignee_name" in row.keys() else None),
        "status": row["status"],
        "priority": row["priority"],
        "due_date": row["due_date"] or "",
        "progress": row["progress"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def milestone_to_dict(row):
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "name": row["name"],
        "due_date": row["due_date"] or "",
        "status": row["status"],
        "done_at": row["done_at"] or "",
    }


def project_to_dict(row, progress_map=None, month_row=None, task_stats=None):
    pm = progress_map.get(row["id"], {}) if progress_map else {}
    # 最新月份的进度（按 YYYY-MM 字符串排序即时间序）
    current = None
    if pm:
        latest_key = sorted(pm.keys())[-1]
        current = pm[latest_key]
    # 本期（可填报月份）的整条记录：进度 + 完成事项 + 未完成事项
    this_month = None
    if month_row is not None:
        this_month = {
            "year": month_row["year"],
            "month": month_row["month"],
            "progress": month_row["progress"],
            "done_items": month_row["done_items"] or "",
            "undone_items": month_row["undone_items"] or "",
        }
    total_tasks, done_tasks = (task_stats or {}).get(row["id"], (0, 0))
    sy,sm=allowed_period()
    share=get_db().execute('SELECT units FROM project_shares WHERE user_id=? AND project_id=? AND year=? AND month=?',(current_user()['id'],row['id'],sy,sm)).fetchone()
    return {
        'my_project_share': share[0]/100 if share else 0,
        "id": row["id"],
        "user_id": row["user_id"],
        "shared": bool(row["shared_scope"]),
        "shared_department": " / ".join(json.loads(row["shared_scope"])) if row["shared_scope"] else "",
        "can_manage": project_can_manage(row),
        "project_code": row["project_code"],
        "project_name": row["project_name"],
        "category": row["category"],
        "start_date": row["start_date"],
        "delivery_date": row["delivery_date"],
        "tasks": (row["tasks"] or "") if "tasks" in row.keys() else "",
        "status": (row["status"] if "status" in row.keys() else "active") or "active",
        "priority": (row["priority"] if "priority" in row.keys() else "normal") or "normal",
        "task_total": total_tasks,
        "task_done": done_tasks,
        "progress": row["progress"],          # 兼容字段（最新月份进度）
        "current_progress": current,          # 最新月份进度（无月度记录时为 null）
        "progress_map": pm,                   # 各月进度 {"2026-08": 80, ...}
        "this_month": this_month,             # 可填报月份的进度与事项
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "owner_name": row["owner_name"] if "owner_name" in row.keys() else None,
    }


# ------------------------------------------------------------------
# 页面
# ------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


# ------------------------------------------------------------------
# 认证
# ------------------------------------------------------------------
@app.post("/api/register")
def api_register():
    """注册：姓名 + 手机号 + 密码（无需验证码）。"""
    d = request.get_json(silent=True) or {}
    name = (d.get("name") or "").strip()
    phone = (d.get("phone") or "").strip()
    password = d.get("password") or ""
    if not name:
        return fail("请填写姓名")
    if not phone:
        return fail("请填写手机号")
    if len(password) < 6:
        return fail("密码至少 6 位")

    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE name = ?", (name,)).fetchone():
        return fail("该姓名已存在，不能重复注册")
    if db.execute("SELECT 1 FROM users WHERE phone = ?", (phone,)).fetchone():
        return fail("该手机号已注册")

    db.execute(
        "INSERT INTO users (name, phone, password_hash, is_admin, must_change_password) VALUES (?,?,?,0,0)",
        (name, phone, generate_password_hash(password)),
    )
    db.commit()
    return jsonify(ok=True, message="注册成功，请登录")


@app.post("/api/login")
def api_login():
    """登录：手机号 + 密码。"""
    d = request.get_json(silent=True) or {}
    phone = (d.get("phone") or "").strip()
    password = d.get("password") or ""
    if not phone or not password:
        return fail("请输入手机号和密码")

    row = get_db().execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
    if row is None or not check_password_hash(row["password_hash"], password):
        return fail("手机号或密码错误", 401)

    session.clear()
    session["uid"] = row["id"]
    return jsonify(ok=True, user=row_to_user(row))


@app.post("/api/logout")
def api_logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
@login_required
def api_me():
    return jsonify(ok=True, user=row_to_user(current_user()))


@app.get("/api/period")
@login_required
def api_period():
    """返回当前允许填报的年月（供前端显示）。"""
    y, m = allowed_period()
    return jsonify(ok=True, year=y, month=m, label=period_label(y, m))


@app.get("/api/pending")
@login_required
def api_pending():
    """
    未填报提醒：
      - 研发人员：返回自己"可填报月份"尚未填报的项目清单；
      - 管理员：返回"可填报月份"完全未填报的研发人员清单。
    """
    db = get_db()
    u = current_user()
    ay, am = allowed_period()
    label = period_label(ay, am)

    if u["is_admin"]:
        unfilled = []
        for r in db.execute("SELECT id, name, department FROM users WHERE is_admin = 0 ORDER BY name").fetchall():
            filled = db.execute(
                "SELECT COUNT(*) c FROM project_progress pp JOIN projects p ON p.id = pp.project_id "
                "WHERE p.user_id = ? AND pp.year = ? AND pp.month = ?",
                (r["id"], ay, am),
            ).fetchone()["c"]
            if filled == 0:
                total = db.execute("SELECT COUNT(*) c FROM projects WHERE user_id = ?", (r["id"],)).fetchone()["c"]
                unfilled.append({"id": r["id"], "name": r["name"],
                                 "department": r["department"] or "", "project_count": total})
        return jsonify(ok=True, year=ay, month=am, label=label, admin=True, unfilled_users=unfilled)

    pending = []
    for r in db.execute("SELECT id, project_code, project_name FROM projects WHERE user_id = ? ORDER BY id",
                        (u["id"],)).fetchall():
        has = db.execute(
            "SELECT 1 FROM project_progress WHERE project_id = ? AND year = ? AND month = ?",
            (r["id"], ay, am),
        ).fetchone()
        if not has:
            pending.append({"id": r["id"], "project_code": r["project_code"], "project_name": r["project_name"]})
    return jsonify(ok=True, year=ay, month=am, label=label, admin=False, pending=pending)


@app.post("/api/change-password")
@login_required
def api_change_password():
    """
    修改密码：
      - 首次登录被强制改密时（must_change_password=1）：无需提供当前密码；
      - 平时自己主动改密：必须提供 old_password 校验身份，防止会话被盗后直接改密。
    """
    d = request.get_json(silent=True) or {}
    new_pw = d.get("new_password") or ""
    old_pw = d.get("old_password") or ""
    if len(new_pw) < 6:
        return fail("新密码至少 6 位")

    me = current_user()
    forced = bool(me["must_change_password"])   # 是否处于"首次强制改密"状态
    if not forced:
        if not old_pw:
            return fail("请输入当前密码")
        if not check_password_hash(me["password_hash"], old_pw):
            return fail("当前密码不正确")

    db = get_db()
    db.execute(
        "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
        (generate_password_hash(new_pw), me["id"]),
    )
    db.commit()
    return jsonify(ok=True, message="密码已更新")


@app.post("/api/profile")
@login_required
def api_save_profile():
    """完善个人信息：部门、职位（必填）。"""
    d = request.get_json(silent=True) or {}
    dept = (d.get("department") or "").strip()
    pos = (d.get("position") or "").strip()
    if not dept:
        return fail("请填写部门")
    if not pos:
        return fail("请填写职位")
    db = get_db()
    me = current_user()
    try:
        # Profile editing cannot grant administrator/manager roles.
        org = org_values({k: d[k] for k in ('department','department2','department3') if k in d}, me)
    except ValueError as exc:
        return fail(str(exc))
    db.execute("UPDATE users SET department=?, department2=?, department3=?, position=? WHERE id=?",
               (org['department'], org['department2'], org['department3'], pos, me['id']))
    db.commit()
    return jsonify(ok=True, message="个人信息已保存", user=row_to_user(current_user()))



# ------------------------------------------------------------------
# 项目（研发人员：仅自己的；管理员：全部）
# ------------------------------------------------------------------
def validate_project_meta(d):
    """校验项目基本信息（不含进度，进度按月单独填报）。"""
    code = (d.get("project_code") or "").strip()
    name = (d.get("project_name") or "").strip()
    category = d.get("category")
    start = (d.get("start_date") or "").strip()
    delivery = (d.get("delivery_date") or "").strip()

    if not code:
        return None, "请填写项目编码"
    if not name:
        return None, "请填写项目名称"
    if category not in ("market", "self"):
        return None, "项目类别不正确"
    if not start:
        return None, "请选择项目起始时间"
    if not delivery:
        return None, "请选择项目交付时间"
    if not valid_optional_date(start) or not valid_optional_date(delivery):
        return None, "项目日期不正确"
    if delivery < start:
        return None, "交付日期不能早于开始日期"
    tasks = (d.get("tasks") or "").strip()[:2000]      # 项目任务概述（选填，最多 2000 字）
    status = d.get("status") or "active"
    if status not in PROJECT_STATUS:
        status = "active"
    priority = d.get("priority") or "normal"
    if priority not in PRIORITIES:
        priority = "normal"
    return {"project_code": code, "project_name": name, "category": category,
            "start_date": start, "delivery_date": delivery, "tasks": tasks,
            "status": status, "priority": priority}, None


def shared_project_visible(project, user=None):
    user = user if user is not None else current_user()
    if user['is_admin'] or project['user_id'] == user['id']:
        return True
    if not user['is_manager']:
        return False
    owner=get_db().execute('SELECT * FROM users WHERE id=?',(project['user_id'],)).fetchone()
    if owner is None or owner['is_admin']:
        return False
    level=user['manager_level']
    scope=list(organization_path(user))[:level]
    return all(scope) and list(organization_path(owner))[:level]==scope


def project_can_manage(project):
    user = current_user()
    return bool(user['is_admin'] or project['user_id'] == user['id'])


def _get_visible_project(pid):
    row = get_db().execute('SELECT * FROM projects WHERE id=?', (pid,)).fetchone()
    if row is None:
        return None, fail('项目不存在',404)
    if not shared_project_visible(row):
        return None, fail('无权查看该项目',403)
    return row, None


def _get_project_or_403(pid):
    """取项目并校验权限：管理员可操作任意项目，普通用户仅可操作自己的。"""
    u = current_user()
    row = get_db().execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
    if row is None:
        return None, fail("项目不存在", 404)
    if not u["is_admin"] and row["user_id"] != u["id"]:
        return None, fail("无权操作他人的项目", 403)
    return row, None



def share_units(value):
    from decimal import Decimal, InvalidOperation
    try:
        v=Decimal(str(value))
        if not v.is_finite() or v<0 or v>100 or v*100 != (v*100).to_integral_value(): raise ValueError()
        return int(v*100)
    except (InvalidOperation,ValueError,TypeError):
        raise ValueError('项目占比需为 0～100 的数字，最多两位小数')

def save_project_share(db,uid,pid,category,value):
    year,month=allowed_period()
    units=share_units(value)
    used=db.execute('SELECT COALESCE(SUM(s.units),0) FROM project_shares s JOIN projects p ON p.id=s.project_id WHERE s.user_id=? AND p.category=? AND s.project_id!=? AND s.year=? AND s.month=?',(uid,category,pid,year,month)).fetchone()[0]
    if used+units>10000:
        raise ValueError(('市场' if category=='market' else '自研')+f'项目占比合计不能超过100%；该项目最多可设置 {(10000-used)/100:g}%')
    db.execute('INSERT INTO project_shares VALUES(?,?,?,?,?) ON CONFLICT(user_id,project_id,year,month) DO UPDATE SET units=excluded.units',(uid,pid,year,month,units))

@app.get('/api/projects/<int:pid>/share')
@login_required
def api_get_share(pid):
    project,err=_get_visible_project(pid)
    if err:return err
    db=get_db();uid=current_user()['id'];year,month=allowed_period()
    own=db.execute('SELECT units FROM project_shares WHERE user_id=? AND project_id=? AND year=? AND month=?',(uid,pid,year,month)).fetchone()
    used=db.execute('SELECT COALESCE(SUM(s.units),0) FROM project_shares s JOIN projects p ON p.id=s.project_id WHERE s.user_id=? AND p.category=? AND s.project_id!=? AND s.year=? AND s.month=?',(uid,project['category'],pid,year,month)).fetchone()[0]
    return jsonify(ok=True,year=year,month=month,share=(own[0]/100 if own else 0),available=(10000-used)/100)

@app.put('/api/projects/<int:pid>/share')
@login_required
def api_put_share(pid):
    db=get_db();db.execute('BEGIN IMMEDIATE')
    project,err=_get_visible_project(pid)
    if err:return err
    uid=current_user()['id']
    if not project['shared_scope'] and project['user_id']!=uid:return fail('只能设置自己的项目占比',403)
    data=request.get_json(silent=True) or {}
    year,month=allowed_period()
    if ('year' in data and data['year']!=year) or ('month' in data and data['month']!=month):return fail('只能设置当前可填报月份的项目占比，请刷新页面')
    try:save_project_share(db,uid,pid,project['category'],data.get('share'))
    except ValueError as exc:return fail(str(exc))
    db.commit();return jsonify(ok=True)

@app.get("/api/projects")
@login_required
def api_list_projects():
    """研发人员只看自己的项目；管理员看全部（附带归属人姓名与各月进度）。"""
    db = get_db()
    u = current_user()
    rows = db.execute("SELECT p.*, us.name AS owner_name FROM projects p JOIN users us ON us.id=p.user_id ORDER BY p.created_at DESC,p.id DESC").fetchall()
    rows = [r for r in rows if shared_project_visible(r,u)]
    pm = fetch_progress_map(db, [r["id"] for r in rows])
    ts = fetch_task_stats(db, [r["id"] for r in rows])
    ay, am = allowed_period()
    mrows = fetch_month_rows(db, [r["id"] for r in rows], ay, am)
    return jsonify(
        ok=True,
        projects=[project_to_dict(r, pm, mrows.get(r["id"]), ts) for r in rows],
        allowed={"year": ay, "month": am, "label": period_label(ay, am)},
    )


@app.post("/api/projects")
@login_required
def api_create_project():
    """
    新建项目。可选地同时填报"可填报月份"的进度（progress 字段）。
    """
    d = request.get_json(silent=True) or {}
    payload, err = validate_project_meta(d)
    if err:
        return fail(err)

    ay, am = allowed_period()          # 可填报月份
    progress = d.get("progress")
    has_progress = progress is not None and str(progress).strip() != ""
    if has_progress:
        try:
            progress = int(progress)
        except (TypeError, ValueError):
            return fail("项目进度需为 0~100 的数字")
        if progress < 0 or progress > 100:
            return fail("项目进度需为 0~100 的数字")
    done_items = (d.get("done_items") or "").strip()[:2000]
    undone_items = (d.get("undone_items") or "").strip()[:2000]

    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    owner_id = current_user()["id"]
    if current_user()["is_admin"] and d.get("user_id"):
        try:
            owner_id = int(d["user_id"])
        except (TypeError, ValueError):
            return fail("请选择有效的项目负责人")
        if not db.execute("SELECT 1 FROM users WHERE id=?", (owner_id,)).fetchone():
            return fail("项目负责人不存在")
    publisher = current_user()
    shared = d.get('shared', bool(publisher['is_manager']))
    if shared not in (True,False):
        return fail('共享选项不正确')
    scope = ''
    if shared:
        if not (publisher['is_admin'] or publisher['is_manager']):
            return fail('仅部门负责人或管理员可发布部门项目',403)
        owner = db.execute('SELECT * FROM users WHERE id=?',(owner_id,)).fetchone()
        scope_user = owner if publisher['is_admin'] else publisher
        depth = scope_user['manager_level'] if scope_user['is_manager'] else 1
        parts = list(organization_path(scope_user))[:depth]
        if not all(parts):
            return fail('请先完善项目负责人的部门层级')
        scope = json.dumps(parts,ensure_ascii=False)
    cur = db.execute(
        "INSERT INTO projects (user_id, project_code, project_name, category, start_date, delivery_date, "
        "tasks, status, priority, progress) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (owner_id, payload["project_code"], payload["project_name"], payload["category"],
         payload["start_date"], payload["delivery_date"], payload["tasks"],
         payload["status"], payload["priority"], progress if has_progress else 0),
    )
    new_id = cur.lastrowid
    db.execute("UPDATE projects SET shared_scope=? WHERE id=?",(scope,new_id))
    try:save_project_share(db,owner_id,new_id,payload['category'],d.get('project_share',0))
    except ValueError as exc:
        db.rollback()
        return fail(str(exc))
    if has_progress or done_items or undone_items:
        db.execute(
            "INSERT OR REPLACE INTO project_progress "
            "(project_id, year, month, progress, done_items, undone_items, updated_at) "
            "VALUES (?,?,?,?,?,?,datetime('now','localtime'))",
            (new_id, ay, am, progress if has_progress else 0, done_items, undone_items),
        )
    db.commit()
    return jsonify(ok=True, id=new_id, message="项目添加成功")


@app.put("/api/projects/<int:pid>")
@login_required
def api_update_project(pid):
    """修改项目基本信息（进度不在这里改，必须走月度进度接口）。"""
    row, err = _get_project_or_403(pid)
    if err:
        return err
    payload, verr = validate_project_meta(dict(dict(row), **(request.get_json(silent=True) or {})))
    if verr:
        return fail(verr)
    db = get_db()
    db.execute('BEGIN IMMEDIATE')
    if row['category']!=payload['category'] and db.execute('SELECT 1 FROM project_shares WHERE project_id=? AND units>0',(pid,)).fetchone():
        return fail('项目已有月份占比，不能更改项目类别；请新建正确类别的项目')
    data=request.get_json(silent=True) or {}
    if 'project_share' in data:
        try:save_project_share(db,row['user_id'],pid,payload['category'],data['project_share'])
        except ValueError as exc:
            db.rollback();return fail(str(exc))
    db.execute(
        "UPDATE projects SET project_code=?, project_name=?, category=?, start_date=?, delivery_date=?, "
        "tasks=?, status=?, priority=?, updated_at=datetime('now','localtime') WHERE id=?",
        (payload["project_code"], payload["project_name"], payload["category"], payload["start_date"],
         payload["delivery_date"], payload["tasks"], payload["status"], payload["priority"], pid),
    )
    db.commit()
    return jsonify(ok=True, message="项目已更新")


@app.put("/api/projects/<int:pid>/progress")
@login_required
def api_set_monthly_progress(pid):
    """
    填报【本期（可填报月份）】的进度 + 完成事项 + 未完成事项。
    仅允许填报"当前月份的上一个月"；其他月份一律拒绝（不能跨月修改）。
    可只改进度、只改事项，或一起改。
    """
    row, err = _get_project_or_403(pid)
    if err:
        return err

    d = request.get_json(silent=True) or {}
    ay, am = allowed_period()                    # 唯一可填报月份
    try:
        year = int(d.get("year"))
        month = int(d.get("month"))
    except (TypeError, ValueError):
        return fail("月份参数不正确")

    if year != ay or month != am:
        return fail("每月只能填报上一个月（%s）的内容，%s 不可修改" % (period_label(ay, am), period_label(year, month)))

    db = get_db()
    existing = db.execute(
        "SELECT * FROM project_progress WHERE project_id = ? AND year = ? AND month = ?",
        (pid, ay, am),
    ).fetchone()

    # 进度：未传或留空则沿用原值；原值也没有则按 0
    raw_progress = d.get("progress")
    if raw_progress is None or str(raw_progress).strip() == "":
        progress = existing["progress"] if existing else 0
    else:
        try:
            progress = int(raw_progress)
        except (TypeError, ValueError):
            return fail("进度需为 0~100 的数字")
        if progress < 0 or progress > 100:
            return fail("进度需为 0~100 的数字")

    # 完成事项 / 未完成事项：未传则沿用原值（传空字符串表示清空），最多 2000 字
    done_items = d.get("done_items")
    undone_items = d.get("undone_items")
    if done_items is None:
        done_items = existing["done_items"] if existing else ""
    if undone_items is None:
        undone_items = existing["undone_items"] if existing else ""
    done_items = (done_items or "").strip()[:2000]
    undone_items = (undone_items or "").strip()[:2000]

    db.execute(
        "INSERT INTO project_progress (project_id, year, month, progress, done_items, undone_items, updated_at) "
        "VALUES (?,?,?,?,?,?,datetime('now','localtime')) "
        "ON CONFLICT(project_id, year, month) DO UPDATE SET "
        "progress=excluded.progress, done_items=excluded.done_items, "
        "undone_items=excluded.undone_items, updated_at=excluded.updated_at",
        (pid, ay, am, progress, done_items, undone_items),
    )
    # 同步兼容字段为"最新月份进度"
    latest = db.execute(
        "SELECT progress FROM project_progress WHERE project_id = ? ORDER BY year DESC, month DESC LIMIT 1",
        (pid,),
    ).fetchone()
    db.execute("UPDATE projects SET progress=?, updated_at=datetime('now','localtime') WHERE id=?",
               (latest["progress"] if latest else progress, pid))
    db.commit()
    return jsonify(ok=True, message="%s 的进度与事项已保存" % period_label(ay, am))


@app.delete("/api/projects/<int:pid>")
@login_required
def api_delete_project(pid):
    row, err = _get_project_or_403(pid)
    if err:
        return err
    db = get_db()
    if db.execute('SELECT 1 FROM task_monthly_reports WHERE project_id=?',(pid,)).fetchone():
        return fail('项目已有个人月度绩效记录，请保留项目并设为已完成')
    db.execute("DELETE FROM projects WHERE id = ?", (pid,))
    db.commit()
    return jsonify(ok=True, message="项目已删除")


# ------------------------------------------------------------------
# 用户管理（仅管理员）
# ------------------------------------------------------------------
@app.get("/api/users")
@admin_required
def api_list_users():
    rows = get_db().execute("SELECT * FROM users ORDER BY is_admin DESC, created_at ASC, id ASC").fetchall()
    return jsonify(ok=True, users=[row_to_user(r) for r in rows])


@app.post("/api/users")
@admin_required
def api_create_user():
    """管理员新增用户：默认初始密码 123456，首次登录强制改密。"""
    d = request.get_json(silent=True) or {}
    name = (d.get("name") or "").strip()
    phone = (d.get("phone") or "").strip()
    dept = (d.get("department") or "").strip()
    pos = (d.get("position") or "").strip()
    is_admin = 1 if d.get("is_admin") else 0
    is_manager = 1 if d.get("is_manager") else 0
    try:
        org = org_values(d)
    except ValueError as exc:
        return fail(str(exc))
    pw = (d.get("password") or "").strip() or INITIAL_PASSWORD

    if not name:
        return fail("请填写姓名")
    if not phone:
        return fail("请填写手机号")

    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE name = ?", (name,)).fetchone():
        return fail("该姓名已存在")
    if db.execute("SELECT 1 FROM users WHERE phone = ?", (phone,)).fetchone():
        return fail("该手机号已存在")

    db.execute(
        "INSERT INTO users (name, phone, password_hash, department, position, is_admin, is_manager, department2, department3, manager_level, must_change_password) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,1)",
        (name, phone, generate_password_hash(pw), dept, pos, is_admin, is_manager, org["department2"], org["department3"], org["manager_level"]),
    )
    db.commit()
    return jsonify(ok=True, message="用户已添加，初始密码为 %s，首次登录需改密" % pw)


@app.put("/api/users/<int:uid>")
@admin_required
def api_update_user(uid):
    """管理员修改用户信息（含重置/设置密码）。"""
    d = request.get_json(silent=True) or {}
    name = (d.get("name") or "").strip()
    phone = (d.get("phone") or "").strip()
    dept = (d.get("department") or "").strip()
    pos = (d.get("position") or "").strip()
    is_admin = 1 if d.get("is_admin") else 0
    is_manager = 1 if d.get("is_manager") else 0
    new_pw = (d.get("password") or "").strip()
    if new_pw and not 6 <= len(new_pw) <= 128:
        return fail("新密码需为6～128位")

    if not name:
        return fail("请填写姓名")
    if not phone:
        return fail("请填写手机号")

    db = get_db()
    target = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if target is None:
        return fail("用户不存在", 404)

    try:
        org = org_values(d, target)
    except ValueError as exc:
        return fail(str(exc))

    if db.execute("SELECT 1 FROM users WHERE name = ? AND id <> ?", (name, uid)).fetchone():
        return fail("该姓名已被其他用户占用")
    if db.execute("SELECT 1 FROM users WHERE phone = ? AND id <> ?", (phone, uid)).fetchone():
        return fail("该手机号已被其他用户占用")

    if target["is_admin"] and not is_admin:
        admins = db.execute("SELECT COUNT(*) c FROM users WHERE is_admin = 1").fetchone()["c"]
        if admins <= 1:
            return fail("系统至少需要保留一个管理员，无法取消该用户的管理员权限")

    db.execute(
        "UPDATE users SET name=?, phone=?, department=?, position=?, is_admin=?, is_manager=?, department2=?, department3=?, manager_level=? WHERE id=?",
        (name, phone, dept, pos, is_admin, is_manager, org["department2"], org["department3"], org["manager_level"], uid),
    )
    if new_pw:
        db.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 1 WHERE id = ?",
            (generate_password_hash(new_pw), uid),
        )
    db.commit()
    return jsonify(ok=True, message="用户信息已更新")


@app.post('/api/users/<int:uid>/reset-password')
@admin_required
def api_admin_reset_password(uid):
    d=request.get_json(silent=True) or {}
    password=d.get('password')
    if not isinstance(password,str) or len(password)<6 or len(password)>128:
        return fail('新密码需为6～128位')
    db=get_db()
    if db.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone() is None:
        return fail('用户不存在',404)
    db.execute('UPDATE users SET password_hash=?,must_change_password=1 WHERE id=?',(generate_password_hash(password),uid))
    db.commit()
    return jsonify(ok=True,message='密码已重置，请通知用户使用新密码登录，登录后需设置自己的密码')


@app.delete("/api/users/<int:uid>")
@admin_required
def api_delete_user(uid):
    """管理员删除用户（其名下项目、月度进度由外键级联一并删除）。"""
    db = get_db()
    target = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if target is None:
        return fail("用户不存在", 404)
    if target["phone"] == ADMIN_PHONE:
        return fail("内置管理员账号不可删除")
    if target["id"] == current_user()["id"]:
        return fail("不能删除当前登录的自己")

    db.execute("DELETE FROM users WHERE id = ?", (uid,))
    db.commit()
    return jsonify(ok=True, message="已删除该用户及其名下所有项目")


# ------------------------------------------------------------------
# 绩效看板（仅管理员）
# ------------------------------------------------------------------
def _resolve_year_month():
    """从 query 取 year/month，缺省用"可填报月份"。"""
    ay, am = allowed_period()
    year = request.args.get("year", type=int) or ay
    month = request.args.get("month", type=int) or am
    if month < 1 or month > 12:
        month = am
    return year, month


@app.get("/api/performance")
@admin_required
def api_performance_list():
    """
    管理员看板：返回研发人员在指定年份的逐月绩效。
    查询参数：?year=2026  ?department=电子电气部（可选，按部门过滤）
    """
    db = get_db()
    year = request.args.get("year", type=int) or allowed_period()[0]
    dept = (request.args.get("department") or "").strip()
    users = db.execute("SELECT * FROM users WHERE is_admin = 0 ORDER BY name ASC").fetchall()
    if dept:
        users = [u for u in users if (u["department"] or "").strip() == dept]
    out = []
    for u in users:
        months = year_monthly_performance(db, u["id"], year)
        year_total = round(sum(m["total_score"] for m in months.values()), 2)
        out.append({
            "user": row_to_user(u),
            "year": year,
            "months": months,                 # {"1": {...}, ..., "12": {...}}
            "year_total": year_total,         # 全年累计得分
        })
    # 顺带返回全部部门清单（供前端下拉）
    all_depts = sorted({(r["department"] or "").strip() for r in
                        db.execute("SELECT department FROM users WHERE is_admin = 0").fetchall()} - {""})
    return jsonify(ok=True, year=year, list=out, departments=all_depts)


@app.get("/api/performance/departments")
@admin_required
def api_performance_departments():
    """
    部门维度统计（管理员）：指定年月的部门绩效汇总。
    平均分 = 该部门"当月有填报"人员的当月总分之和 ÷ 当月有填报人数。
    """
    db = get_db()
    year = request.args.get("year", type=int) or allowed_period()[0]
    month = request.args.get("month", type=int) or allowed_period()[1]
    if month < 1 or month > 12:
        month = allowed_period()[1]

    groups = {}
    for u in db.execute("SELECT * FROM users WHERE is_admin = 0").fetchall():
        dept = (u["department"] or "").strip() or "（未填写部门）"
        g = groups.setdefault(dept, {"department": dept, "people": 0, "filled_people": 0,
                                     "sum_total": 0.0, "sum_market": 0.0, "sum_self": 0.0})
        g["people"] += 1
        r = monthly_performance(db, u["id"], year, month)
        if r["total"] > 0:               # 当月有填报的人员才计入平均
            g["filled_people"] += 1
            g["sum_total"] += r["total_score"]
            g["sum_market"] += r["market_score"]
            g["sum_self"] += r["self_score"]

    out = []
    for dept in sorted(groups):
        g = groups[dept]
        n = g["filled_people"]
        out.append({
            "department": dept,
            "people": g["people"],
            "filled_people": n,
            "avg_total_score": round(g["sum_total"] / n, 2) if n else 0,
            "avg_market_score": round(g["sum_market"] / n, 2) if n else 0,
            "avg_self_score": round(g["sum_self"] / n, 2) if n else 0,
            "sum_total_score": round(g["sum_total"], 2),
        })
    return jsonify(ok=True, year=year, month=month, label=period_label(year, month), list=out)


@app.get("/api/performance/<int:uid>")
@admin_required
def api_performance_one(uid):
    """某一位研发人员的逐月绩效（可指定年份）。"""
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if user is None:
        return fail("用户不存在", 404)
    year = request.args.get("year", type=int) or allowed_period()[0]
    months = year_monthly_performance(db, uid, year)
    return jsonify(ok=True, user=row_to_user(user), year=year, months=months,
                   year_total=round(sum(m["total_score"] for m in months.values()), 2))


# ------------------------------------------------------------------
# 绩效 Excel 导出（管理员 / 部门负责人 共用同一套生成逻辑）
# ------------------------------------------------------------------
HEADER_FILL = PatternFill("solid", fgColor="1B2743")
HEADER_FONT = Font(color="FFFFFF", bold=True)


def _style_sheet(ws, widths):
    """表头加粗上色、冻结首行、设置列宽。"""
    for idx, w in enumerate(widths, start=1):
        c = ws.cell(row=1, column=idx)
        c.fill = HEADER_FILL
        c.font = HEADER_FONT
        c.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[get_column_letter(idx)].width = w
    ws.freeze_panes = "A2"


def build_performance_workbook(db, users, year, month):
    """
    生成绩效 Excel 的二进制内容（管理员与部门负责人共用）。
    包含 4 个工作表：
      ① 月度绩效汇总（指定年月，逐人）
      ② 全年逐月绩效（每人 × 12 个月）
      ③ 项目月度进度（每人 × 项目 × 月份，含完成/未完成事项）
      ④ 部门月度绩效（每个部门 × 12 个月）
    """
    wb = openpyxl.Workbook()

    # ---- 工作表 1：月度绩效汇总（指定年月） ----
    ws = wb.active
    ws.title = "月度绩效汇总"
    ws.append(["统计月份", period_label(year, month), "", "", "", "", "", "", "", "", "", ""])
    ws.append(["姓名", "手机号", "部门", "职位", "参与项目数",
               "市场项目数", "市场平均完成率(%)", "市场绩效得分",
               "自研项目数", "自研平均完成率(%)", "自研绩效得分", "项目绩效总分(按个人权重)"])
    for u in users:
        r = monthly_performance(db, u["id"], year, month)
        ws.append([u["name"], u["phone"], u["department"] or "", u["position"] or "",
                   r["total"], r["market_count"], r["market_avg"], r["market_score"],
                   r["self_count"], r["self_avg"], r["self_score"], r["total_score"]])
    # 表头在第 2 行，单独美化
    for idx in range(1, 13):
        c = ws.cell(row=2, column=idx)
        c.fill = HEADER_FILL
        c.font = HEADER_FONT
        c.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[get_column_letter(idx)].width = 18
    ws.freeze_panes = "A3"

    # ---- 工作表 2：全年逐月绩效 ----
    ws2 = wb.create_sheet("全年逐月绩效")
    ws2.append(["姓名", "月份", "参与项目数", "市场项目数", "市场平均完成率(%)", "市场绩效得分",
                "自研项目数", "自研平均完成率(%)", "自研绩效得分", "当月绩效总分(满分60)", "全年累计得分"])
    for u in users:
        months = year_monthly_performance(db, u["id"], year)
        year_total = round(sum(m["total_score"] for m in months.values()), 2)
        for m in range(1, 13):
            r = months[m]
            ws2.append([u["name"], "%d年%d月" % (year, m), r["total"], r["market_count"], r["market_avg"],
                        r["market_score"], r["self_count"], r["self_avg"], r["self_score"],
                        r["total_score"], year_total if m == 12 else ""])
    _style_sheet(ws2, [12, 12, 12, 12, 18, 14, 12, 18, 14, 20, 14])

    # ---- 工作表 3：项目月度进度明细 ----
    ws3 = wb.create_sheet("项目月度进度")
    ws3.append(["归属人", "项目编码", "项目名称", "项目类别", "项目任务", "年", "月", "当月进度(%)",
                "本期完成事项", "未完成事项", "填报时间"])
    for u in users:
        rows = db.execute(
            "SELECT pp.year, pp.month, pp.progress, pp.done_items, pp.undone_items, pp.updated_at, "
            "p.project_code, p.project_name, p.category, p.tasks "
            "FROM project_progress pp JOIN projects p ON p.id = pp.project_id "
            "WHERE p.user_id = ? ORDER BY p.project_code, pp.year, pp.month",
            (u["id"],),
        ).fetchall()
        for r in rows:
            ws3.append([u["name"], r["project_code"], r["project_name"],
                        "市场项目" if r["category"] == "market" else "自研项目",
                        r["tasks"] or "",
                        r["year"], r["month"], r["progress"],
                        r["done_items"] or "", r["undone_items"] or "", r["updated_at"]])
    _style_sheet(ws3, [12, 16, 30, 12, 40, 8, 8, 14, 38, 38, 20])
    for row in ws3.iter_rows(min_row=2, min_col=5, max_col=5):     # 项目任务换行
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    for row in ws3.iter_rows(min_row=2, min_col=9, max_col=10):    # 事项换行
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")

    # ---- 工作表：项目任务清单 ----
    ws5 = wb.create_sheet("项目任务清单")
    ws5.append(["归属人", "项目编码", "项目名称", "项目类别", "项目任务", "起始时间", "交付时间"])
    for u in users:
        for p in db.execute(
            "SELECT * FROM projects WHERE user_id = ? ORDER BY project_code", (u["id"],)
        ).fetchall():
            ws5.append([u["name"], p["project_code"], p["project_name"],
                        "市场项目" if p["category"] == "market" else "自研项目",
                        p["tasks"] or "", p["start_date"], p["delivery_date"]])
    _style_sheet(ws5, [12, 16, 30, 12, 52, 14, 14])
    for row in ws5.iter_rows(min_row=2, min_col=5, max_col=5):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")

    # ---- 工作表 4：部门月度绩效 ----
    ws4 = wb.create_sheet("部门月度绩效")
    ws4.append(["部门", "月份", "部门人数", "当月已填报人数", "部门当月平均绩效分", "部门当月绩效分合计"])
    members = {}
    for u in db.execute("SELECT * FROM users WHERE is_admin = 0 ORDER BY name ASC").fetchall():
        dept = (u["department"] or "").strip() or "（未填写部门）"
        members.setdefault(dept, []).append(u)
    for dept in sorted(members):
        for m in range(1, 13):
            scores = []
            for mu in members[dept]:
                r = monthly_performance(db, mu["id"], year, m)
                if r["total"] > 0:
                    scores.append(r["total_score"])
            avg = round(sum(scores) / len(scores), 2) if scores else 0
            ws4.append([dept, "%d年%d月" % (year, m), len(members[dept]), len(scores), avg, round(sum(scores), 2)])
    _style_sheet(ws4, [18, 12, 12, 18, 22, 22])

    ws5=wb.create_sheet('个人任务月报')
    ws5.append(['姓名','项目编码','任务','年','月','进度','完成事项','未完成事项'])
    for person in users:
        for report in db.execute('SELECT r.*,p.project_code,t.title FROM task_monthly_reports r JOIN projects p ON p.id=r.project_id JOIN tasks t ON t.id=r.task_id WHERE r.user_id=? AND r.year=? ORDER BY r.month,r.project_id,r.task_id',(person['id'],year)).fetchall():
            ws5.append([person['name'],report['project_code'],report['title'],report['year'],report['month'],report['progress'],report['done_items'],report['undone_items']])
    _style_sheet(ws5,[16,20,32,10,8,12,40,40])
    shares_sheet=wb.create_sheet('项目月度占比')
    shares_sheet.append(['姓名','年','月','项目编码','项目名称','类别','项目占比(%)'])
    for person in users:
        for item in db.execute('SELECT s.*,p.project_code,p.project_name,p.category FROM project_shares s JOIN projects p ON p.id=s.project_id WHERE s.user_id=? AND s.year=? ORDER BY s.month,p.category,p.id',(person['id'],year)).fetchall():
            shares_sheet.append([person['name'],year,item['month'],item['project_code'],item['project_name'],'市场' if item['category']=='market' else '自研',item['units']/100])
    _style_sheet(shares_sheet,[16,10,8,20,32,12,18])
    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@app.get("/api/export/performance.xlsx")
@admin_required
def api_export_performance():
    """导出绩效 Excel（管理员）。可带 ?user_id=N 只导出某人；?year=2026 指定年份。"""
    db = get_db()
    uid = request.args.get("user_id", type=int)
    year = request.args.get("year", type=int) or allowed_period()[0]
    month = request.args.get("month", type=int) or allowed_period()[1]

    if uid:
        users = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchall()
    else:
        users = db.execute("SELECT * FROM users WHERE is_admin = 0 ORDER BY name ASC").fetchall()
    if not users:
        return fail("没有可导出的研发人员数据", 404)

    bio = build_performance_workbook(db, users, year, month)
    fname = ("绩效统计_%s_%d年.xlsx" % (users[0]["name"], year)) if uid else ("绩效统计_全部人员_%d年.xlsx" % year)
    return send_file(bio, mimetype=XLSX_MIME, as_attachment=True, download_name=fname)


@app.get("/api/dept/export.xlsx")
@manager_required
def api_dept_export():
    """导出【本部门】绩效 Excel（部门负责人 / 管理员）。"""
    db = get_db()
    dept = my_dept()
    if not dept:
        return fail("你还没有填写「部门」，无法导出部门绩效")
    year = request.args.get("year", type=int) or allowed_period()[0]
    month = request.args.get("month", type=int) or allowed_period()[1]
    users = department_people()
    if not users:
        return fail("本部门暂无可导出的数据", 404)

    bio = build_performance_workbook(db, users, year, month)
    return send_file(bio, mimetype=XLSX_MIME, as_attachment=True,
                     download_name="绩效统计_%s_%d年.xlsx" % (dept, year))


# ------------------------------------------------------------------
# 系统设置：绩效核算权重（仅管理员）
# ------------------------------------------------------------------

def weight_people():
    u = current_user()
    if u['is_admin']:
        return get_db().execute('SELECT * FROM users WHERE is_admin=0 ORDER BY name').fetchall()
    return [p for p in department_people(filtered=False)
            if p['id'] != u['id'] and (not p['is_manager'] or p['manager_level'] > u['manager_level'])]

@app.get('/api/person-weights')
@manager_required
def api_person_weights():
    db = get_db()
    out = []
    for p in weight_people():
        mw, sw = load_weights(db, p['id'])
        custom = db.execute('SELECT 1 FROM person_weights WHERE user_id=?',(p['id'],)).fetchone() is not None
        out.append(dict(id=p['id'],name=p['name'],department=' / '.join(x for x in organization_path(p) if x),market_weight=round(mw*100,2),self_weight=round(sw*100,2),custom=custom))
    return jsonify(ok=True,users=out)

@app.put('/api/person-weights/<int:uid>')
@manager_required
def api_save_person_weights(uid):
    if uid not in {p['id'] for p in weight_people()}:
        return fail('只能修改所辖部门的下级人员权重',403)
    d=request.get_json(silent=True) or {}
    db=get_db()
    if d.get('use_default') is True:
        db.execute('DELETE FROM person_weights WHERE user_id=?',(uid,))
    else:
        try:
            mw,sw=float(d['market_weight']),float(d['self_weight'])
            if not (0<=mw<=100 and 0<=sw<=100): raise ValueError()
        except (KeyError,TypeError,ValueError):
            return fail('权重需为 0~100 的数字')
        db.execute('INSERT INTO person_weights VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET market_weight=excluded.market_weight,self_weight=excluded.self_weight',(uid,round(mw/100,6),round(sw/100,6)))
    db.commit()
    return jsonify(ok=True)

@app.get("/api/settings")
@admin_required
def api_get_settings():
    db = get_db()
    mw, sw = load_weights(db)
    return jsonify(ok=True,
                   market_weight=round(mw * 100, 2),
                   self_weight=round(sw * 100, 2),
                   full_score=round((mw + sw) * 100, 2))


@app.put("/api/settings")
@admin_required
def api_put_settings():
    """修改绩效核算权重（按百分数传入，如 30 表示 30%）。"""
    d = request.get_json(silent=True) or {}
    try:
        mw = float(d.get("market_weight"))
        sw = float(d.get("self_weight"))
    except (TypeError, ValueError):
        return fail("权重需为 0~100 的数字")
    if not (0 <= mw <= 100) or not (0 <= sw <= 100):
        return fail("权重需为 0~100 的数字")

    db = get_db()
    for key, val in (("market_weight", mw), ("self_weight", sw)):
        db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(round(val / 100.0, 6))),
        )
    db.commit()
    return jsonify(ok=True, message="绩效权重已保存（市场 %g%% / 自研 %g%%）" % (mw, sw),
                   full_score=round(mw + sw, 2))


# ------------------------------------------------------------------
# 部门负责人：查看本部门的成员 / 项目 / 绩效
# ------------------------------------------------------------------
@app.get("/api/dept/members")
@manager_required
def api_dept_members():
    db = get_db()
    dept = my_dept()
    if not dept:
        return jsonify(ok=True, department="", members=[],
                       note="你还没有填写「部门」，请先在「我的个人信息」中填写后再查看部门数据。")
    rows = department_people()
    return jsonify(ok=True, department=dept, members=[row_to_user(r) for r in rows])


@app.get("/api/dept/projects")
@manager_required
def api_dept_projects():
    db = get_db()
    dept = my_dept()
    ay, am = allowed_period()
    allowed = {"year": ay, "month": am, "label": period_label(ay, am)}
    if not dept:
        return jsonify(ok=True, department="", projects=[], allowed=allowed)
    ids = [r['id'] for r in department_people()]
    rows = db.execute(
        "SELECT p.*, us.name AS owner_name FROM projects p JOIN users us ON us.id = p.user_id "
        "WHERE us.id IN (%s) ORDER BY us.name ASC, p.created_at DESC" % (','.join('?' for _ in ids) or 'NULL'),
        ids,
    ).fetchall()
    pm = fetch_progress_map(db, [r["id"] for r in rows])
    ts = fetch_task_stats(db, [r["id"] for r in rows])
    mrows = fetch_month_rows(db, [r["id"] for r in rows], ay, am)
    return jsonify(ok=True, department=dept, allowed=allowed,
                   projects=[project_to_dict(r, pm, mrows.get(r["id"]), ts) for r in rows])


@app.get("/api/dept/performance")
@manager_required
def api_dept_performance():
    db = get_db()
    dept = my_dept()
    year = request.args.get("year", type=int) or allowed_period()[0]
    mw, sw = load_weights(db)
    meta = {"year": year, "department": dept,
            "market_weight": round(mw * 100, 2), "self_weight": round(sw * 100, 2),
            "full_score": round((mw + sw) * 100, 2)}
    if not dept:
        return jsonify(ok=True, list=[], **meta)
    users = department_people()
    out = []
    for mu in users:
        months = year_monthly_performance(db, mu["id"], year)
        out.append({
            "user": row_to_user(mu),
            "year": year,
            "months": months,
            "year_total": round(sum(m["total_score"] for m in months.values()), 2),
        })
    return jsonify(ok=True, list=out, **meta)


# ------------------------------------------------------------------
# 项目管理：项目详情 / 任务 / 里程碑 / 我的任务 / 工作台
# ------------------------------------------------------------------
@app.get("/api/user-options")
@login_required
def api_user_options():
    """供「任务负责人」下拉使用：仅返回 id / 姓名 / 部门。"""
    rows = get_db().execute(
        "SELECT id, name, department FROM users ORDER BY name"
    ).fetchall()
    return jsonify(ok=True, users=[{"id": r["id"], "name": r["name"],
                                    "department": r["department"] or ""} for r in rows])


@app.get("/api/projects/<int:pid>")
@login_required
def api_project_detail(pid):
    """项目详情：基本信息 + 负责人 + 任务 + 里程碑 + 月度进度明细。"""
    db = get_db()
    row, err = _get_visible_project(pid)
    if err:
        return err
    owner = db.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone()
    pm = fetch_progress_map(db, [pid])
    ts = fetch_task_stats(db, [pid])
    ay, am = allowed_period()
    mrows = fetch_month_rows(db, [pid], ay, am)
    proj = project_to_dict(row, pm, mrows.get(pid), ts)
    proj["owner"] = row_to_user(owner)

    tasks = db.execute(
        "SELECT t.*, u.name AS assignee_name FROM tasks t "
        "LEFT JOIN users u ON u.id = t.assignee_id "
        "WHERE t.project_id = ? ORDER BY (t.status = 'done'), t.due_date, t.id", (pid,)
    ).fetchall()
    ms = db.execute(
        "SELECT * FROM milestones WHERE project_id = ? ORDER BY due_date, id", (pid,)
    ).fetchall()
    prog = db.execute(
        "SELECT year, month, progress, done_items, undone_items, updated_at "
        "FROM project_progress WHERE project_id = ? ORDER BY year, month", (pid,)
    ).fetchall()
    reports = db.execute('SELECT r.*,u.name AS user_name,t.title AS task_title FROM task_monthly_reports r JOIN users u ON u.id=r.user_id JOIN tasks t ON t.id=r.task_id WHERE r.project_id=? ORDER BY r.year DESC,r.month DESC,r.task_id',(pid,)).fetchall()
    reports = [dict(r) for r in reports if project_can_manage(row) or r['user_id']==current_user()['id']]
    return jsonify(ok=True, project=proj, task_reports=reports,
                   tasks=[task_to_dict(t) for t in tasks],
                   milestones=[milestone_to_dict(m) for m in ms],
                   progress=[dict(x) for x in prog],
                   allowed={"year": ay, "month": am, "label": period_label(ay, am)})


def valid_optional_date(value):
    if not isinstance(value, str):
        return False
    if not value:
        return True
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _task_payload(d):
    title = str(d.get("title") or "").strip()
    if not title:
        return None, "请填写任务名称"
    status = d.get("status") or "todo"
    priority = d.get("priority") or "normal"
    if status not in TASK_STATUS or priority not in PRIORITIES:
        return None, "任务状态或优先级不正确"
    try:
        raw = d.get("progress", 0)
        progress = int(raw)
        if float(raw) != progress or not 0 <= progress <= 100:
            raise ValueError()
    except (TypeError, ValueError):
        return None, "进度必须是 0 到 100 的整数"
    due = d.get("due_date") or ""
    if not valid_optional_date(due):
        return None, "截止日期不正确"
    try:
        assignee = int(d["assignee_id"]) if d.get("assignee_id") not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        return None, "任务负责人不正确"
    if assignee is not None and not get_db().execute("SELECT 1 FROM users WHERE id=?", (assignee,)).fetchone():
        return None, "任务负责人不存在"
    return {"title": title[:200], "detail": str(d.get("detail") or "").strip()[:2000],
            "assignee_id": assignee, "status": status, "priority": priority,
            "due_date": due, "progress": 100 if status == "done" else progress}, None


@app.get("/api/projects/<int:pid>/tasks")
@login_required
def api_list_tasks(pid):
    row, err = _get_visible_project(pid)
    if err:
        return err
    rows = get_db().execute(
        "SELECT t.*, u.name AS assignee_name FROM tasks t LEFT JOIN users u ON u.id = t.assignee_id "
        "WHERE t.project_id = ? ORDER BY (t.status = 'done'), t.due_date, t.id", (pid,)
    ).fetchall()
    return jsonify(ok=True, tasks=[task_to_dict(t) for t in rows])


@app.post("/api/projects/<int:pid>/tasks")
@login_required
def api_create_task(pid):
    row, err = _get_visible_project(pid)
    if err:
        return err
    payload, verr = _task_payload(request.get_json(silent=True) or {})
    if verr:
        return fail(verr)
    if not project_can_manage(row):
        return fail('只能在自己创建的项目中添加任务',403)
    if row['shared_scope'] and payload['assignee_id']:
        person = get_db().execute('SELECT * FROM users WHERE id=?',(payload['assignee_id'],)).fetchone()
        if not shared_project_visible(row,person):
            return fail('执行人不在项目部门范围内')
    db = get_db()
    db.execute(
        "INSERT INTO tasks (project_id, title, detail, assignee_id, status, priority, due_date, progress) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (pid, payload["title"], payload["detail"], payload["assignee_id"], payload["status"],
         payload["priority"], payload["due_date"], payload["progress"]),
    )
    db.commit()
    return jsonify(ok=True, message="任务已添加")


def _get_task_or_403(tid):
    db = get_db()
    t = db.execute(
        "SELECT t.*, p.user_id AS owner_id FROM tasks t JOIN projects p ON p.id = t.project_id WHERE t.id = ?",
        (tid,),
    ).fetchone()
    if t is None:
        return None, fail("任务不存在", 404)
    u = current_user()
    if not u["is_admin"] and t["owner_id"] != u["id"] and t["assignee_id"] != u["id"]:
        return None, fail("无权操作该任务", 403)
    project = db.execute('SELECT * FROM projects WHERE id=?',(t['project_id'],)).fetchone()
    if not shared_project_visible(project,u):
        return None, fail('已不在项目部门范围内',403)
    return t, None


@app.put("/api/tasks/<int:tid>")
@login_required
def api_update_task(tid):
    t, err = _get_task_or_403(tid)
    if err:
        return err
    d = request.get_json(silent=True) or {}
    u = current_user()
    project = get_db().execute('SELECT * FROM projects WHERE id=?',(t['project_id'],)).fetchone()
    if not project_can_manage(project):
        if project['shared_scope']:
            if 'assignee_id' in d and str(d['assignee_id']) != str(u['id']):
                return fail('不能转派他人的工作任务',403)
        elif any(k not in ('status','progress') for k in d):
            return fail('任务执行人仅可更新状态和进度',403)
    payload, verr = _task_payload(dict(dict(t), **d))
    if verr:
        return fail(verr)
    if project['shared_scope'] and payload['assignee_id']:
        person=get_db().execute('SELECT * FROM users WHERE id=?',(payload['assignee_id'],)).fetchone()
        if not shared_project_visible(project,person):
            return fail('执行人不在项目部门范围内')
    db = get_db()
    db.execute(
        "UPDATE tasks SET title=?, detail=?, assignee_id=?, status=?, priority=?, due_date=?, progress=?, "
        "updated_at=datetime('now','localtime') WHERE id=?",
        (payload["title"], payload["detail"], payload["assignee_id"], payload["status"],
         payload["priority"], payload["due_date"], payload["progress"], tid),
    )
    db.commit()
    return jsonify(ok=True, message="任务已更新")


@app.delete("/api/tasks/<int:tid>")
@login_required
def api_delete_task(tid):
    t, err = _get_task_or_403(tid)
    if err:
        return err
    if not current_user()["is_admin"] and t["owner_id"] != current_user()["id"]:
        return fail("仅项目负责人或管理员可删除任务", 403)
    db = get_db()
    if db.execute('SELECT 1 FROM task_monthly_reports WHERE task_id=?',(tid,)).fetchone():
        return fail('任务已有月度绩效记录，请保留任务')
    db.execute("DELETE FROM tasks WHERE id = ?", (tid,))
    db.commit()
    return jsonify(ok=True, message="任务已删除")


@app.put('/api/tasks/<int:tid>/monthly')
@login_required
def api_task_monthly(tid):
    task,err = _get_task_or_403(tid)
    if err: return err
    db=get_db()
    project=db.execute('SELECT * FROM projects WHERE id=?',(task['project_id'],)).fetchone()
    if not project['shared_scope'] or task['assignee_id'] is None:
        return fail('仅已分配执行人的共享项目任务支持个人月报')
    d=request.get_json(silent=True) or {}
    year,month=allowed_period()
    if str(d.get('year'))!=str(year) or str(d.get('month'))!=str(month):
        return fail('只能填报上一个月')
    payload,error=_task_payload(dict(dict(task),**{'progress':d.get('progress'),'status':'doing'}))
    if error:return fail(error)
    old=db.execute('SELECT user_id FROM task_monthly_reports WHERE task_id=? AND year=? AND month=?',(tid,year,month)).fetchone()
    if old and old['user_id']!=task['assignee_id']:
        return fail('本月已由原执行人填报，不能覆盖其历史记录')
    db.execute('INSERT INTO task_monthly_reports(task_id,project_id,user_id,year,month,category,progress,done_items,undone_items) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(task_id,year,month) DO UPDATE SET progress=excluded.progress,done_items=excluded.done_items,undone_items=excluded.undone_items',
       (tid,project['id'],task['assignee_id'],year,month,project['category'],payload['progress'],str(d.get('done_items') or '')[:2000],str(d.get('undone_items') or '')[:2000]))
    db.commit()
    return jsonify(ok=True)


@app.get("/api/projects/<int:pid>/milestones")
@login_required
def api_list_milestones(pid):
    row, err = _get_visible_project(pid)
    if err:
        return err
    rows = get_db().execute(
        "SELECT * FROM milestones WHERE project_id = ? ORDER BY due_date, id", (pid,)
    ).fetchall()
    return jsonify(ok=True, milestones=[milestone_to_dict(m) for m in rows])


@app.post("/api/projects/<int:pid>/milestones")
@login_required
def api_create_milestone(pid):
    row, err = _get_project_or_403(pid)
    if err:
        return err
    d = request.get_json(silent=True) or {}
    if not valid_optional_date(d.get("due_date") or ""):
        return fail("里程碑日期不正确")
    name = (d.get("name") or "").strip()
    if not name:
        return fail("请填写里程碑名称")
    db = get_db()
    db.execute("INSERT INTO milestones (project_id, name, due_date, status) VALUES (?,?,?,'pending')",
               (pid, name[:200], (d.get("due_date") or "").strip()[:10]))
    db.commit()
    return jsonify(ok=True, message="里程碑已添加")


def _get_milestone_or_403(mid):
    db = get_db()
    m = db.execute(
        "SELECT m.*, p.user_id AS owner_id FROM milestones m JOIN projects p ON p.id = m.project_id WHERE m.id = ?",
        (mid,),
    ).fetchone()
    if m is None:
        return None, fail("里程碑不存在", 404)
    u = current_user()
    if not u["is_admin"] and m["owner_id"] != u["id"]:
        return None, fail("无权操作该里程碑", 403)
    return m, None


@app.put("/api/milestones/<int:mid>")
@login_required
def api_update_milestone(mid):
    m, err = _get_milestone_or_403(mid)
    if err:
        return err
    d = request.get_json(silent=True) or {}
    if not valid_optional_date(d.get("due_date") or ""):
        return fail("里程碑日期不正确")
    name = (d.get("name") or "").strip()
    if not name:
        return fail("请填写里程碑名称")
    status = "done" if d.get("status") == "done" else "pending"
    done_at = m["done_at"] or ""
    if status == "done" and not done_at:
        done_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if status == "pending":
        done_at = ""
    db = get_db()
    db.execute("UPDATE milestones SET name=?, due_date=?, status=?, done_at=? WHERE id=?",
               (name[:200], (d.get("due_date") or "").strip()[:10], status, done_at, mid))
    db.commit()
    return jsonify(ok=True, message="里程碑已更新")


@app.delete("/api/milestones/<int:mid>")
@login_required
def api_delete_milestone(mid):
    m, err = _get_milestone_or_403(mid)
    if err:
        return err
    db = get_db()
    db.execute("DELETE FROM milestones WHERE id = ?", (mid,))
    db.commit()
    return jsonify(ok=True, message="里程碑已删除")


@app.get("/api/my-tasks")
@login_required
def api_my_tasks():
    """「我的任务」：分派给我的 + 我自己项目下的全部任务。"""
    db = get_db()
    u = current_user()
    rows = db.execute(
        "SELECT t.*, uu.name AS assignee_name, p.project_code, p.project_name, "
        "p.status AS project_status, ow.name AS owner_name "
        "FROM tasks t JOIN projects p ON p.id = t.project_id "
        "LEFT JOIN users uu ON uu.id = t.assignee_id "
        "LEFT JOIN users ow ON ow.id = p.user_id "
        "WHERE t.assignee_id = ? OR p.user_id = ? "
        "ORDER BY (t.status = 'done'), t.due_date, t.id",
        (u["id"], u["id"]),
    ).fetchall()
    out = []
    for r in rows:
        project=db.execute('SELECT * FROM projects WHERE id=?',(r['project_id'],)).fetchone()
        if not shared_project_visible(project,u):continue
        d = task_to_dict(r)
        d["project_code"] = r["project_code"]
        d["project_name"] = r["project_name"]
        d["owner_name"] = r["owner_name"]
        d["project_status"] = r["project_status"]
        d["is_mine"] = (r["assignee_id"] == u["id"])
        out.append(d)
    return jsonify(ok=True, tasks=out)


@app.get("/api/dashboard")
@login_required
def api_dashboard():
    """工作台：项目/任务统计、逾期提醒、本月填报情况。"""
    db = get_db()
    u = current_user()
    ay, am = allowed_period()

    projs = db.execute("SELECT * FROM projects WHERE user_id = ?", (u["id"],)).fetchall()
    by_status = {s: 0 for s in PROJECT_STATUS}
    filled = 0
    for p in projs:
        st = p["status"] or "active"
        by_status[st] = by_status.get(st, 0) + 1
        if db.execute("SELECT 1 FROM project_progress WHERE project_id=? AND year=? AND month=?",
                      (p["id"], ay, am)).fetchone():
            filled += 1

    tasks = db.execute(
        "SELECT t.*, p.project_code, p.project_name FROM tasks t JOIN projects p ON p.id = t.project_id "
        "WHERE p.user_id = ? OR t.assignee_id = ?",
        (u["id"], u["id"]),
    ).fetchall()
    tasks=[t for t in tasks if shared_project_visible(db.execute('SELECT * FROM projects WHERE id=?',(t['project_id'],)).fetchone(),u)]
    t_by_status = {s: 0 for s in TASK_STATUS}
    today = date.today().isoformat()
    overdue = []
    for t in tasks:
        t_by_status[t["status"]] = t_by_status.get(t["status"], 0) + 1
        if t["status"] != "done" and t["due_date"] and t["due_date"] < today:
            overdue.append({"id": t["id"], "title": t["title"], "due_date": t["due_date"],
                            "project_code": t["project_code"], "project_name": t["project_name"]})
    overdue.sort(key=lambda x: x["due_date"])

    ms = db.execute(
        "SELECT m.*, p.project_code, p.project_name FROM milestones m JOIN projects p ON p.id = m.project_id "
        "WHERE p.user_id = ? AND m.status = 'pending' ORDER BY m.due_date",
        (u["id"],),
    ).fetchall()

    return jsonify(
        ok=True,
        allowed={"year": ay, "month": am, "label": period_label(ay, am)},
        project_total=len(projs), project_by_status=by_status,
        month_filled=filled, month_pending=max(0, len(projs) - filled),
        task_total=len(tasks), task_by_status=t_by_status,
        overdue=overdue[:20],
        upcoming=[dict(milestone_to_dict(m), project_code=m["project_code"],
                       project_name=m["project_name"]) for m in ms][:20],
    )


# ------------------------------------------------------------------
# 启动
# ------------------------------------------------------------------

@app.get("/api/work/tasks")
@login_required
def api_work_tasks():
    u = current_user()
    projects={p['id']:p for p in get_db().execute('SELECT * FROM projects').fetchall()}
    rows=get_db().execute('SELECT t.*,p.user_id AS owner_id,p.project_name,p.project_code,u.name AS assignee_name FROM tasks t JOIN projects p ON p.id=t.project_id LEFT JOIN users u ON u.id=t.assignee_id ORDER BY t.id DESC').fetchall()
    rows=[r for r in rows if shared_project_visible(projects[r['project_id']],u)]
    return jsonify(ok=True, tasks=[dict(task_to_dict(r), project_name=r["project_name"],
        project_code=r["project_code"], can_manage=bool(u["is_admin"] or r["owner_id"]==u["id"])) for r in rows])


@app.get("/api/work/milestones")
@login_required
def api_work_milestones():
    u = current_user()
    projects={p['id']:p for p in get_db().execute('SELECT * FROM projects').fetchall()}
    rows=get_db().execute("SELECT m.*,p.project_name,p.project_code FROM milestones m JOIN projects p ON p.id=m.project_id ORDER BY (m.due_date=''),m.due_date,m.id").fetchall()
    rows=[r for r in rows if shared_project_visible(projects[r['project_id']],u)]
    return jsonify(ok=True, milestones=[dict(milestone_to_dict(r),project_name=r["project_name"],project_code=r["project_code"]) for r in rows])


init_db()  # 首次导入即建表 + 迁移老数据 + 创建管理员

if __name__ == "__main__":
    # 本地调试用；生产环境由 gunicorn 启动
    app.run(host="127.0.0.1", port=8000, debug=False)
