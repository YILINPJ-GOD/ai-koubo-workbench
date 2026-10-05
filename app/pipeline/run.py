"""刷新流水线编排：抓取 → 抽取 → 摘要 → 去重 → 入库（→ 聚合打分，M3 接入）。"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from ..config import SOURCE_REGIONS, load_config
from ..db import connect, execute, query, query_one, set_state
from ..services import jobs
from . import extract
from .dedup import dedup_drafts
from .fetchers import FETCHERS
from .http import SourceError, http
from .summarize import summarize_items

MAX_EXTRACT = 60  # 单次刷新最多全文抽取条数，控制总时长
STALE_DAYS = 15   # 旧闻过滤：原文发布时间超过 15 天不要（2026-10-04 用户反馈）


def _is_stale(published_at: str) -> bool:
    """原文发布时间是否超过 STALE_DAYS 天。缺失/无法解析视为新鲜。"""
    pub = (published_at or "").strip()
    if not pub:
        return False
    try:
        from datetime import timezone as _tz

        ts = datetime.fromisoformat(pub.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_tz.utc)
        cutoff = datetime.now(_tz.utc) - timedelta(days=STALE_DAYS)
        return ts < cutoff
    except ValueError:
        return False


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _recent_titles() -> list[str]:
    since = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    rows = query(
        "SELECT title FROM items WHERE day >= ? ORDER BY id DESC LIMIT 600",
        (since,),
    )
    return [r["title"] for r in rows]


def _existing_urls() -> set[str]:
    rows = query("SELECT url FROM items")
    return {r["url"] for r in rows}


def refresh_pipeline(job_id: str) -> dict:
    """完整刷新一次。由 jobs.start_job 调度；M3 的聚合打分在入库后执行。"""
    cfg = load_config()
    enabled = [k for k, on in cfg.get("sources", {}).items() if on]
    run_id = _start_run(len(enabled))
    stats = {"raw": 0, "dup": 0, "irrelevant": 0, "kept": 0, "per_source": {}, "sources_total": len(enabled)}
    drafts_all: list[dict] = []

    with http() as client:
        for idx, key in enumerate(enabled):
            jobs.update_job(
                job_id,
                done=idx,
                total=len(enabled),
                stage="fetching",
                message=f"正在抓取 {key}",
            )
            per = {"fetched": 0, "kept": 0, "error": ""}
            try:
                drafts = FETCHERS[key](client)
                per["fetched"] = len(drafts)
                drafts_all.extend(drafts)
            except SourceError as e:
                per["error"] = str(e)
            except KeyError:
                per["error"] = "未知抓取源"
            except Exception as e:  # noqa: BLE001 —— 单源异常形状不拖垮整次刷新（审查M2）
                per["error"] = f"{type(e).__name__}: {str(e)[:60]}"
            stats["per_source"][key] = per

    stats["raw"] = len(drafts_all)
    jobs.update_job(job_id, done=len(enabled), stage="extract", message="抓取完成，正在读取正文")

    # 批内 + 库内去重
    drafts_all, dup = dedup_drafts(drafts_all, _existing_urls(), _recent_titles())
    stats["dup"] = dup

    # 并发抽取正文与配图（热榜条目多为搜索链接，跳过全文抽取）
    to_extract = [d for d in drafts_all if d["source_type"] != "trending"][:MAX_EXTRACT]
    if to_extract:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(extract.extract_fulltext, d["url"]): d for d in to_extract}
            for fut in as_completed(futures):
                d = futures[fut]
                try:
                    d["raw_text"], d["images"] = fut.result()
                except Exception:  # noqa: BLE001
                    d["raw_text"], d["images"] = "", []

    # AI 摘要与分类（LLM 判「无关」的丢弃）
    jobs.update_job(job_id, stage="summarize", message="AI 正在生成摘要")
    summaries = _summarize_with_fallback(drafts_all)

    kept = 0
    conn = connect()
    try:
        for i, d in enumerate(drafts_all):
            s = summaries.get(i)
            if s is None:
                stats["irrelevant"] += 1
                continue
            # 旧闻过滤：原文发布时间超过 15 天的不要（缺失发布时间视为新鲜）
            if _is_stale(d.get("published_at") or ""):
                stats["stale"] = stats.get("stale", 0) + 1
                continue
            images = d.get("images") or []
            region = s.get("region") or SOURCE_REGIONS.get(d["source_key"], "")
            conn.execute(
                """INSERT OR IGNORE INTO items
                   (source_key, source_type, title, url, summary, summary_zh, category,
                    image_url, images, raw_text, published_at, fetched_at, day, region)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    d["source_key"], d["source_type"], d["title"], d["url"],
                    s["summary"], s["summary_zh"], s["category"],
                    images[0] if images else (d.get("image_url") or ""),
                    json.dumps(images, ensure_ascii=False),
                    d.get("raw_text") or "", d.get("published_at") or "",
                    _now(), _today(), region,
                ),
            )
            kept += 1
        conn.commit()
    finally:
        conn.close()
    stats["kept"] = kept

    # M3 聚合打分：失败不阻塞刷新（无 key / 网络问题降级为仅抓取）
    # 传入本次抓取的 day：23:59 开跑 00:01 聚类时不会丢掉昨夜条目（审查L10）
    try:
        from .cluster import cluster_and_store

        stats.update(cluster_and_store(job_id, run_day=_today()))
    except Exception as e:  # noqa: BLE001
        stats["cluster_error"] = str(e)

    _finish_run(run_id, stats)
    set_state("last_refresh_at", _now())
    set_state("last_refresh_stats", json.dumps(stats, ensure_ascii=False))
    new_events = stats.get("new", 0)
    toast = f"抓到 {stats['raw']} 条，去重后新增 {stats['kept']} 条，热点 {new_events} 个"
    if stats.get("cluster_error"):
        toast += f"；热点聚合未完成：{stats['cluster_error']}"
    return {"toast": toast, "stats": stats, "run_id": run_id}


def _summarize_with_fallback(drafts: list[dict]) -> dict[int, dict]:
    if not drafts:
        return {}
    from ..llm import get_llm

    items = [
        {
            "i": i,
            "title": d["title"],
            "text": d.get("raw_text") or d.get("summary_html") or d["title"],
            "overseas": d["source_type"] == "overseas",
        }
        for i, d in enumerate(drafts)
    ]
    try:
        llm = get_llm()
    except Exception:  # noqa: BLE001 —— 无 key 时保留条目不丢，摘要为空待配 key
        return {
            i: {"summary": "", "category": "", "summary_zh": ""}
            for i in range(len(drafts))
        }
    return summarize_items(items, llm)


def _start_run(total: int) -> int:
    return execute(
        "INSERT INTO fetch_runs(started_at, status, stats) VALUES (?, 'running', ?)",
        (_now(), json.dumps({"sources_total": total}, ensure_ascii=False)),
    )


def _finish_run(run_id: int, stats: dict) -> None:
    conn = connect()
    try:
        conn.execute(
            "UPDATE fetch_runs SET finished_at=?, status='done', stats=? WHERE id=?",
            (_now(), json.dumps(stats, ensure_ascii=False), run_id),
        )
        conn.commit()
    finally:
        conn.close()


def last_run() -> dict | None:
    return query_one("SELECT * FROM fetch_runs ORDER BY id DESC LIMIT 1")
