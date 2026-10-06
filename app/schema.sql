-- AI口播工作台 v1 数据库结构
CREATE TABLE IF NOT EXISTS sources (
  key    TEXT PRIMARY KEY,
  name   TEXT NOT NULL,
  type   TEXT NOT NULL,            -- official | media | overseas | trending
  region TEXT DEFAULT ''           -- 国内 | 海外
);

CREATE TABLE IF NOT EXISTS items (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  source_key    TEXT NOT NULL,
  source_type   TEXT NOT NULL,     -- official | media | overseas | trending
  title         TEXT NOT NULL,
  url           TEXT NOT NULL,
  summary       TEXT DEFAULT '',   -- AI 一句话中文摘要
  summary_zh    TEXT DEFAULT '',   -- 海外源原文的中文翻译摘要
  category      TEXT DEFAULT '',   -- 大模型 | AI产品 | 科技大事件
  image_url     TEXT DEFAULT '',   -- 原文配图（og:image 等，第一张）
  images        TEXT DEFAULT '[]', -- 原文配图全集（json 数组）
  raw_text      TEXT DEFAULT '',   -- 正文抽取文本
  published_at  TEXT DEFAULT '',
  fetched_at    TEXT NOT NULL,
  day           TEXT NOT NULL,     -- 抓取日 YYYY-MM-DD
  is_read       INTEGER DEFAULT 0,
  is_starred    INTEGER DEFAULT 0,
  todo_done     INTEGER DEFAULT 0,
  region        TEXT DEFAULT '',   -- 内容主体地区：国内 | 海外
  event_id      INTEGER,
  UNIQUE(source_key, url)
);
CREATE INDEX IF NOT EXISTS idx_items_day   ON items(day);
CREATE INDEX IF NOT EXISTS idx_items_event ON items(event_id);

CREATE TABLE IF NOT EXISTS hotspots (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  title            TEXT NOT NULL,
  category         TEXT DEFAULT '',
  why              TEXT DEFAULT '',
  angles           TEXT DEFAULT '[]',      -- json array
  suggested_length TEXT DEFAULT '30s',     -- 15s | 30s | 60s
  score            INTEGER DEFAULT 0,
  sources_count    INTEGER DEFAULT 1,
  status           TEXT DEFAULT 'pending', -- pending | packed | shot
  day              TEXT NOT NULL,          -- 首次出现日
  cover            TEXT DEFAULT '',
  sequel_of        INTEGER,                -- 往期热点 id（续集）
  is_must          INTEGER DEFAULT 0,
  is_backup        INTEGER DEFAULT 0,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hotspots_day ON hotspots(day);

CREATE TABLE IF NOT EXISTS packs (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  hotspot_id       INTEGER NOT NULL UNIQUE,
  scripts          TEXT NOT NULL,          -- json {"15s":..,"30s":..,"60s":..}
  captions         TEXT NOT NULL,          -- json {"15s":[{t,text,gold}]}
  image_candidates TEXT NOT NULL,          -- json [{type:card|remote,path/url,label}]
  image_selected   TEXT NOT NULL,          -- json [index,...]
  publish          TEXT NOT NULL,          -- json {titles,tags,cover_text}
  risks            TEXT NOT NULL,          -- json [{word,suggestion,index}]
  checklist        TEXT NOT NULL,          -- json [str]
  style_id         INTEGER,
  style_note       TEXT DEFAULT '',
  wordcount_ok     INTEGER DEFAULT 1,
  outline          TEXT DEFAULT '{}',   -- 关键词提纲缓存 {"15s":[{"text","keys"}],...}
  created_at       TEXT NOT NULL,
  FOREIGN KEY(hotspot_id) REFERENCES hotspots(id)
);

CREATE TABLE IF NOT EXISTS styles (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT NOT NULL,
  source_text TEXT NOT NULL,
  teardown    TEXT NOT NULL,               -- json 五件套
  used_count  INTEGER DEFAULT 0,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fetch_runs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  status      TEXT DEFAULT 'running',      -- running | done | error
  stats       TEXT DEFAULT '{}',           -- json 漏斗与各源明细
  error       TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS app_state (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
