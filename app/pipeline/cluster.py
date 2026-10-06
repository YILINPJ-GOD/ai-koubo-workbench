"""事件聚合：把今天的资讯条目聚合成热点事件并落库。

- 同一事件多篇报道 → 一张热点卡，记独立来源数
- 参考近期热点历史：讲过的不重复硬推，后续进展标续集
- 无值得做的选题时生成「今天没有」的理由存入 app_state
"""
import json
from datetime import datetime, timedelta

from ..db import connect, execute, query, query_one, set_state
from ..services.today import _active_since
from .dedup import title_similarity
from difflib import SequenceMatcher as _SeqM
from .http import SourceError
from .score import MUST_CAP, MUST_FLOOR, balance_must_by_origin, normalize_event, pick_must_and_backup

SYSTEM_CLUSTER = (
    "你是抖音AI资讯口播账号的选题主编，账号风格轻松接地气，主打大模型动态与AI产品应用，"
    "科技互联网大事件也会跟进，更新宁缺毋滥。"
    "把今天的资讯聚合成值得做视频的「热点事件」（同一事件的多条报道合并），并打分，"
    "并给事件标注 category（大模型/AI产品/科技大事件/无关 之一）。"
    "打分标准：大模型/AI产品相关加成；多家独立来源报道加成；出圈潜质加成。"
    "标题和 why 必须点名具体主角：是谁家的什么（工具/产品/模型的名字要出现）；"
    "如果新闻是'某个工具/项目让某个模型做到了某事'，主角是那个工具，标题以它为主"
    "（如'Strata让千问125B在游戏本跑起来'），不要只写泛泛的技术现象。"
    "宁缺毋滥，以下硬性不给事件（category 填「无关」）："
    "开发者工具/开源项目/云服务/操作系统（除非与AI直接相关）；"
    "公司招聘、融资八卦、YC 公司动态；教程/盘点/预测类软文；"
    "与 AI 大模型、AI 产品、头部科技公司重大发布都无关的一般互联网新闻。"
    "「科技大事件」只给全民级出圈的头部科技大事（巨头重大发布、行业地震），"
    "普通的开发者新闻不算。汽车/新能源车/电动车行业动态不算科技大事件"
    "（除非核心是AI自动驾驶）；消费、娱乐、体育类也不算。"
    "只有值得单独开一条视频的才给事件，一天通常 5~10 个，不要超过 15 个。"
    "覆盖面要求：国内外兼顾——海外首发的重要动态（大模型发布、行业大事）要覆盖，"
    "国内厂商动态和国内视角报道也至少保留一个，不要清一色全是海外或全是国内。"
    "另参考「近期已做过的选题」：完全相同的旧事件不要给；如果是它的后续新进展，"
    "在 sequel_of 填那个旧选题标题。"
    "今天整体平淡时在 day_comment 写一句为什么今天不值得做。"
    '严格输出 JSON：{"events":[{"title":"...","why":"...","angles":["..."],'
    '"suggested_length":"15s|30s|60s","score":0到100,"item_ids":[条目i序号],'
    '"category":"大模型/AI产品/科技大事件/无关","sequel_of":""}],"day_comment":"..."}'
)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def fetch_today_items(day: str = "") -> list[dict]:
    return query(
        "SELECT id, title, summary, category, source_type, source_key, image_url "
        "FROM items WHERE day=? ORDER BY id",
        (day or _today(),),
    )


def recent_hotspot_titles(limit: int = 40) -> list[dict]:
    rows = query(
        """SELECT id, title, day, status FROM hotspots
           WHERE status IN ('packed','shot') OR day >= ?
           ORDER BY id DESC LIMIT ?""",
        ((datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d"), limit),
    )
    return rows


def build_cluster_payload(items: list[dict], recent: list[dict]) -> str:
    payload = {
        "today_items": [
            {
                "i": it["id"],
                "title": it["title"],
                "summary": it["summary"] or it["title"],
                "category": it["category"],
                "source_type": it["source_type"],
            }
            for it in items
        ],
        "recent_done_topics": [
            {"title": r["title"], "day": r["day"], "status": r["status"]} for r in recent
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def parse_events(data: dict) -> tuple[list[dict], str]:
    events = []
    for ev in (data or {}).get("events", []):
        norm = normalize_event(ev)
        if norm:
            events.append(norm)
    return events, str((data or {}).get("day_comment", "") or "")


def match_existing_hotspot(title: str, rows: list[dict], threshold: float = 0.7):
    """同事件匹配：相似度达阈值，或含 ≥10 字符公共子串（中英双源/措辞变体）。"""
    for r in rows:
        if title_similarity(title, r["title"]) >= threshold:
            return r
        if _longest_common(title, r["title"]) >= 10:
            return r
    return None


def upsert_hotspots(events: list[dict], llm_unused=None) -> dict:
    """事件落库：合并到近3天同事件，或新建。返回统计。"""
    today = _today()
    now = _now()
    stats = {"new": 0, "merged": 0, "must": 0, "backup": 0, "orphan": 0}
    for ev in events:
        ev.setdefault("category", "")

    conn = connect()

    # 匹配池 = 近3天已有卡 + 本轮处理中新建/合并的卡（防同轮变体重复建卡抢来源）
    recent_rows = query(
        "SELECT * FROM hotspots WHERE day >= ?",
        ((datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d"),),
    )

    must_idx, backup_idx = pick_must_and_backup(events)
    # 国内外兼顾：必做全为同一地区时，用另一地区高分备选换入
    DOMESTIC_KEYS = {"qwen"}  # 官方源里托管在海外的国内厂商

    def _origin_of(idx: int) -> str:
        ids = events[idx]["item_ids"]
        if not ids:
            return "国内"
        marks = ",".join("?" * len(ids))
        row = conn.execute(
            f"SELECT source_type, source_key FROM items WHERE id IN ({marks}) LIMIT 1", ids
        ).fetchone()
        if row:
            if row["source_key"] in DOMESTIC_KEYS:
                return "国内"
            if row["source_type"] in ("overseas", "official"):
                return "海外"
        return "国内"

    must_idx, backup_idx = balance_must_by_origin(events, must_idx, backup_idx, _origin_of)
    try:
        today_items = fetch_today_items()
        for idx, ev in enumerate(events):
            # item_ids 有效化：剔除 LLM 幻觉的不存在 id
            valid_ids = []
            if ev["item_ids"]:
                marks = ",".join("?" * len(ev["item_ids"]))
                valid_ids = [
                    r["id"]
                    for r in conn.execute(
                        f"SELECT id FROM items WHERE id IN ({marks}) AND day=?",
                        (*ev["item_ids"], _today()),
                    ).fetchall()
                ]
            # 无有效来源时按标题找回当日相似资讯（LLM 偶发改写条目标题）
            if not valid_ids:
                for it in today_items:
                    a, b = _normalize2(ev["title"]), _normalize2(it["title"])
                    contained = len(a) >= 6 and a in b or len(b) >= 6 and b in a
                    if title_similarity(ev["title"], it["title"]) >= 0.62 or contained:
                        valid_ids.append(it["id"])
                    if len(valid_ids) >= 3:
                        break
            ev["item_ids"] = valid_ids
            if not valid_ids:
                # 来源找不回：无证据的事件不推荐（保留记录但不进必做/备选/递补）
                ev["unusable"] = True
                stats["orphan"] += 1

            item_ids = ev["item_ids"]
            sources_count = 0
            cover = ""
            if item_ids:
                marks = ",".join("?" * len(item_ids))
                members = conn.execute(
                    f"SELECT DISTINCT source_key FROM items WHERE id IN ({marks})",
                    item_ids,
                ).fetchall()
                sources_count = len(members)
                cover_row = conn.execute(
                    f"SELECT image_url FROM items WHERE id IN ({marks}) AND image_url != '' LIMIT 1",
                    item_ids,
                ).fetchone()
                cover = cover_row["image_url"] if cover_row else ""

            sequel_id = None
            if ev.get("sequel_of"):
                past = query_one(
                    "SELECT id FROM hotspots WHERE title=? LIMIT 1",
                    (ev["sequel_of"],),
                )
                if past:
                    sequel_id = past["id"]

            existing = match_existing_hotspot(ev["title"], recent_rows)
            if existing:
                conn.execute(
                    """UPDATE hotspots SET score=?, why=?, angles=?, suggested_length=?,
                       sources_count=?, category=CASE WHEN ?!='' THEN ? ELSE category END,
                       cover=CASE WHEN ?!='' THEN ? ELSE cover END,
                       sequel_of=COALESCE(?, sequel_of), updated_at=? WHERE id=?""",
                    (ev["score"], ev["why"], json.dumps(ev["angles"], ensure_ascii=False),
                     ev["suggested_length"], sources_count, ev["category"], ev["category"],
                     cover, cover, sequel_id, now, existing["id"]),
                )
                hotspot_id = existing["id"]
                stats["merged"] += 1
                ev["hotspot_id"] = hotspot_id
            else:
                hotspot_id = conn.execute(
                    """INSERT INTO hotspots(title, category, why, angles, suggested_length, score,
                       sources_count, status, day, cover, sequel_of, is_must, is_backup,
                       created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,'pending',?,?,?,0,0,?,?)""",
                    (ev["title"], ev["category"], ev["why"], json.dumps(ev["angles"], ensure_ascii=False),
                     ev["suggested_length"], ev["score"], sources_count, today, cover,
                     sequel_id, now, now),
                ).lastrowid
                stats["new"] += 1
                ev["hotspot_id"] = hotspot_id
                recent_rows = [dict(r) for r in recent_rows]
                recent_rows.append({"id": hotspot_id, "title": ev["title"]})
            # 资讯条目挂到热点卡（feed 页跳转、来源聚合依赖此关联）
            if item_ids:
                marks = ",".join("?" * len(item_ids))
                conn.execute(
                    f"UPDATE items SET event_id=? WHERE id IN ({marks})",
                    (hotspot_id, *item_ids),
                )
        _apply_flags(conn, events, must_idx, backup_idx, stats)
        stats["backfilled"] = ensure_must_floor(conn)
        conn.commit()
    finally:
        conn.close()
    return stats


def _normalize2(t: str) -> str:
    from .dedup import normalize_title

    return normalize_title(t or "")


def _longest_common(a: str, b: str, normalize=True) -> int:
    """两标题规范化后的最长公共子串长度（用于中英双源同事件识别）。"""
    if normalize:
        from .dedup import normalize_title

        a, b = normalize_title(a), normalize_title(b)
    if not a or not b:
        return 0
    m = _SeqM(None, a, b).find_longest_match(0, len(a), 0, len(b))
    return m.size


def ensure_must_floor(conn, floor: int = MUST_FLOOR, cap: int = MUST_CAP) -> int:
        """必做保底：今天必做不足 floor 时，从热点库递补未拍（pending/packed）
        的高分选题，把 is_must 打开并刷新 updated_at 使其出现在首页。

        递补顺序=分数降序：今天的备选优先升入，其次是近几天未拍的旧选题。
        已拍（shot）永不递补。返回递补数量。
        """
        now = _now()
        today = _today()
        n = conn.execute(
            "SELECT COUNT(*) FROM hotspots WHERE is_must=1 AND status != 'expired' "
            "AND date(updated_at) >= ?",
            (_active_since(),),
        ).fetchone()[0]
        if n >= floor:
            return 0
        need = min(floor - n, cap - n)
        if need <= 0:
            return 0
        rows = conn.execute(
            """SELECT id, title FROM hotspots
               WHERE is_must=0 AND status NOT IN ('shot', 'expired')
                 AND (category = '' OR category IN ('大模型', 'AI产品', '科技大事件'))
                 AND EXISTS (SELECT 1 FROM items i WHERE i.event_id = hotspots.id)
               ORDER BY score DESC, updated_at DESC LIMIT ?""",
            (need * 3,),
        ).fetchall()
        cur = conn.execute(
            "SELECT title FROM hotspots WHERE is_must=1 AND status != 'expired' "
            "AND date(updated_at) >= ?",
            (_active_since(),),
        ).fetchall()
        must_titles = [r["title"] for r in cur]
        added = 0
        for r in rows:
            if n + added >= floor:
                break
            # 同事件去重：与任一在位必做相似或含长公共子串则跳过
            # （中英双源会聚成两张卡，如 "推出Gemini 4 Argon…" vs "Introducing Gemini 4 Argon"）
            if any(
                title_similarity(r["title"], t) >= 0.7
                or _longest_common(r["title"], t) >= 10
                for t in must_titles
            ):
                continue
            conn.execute(
                "UPDATE hotspots SET is_must=1, is_backup=0, updated_at=? WHERE id=?",
                (now, r["id"]),
            )
            must_titles.append(r["title"])
            added += 1
        return added


def _apply_flags(conn, events, must_idx, backup_idx, stats) -> None:
    """按选题结果设置 is_must/is_backup；先清今天的旧标记。"""
    today = _today()
    conn.execute(
        "UPDATE hotspots SET is_must=0, is_backup=0 WHERE day=? OR date(updated_at)=?",
        (today, today),
    )
    must_idx = [i for i in must_idx if not events[i].get("unusable")]
    backup_idx = [i for i in backup_idx if not events[i].get("unusable")]
    # 用落库时记录的真实 hotspot_id 打标记：合并进旧卡的事件标题未变，
    # 按标题回查会 miss（审查发现 M1：合并卡必做标记静默丢失）
    for idx in must_idx:
        hid = events[idx].get("hotspot_id")
        if hid:
            conn.execute("UPDATE hotspots SET is_must=1 WHERE id=?", (hid,))
            stats["must"] += 1
    for idx in backup_idx:
        hid = events[idx].get("hotspot_id")
        if hid:
            conn.execute("UPDATE hotspots SET is_backup=1 WHERE id=?", (hid,))
            stats["backup"] += 1


def merge_events_locally(events: list[dict], threshold: float = 0.75) -> list[dict]:
    """跨批次的同事件本地合并（确定性，不依赖 LLM）：标题相似即合并。

    合并规则：取高分者的标题/why/suggested_length，角度并集去重，item_ids 并集，分数取最大。
    """
    merged: list[dict] = []
    for ev in events:
        target = None
        for m in merged:
            if title_similarity(ev["title"], m["title"]) >= threshold:
                target = m
                break
        if target is None:
            merged.append(dict(ev))
            continue
        target["item_ids"] = sorted(set(target["item_ids"]) | set(ev["item_ids"]))
        target["angles"] = list(dict.fromkeys(target["angles"] + ev["angles"]))[:3]
        if ev["score"] > target["score"]:
            target["score"] = ev["score"]
            target["title"] = ev["title"]
        if ev["why"] and len(ev["why"]) > len(target["why"]):
            target["why"] = ev["why"]
        if ev.get("sequel_of") and not target.get("sequel_of"):
            target["sequel_of"] = ev["sequel_of"]
    return merged


CHUNK_SIZE = 25  # 单次 LLM 聚合的条目数上限，防止超长输出卡死


def cluster_and_store(job_id: str = "", llm=None, run_day: str = "") -> dict:
    """刷新流水线末尾调用：分批聚合当天条目 → 本地合并 → 热点卡落库。

    run_day：本次刷新开始时的日期。跨午夜跑完时仍聚合开跑那天的条目（审查L10）。
    """
    from ..llm import get_llm
    from ..services import jobs

    items = fetch_today_items(run_day)
    if not items:
        set_state(f"empty_reason_{_today()}", "今天还没有抓到任何资讯")
        return {"new": 0, "merged": 0}
    llm = llm or get_llm()
    recent = recent_hotspot_titles()

    chunks = [items[i : i + CHUNK_SIZE] for i in range(0, len(items), CHUNK_SIZE)]
    all_events: list[dict] = []
    comments: list[str] = []
    last_error = ""
    for ci, chunk in enumerate(chunks):
        if job_id:
            jobs.update_job(
                job_id,
                stage="cluster",
                message=f"AI 正在聚合今日热点（{ci + 1}/{len(chunks)} 批）…",
            )
        try:
            data = llm.chat_json(SYSTEM_CLUSTER, build_cluster_payload(chunk, recent))
        except Exception as e:  # noqa: BLE001 —— 单批失败跳过，其余批次照常
            last_error = str(e)
            continue
        evs, comment = parse_events(data)
        all_events.extend(evs)
        if comment:
            comments.append(comment)

    if not all_events and not comments and last_error:
        raise SourceError(f"热点聚合失败：{last_error}")

    events = merge_events_locally(all_events)
    # 分数地板：低于 55 的事件是噪音，直接丢弃（宁缺毋滥）
    dropped = [e for e in events if e["score"] < 55]
    events = [e for e in events if e["score"] >= 55]
    # 必做准入：明确「无关」的事件最多做备选
    for ev in events:
        if ev.get("category") == "无关":
            ev["score"] = min(ev["score"], 59)
    day_comment = comments[0] if comments else ""

    if not events:
        reason = day_comment or "AI 看完今天全部资讯，没有值得单独做视频的选题"
        set_state(f"empty_reason_{_today()}", reason)

    stats = upsert_hotspots(events)
    if events:
        set_state(f"empty_reason_{_today()}", "" if stats["must"] else
                  (day_comment or f"今天有 {len(events)} 个事件，但都不够值得单独开一条视频"))
    if last_error and events:
        stats["partial_error"] = last_error
    stats["day_comment"] = day_comment
    return stats
