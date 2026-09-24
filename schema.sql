
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
