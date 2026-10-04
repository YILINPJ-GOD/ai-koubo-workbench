"""热点打分与必做/备选选择（纯逻辑，独立测试）。"""

MUST_MIN = 80     # ≥80 直接进必做
BACKUP_MIN = 60   # 60~79 进备选
MUST_CAP = 6      # 首页必做最多 6 张
BACKUP_CAP = 6
MUST_FLOOR = 4    # 必做保底：不足 4 条时从热点库递补未拍高分选题（2026-10-04 用户反馈）    # 备选最多 6 个

VALID_LENGTHS = {"15s", "30s", "60s"}


def pick_must_and_backup(events: list[dict]):
    """events 按 score 降序传入或任意顺序均可。

    返回 (must_ids, backup_ids) —— events 里的下标列表。
    """
    ordered = sorted(
        range(len(events)),
        key=lambda i: events[i].get("score") or 0,
        reverse=True,
    )
    must, backup = [], []
    for i in ordered:
        s = events[i].get("score") or 0
        if s >= MUST_MIN and len(must) < MUST_CAP:
            must.append(i)
        elif s >= BACKUP_MIN and len(backup) < BACKUP_CAP:
            backup.append(i)
    return must, backup


def balance_must_by_origin(events, must_idx, backup_idx, origin_of):
    """国内外兼顾：若必做全为同一来源地区，用另一地区的最高分备选换入。

    origin_of(event_index) 返回 "国内" 或 "海外"。
    返回 (new_must_idx, new_backup_idx)；无法配平时原样返回。
    """
    if not must_idx or not backup_idx:
        return must_idx, backup_idx
    origins = [origin_of(i) for i in must_idx]
    if len(set(origins)) > 1:
        return must_idx, backup_idx
    minority = "国内" if origins[0] == "海外" else "海外"
    candidates = [j for j in backup_idx if origin_of(j) == minority]
    if not candidates:
        return must_idx, backup_idx
    weakest = min(must_idx, key=lambda i: events[i].get("score") or 0)
    swap_in = max(candidates, key=lambda j: events[j].get("score") or 0)
    # 换上来的不能太差
    if (events[swap_in].get("score") or 0) < 65:
        return must_idx, backup_idx
    new_must = [i for i in must_idx if i != weakest]
    new_must.append(swap_in)
    new_backup = [j for j in backup_idx if j != swap_in]
    new_backup.insert(0, weakest)
    return new_must, new_backup


def normalize_event(ev: dict) -> dict | None:
    """清洗 LLM 返回的单个事件；不合法返回 None。"""
    title = str(ev.get("title") or "").strip()
    if not title:
        return None
    score = ev.get("score")
    try:
        score = max(0, min(100, int(score)))
    except (TypeError, ValueError):
        score = 50
    length = str(ev.get("suggested_length") or "30s").strip()
    if length not in VALID_LENGTHS:
        length = "30s"
    angles = ev.get("angles")
    if not isinstance(angles, list):
        angles = [str(angles)] if angles else []
    return {
        "title": title[:80],
        "category": str(ev.get("category") or "").strip(),
        "why": str(ev.get("why") or "").strip(),
        "angles": [str(a).strip()[:60] for a in angles if str(a).strip()][:3],
        "suggested_length": length,
        "score": score,
        "item_ids": [int(x) for x in (ev.get("item_ids") or []) if str(x).isdigit()],
        "sequel_of": str(ev.get("sequel_of") or "").strip(),
    }
