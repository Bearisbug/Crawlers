"""把抖音、小红书的抓取结果和问卷对应关系整理进一个 SQLite（默认 outputs/crawl.sqlite），供分析和评论标注用。

数据来源（本机 outputs/，与服务器 data/ 校验一致）：
  抖音：dy_full/{accounts,works,comments,comments_tikhub}.jsonl、dy_full/raw/aweme.jsonl.gz（AI 标识）、
        dy_fix/{names_accepted,tikhub_resolved}.jsonl（按名称 / 短号补找回的号）
  小红书：xhs_full/{resolved,accounts,accounts_tikhub,xhs_accepted,notes_list,notes,comments}.jsonl
  问卷：inputs/{douyin,xhs}_meta.json（问卷里填的账号 → 类型、名称、答卷编号、自报粉丝数）
时间一律存北京时间「YYYY-MM-DD HH:MM:SS」。去重：同一个问卷账号以后写的行为准；作品 / 笔记按 ID、评论按评论 ID 去重。每次运行整库重建。
表：accounts（问卷里的每个账号）、account_responses（账号 ↔ 答卷编号）、posts（时间范围内的作品 / 笔记）、comments（一级评论）。
用法：python scripts/build_db.py [--out outputs/crawl.sqlite]
"""
import argparse
import gzip
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs"
CST = timezone(timedelta(hours=8))

SCHEMA = """
CREATE TABLE accounts (
  platform TEXT, input_id TEXT, q_type TEXT, q_name TEXT, q_fans TEXT,
  resolved INTEGER, match_method TEXT, account_id TEXT, display_id TEXT, nickname TEXT,
  followers INTEGER, posts_total INTEGER,
  PRIMARY KEY (platform, input_id));
CREATE TABLE account_responses (platform TEXT, input_id TEXT, response_id INTEGER);
CREATE TABLE posts (
  platform TEXT, post_id TEXT, input_id TEXT, type TEXT, title TEXT, text TEXT, tags TEXT,
  published_at TEXT, likes INTEGER, comments INTEGER, collects INTEGER, shares INTEGER,
  duration_s REAL, image_count INTEGER, url TEXT, ip_location TEXT, is_pinned INTEGER,
  ai_label TEXT, ai_download_forbidden INTEGER, source TEXT,
  PRIMARY KEY (platform, post_id));
CREATE TABLE comments (
  platform TEXT, comment_id TEXT, post_id TEXT, text TEXT, created_at TEXT, likes INTEGER,
  reply_count INTEGER, ip_location TEXT, user_id TEXT, user_name TEXT, source TEXT,
  PRIMARY KEY (platform, comment_id));
CREATE INDEX comments_post ON comments (platform, post_id);
CREATE INDEX posts_account ON posts (platform, input_id);
"""


def jsonl(path: Path):
    if path.exists():
        for line in path.open():
            if line.strip():
                yield json.loads(line)


def cst(iso_utc: str | None) -> str | None:
    # 小红书的时间是 UTC 的 ISO 串（…Z），抖音的已经是北京时间
    if not iso_utc:
        return None
    return datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).astimezone(CST).strftime("%Y-%m-%d %H:%M:%S")


def latest(rows, key):
    return {r[key]: r for r in rows}


def douyin_ai_labels() -> dict:
    """aweme_id → (AI 标签原文, 是否因 AI 内容禁止下载)，来自原始作品数据的 risk_infos 与 download_info。"""
    labels = {}
    with gzip.open(OUT / "dy_full/raw/aweme.jsonl.gz", "rt") as f:
        for line in f:
            a = json.loads(line)["response"]
            content = (a.get("risk_infos") or {}).get("content") or ""
            fail = ((a.get("video_control") or {}).get("download_info") or {}).get("fail_info") or {}
            labels[a["aweme_id"]] = (content if "AI" in content else None, int(fail.get("reason") == "aigc_forbidden"))
    return labels


def build_douyin(db: sqlite3.Connection) -> None:
    meta = json.loads((ROOT / "inputs/douyin_meta.json").read_text())
    acc = latest(jsonl(OUT / "dy_full/accounts.jsonl"), "dy_id")
    how = {r["dy_id"]: "name" for r in jsonl(OUT / "dy_fix/names_accepted.jsonl")}
    how |= {r["dy_id"]: "short_id" if r["match"] == "short_id" else "name" for r in jsonl(OUT / "dy_fix/tikhub_resolved.jsonl")}
    for dy_id, m in meta.items():
        a = acc.get(dy_id) or {}
        db.execute("INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
            "douyin", dy_id, m["type"], m["name"], m.get("self_reported_fans"), int(bool(a.get("resolved"))),
            how.get(dy_id, "id") if a.get("resolved") else None, a.get("sec_uid"), a.get("unique_id"), a.get("nickname"),
            a.get("follower_count"), a.get("aweme_count")))
        db.executemany("INSERT INTO account_responses VALUES (?,?,?)", [("douyin", dy_id, r) for r in m["responses"]])

    ai = douyin_ai_labels()
    for w in latest(jsonl(OUT / "dy_full/works.jsonl"), "id").values():
        label, forbidden = ai.get(w["id"], (None, 0))
        db.execute("INSERT INTO posts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "douyin", w["id"], w["dy_id"], "image" if w.get("aweme_type") == 68 else "video", None, w.get("desc"),
            json.dumps(w.get("hashtags") or [], ensure_ascii=False), w.get("create_time"), w.get("digg_count"),
            w.get("comment_count"), w.get("collect_count"), w.get("share_count"),
            (w.get("duration_ms") or 0) / 1000 or None, w.get("image_count"), w.get("share_url"), None,
            int(bool(w.get("is_top"))), label, forbidden, "self"))

    for name, source in (("comments.jsonl", "self"), ("comments_tikhub.jsonl", "tikhub")):
        db.executemany("INSERT OR IGNORE INTO comments VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            ("douyin", c["cid"], c["aweme_id"], c.get("text"), c.get("create_time"), c.get("digg_count"),
             c.get("reply_comment_total"), c.get("ip_label"), c.get("user_sec_uid"), c.get("user_nickname"), source)
            for c in jsonl(OUT / "dy_full" / name)))


def build_xhs(db: sqlite3.Connection) -> None:
    X = OUT / "xhs_full"
    meta = json.loads((ROOT / "inputs/xhs_meta.json").read_text())
    resolved = latest(jsonl(X / "resolved.jsonl"), "red_id")
    profiles = latest(jsonl(X / "accounts.jsonl"), "red_id")
    listed = latest((r for r in jsonl(X / "accounts_tikhub.jsonl") if r["step"] == "list"), "red_id")
    accepted = latest(jsonl(X / "xhs_accepted.jsonl"), "red_id")
    for red_id, m in meta.items():
        r, p, t = resolved.get(red_id) or {}, profiles.get(red_id) or {}, listed.get(red_id) or {}
        if red_id in accepted:
            ok, method, uid, shown = 1, "name", accepted[red_id]["user_id"], accepted[red_id]["matched_red_id"]
        elif r.get("matched"):
            ok, method, uid, shown = 1, "id", r.get("user_id"), red_id
        else:
            ok, method, uid, shown = 0, None, None, None
        db.execute("INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
            "xhs", red_id, m["type"], m["name"], m.get("self_reported_fans"), ok, method, uid, shown,
            t.get("nickname") or p.get("nickname") or r.get("nickname") if ok else None,
            t.get("fans") or p.get("fans") or r.get("fans") if ok else None, t.get("notes_posted")))
        db.executemany("INSERT INTO account_responses VALUES (?,?,?)", [("xhs", red_id, i) for i in m["responses"]])

    list_type = {n["note_id"]: n.get("type") for n in jsonl(X / "notes_list.jsonl")}
    notes = latest(jsonl(X / "notes.jsonl"), "note_id")
    for n in notes.values():
        db.execute("INSERT INTO posts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "xhs", n["note_id"], n["red_id"], n.get("type") or list_type.get(n["note_id"]), n.get("title"), n.get("desc"),
            json.dumps(n.get("tags") or [], ensure_ascii=False), cst(n.get("published_at")), n.get("likes"), n.get("comments"),
            n.get("collects"), n.get("shares"), n.get("video_duration_s"), n.get("image_count"), n.get("url"),
            n.get("ip_location"), int(bool(n.get("is_pinned"))), None, None, "tikhub" if n.get("source") == "tikhub" else "self"))
    src = {k: ("tikhub" if n.get("source") == "tikhub" else "self") for k, n in notes.items()}
    db.executemany("INSERT OR IGNORE INTO comments VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
        ("xhs", c["comment_id"], c["note_id"], c.get("content"), cst(c.get("created_at")), c.get("likes"),
         c.get("sub_comment_count"), c.get("ip_location"), c.get("user_id"), c.get("nickname"), src.get(c["note_id"]))
        for c in jsonl(X / "comments.jsonl")))


def main(out: Path) -> None:
    out.unlink(missing_ok=True)
    db = sqlite3.connect(out)
    db.executescript(SCHEMA)
    build_douyin(db)
    build_xhs(db)
    db.commit()
    for p in ("douyin", "xhs"):
        counts = [db.execute(q, (p,)).fetchone()[0] for q in (
            "SELECT count(*) FROM accounts WHERE platform=?", "SELECT sum(resolved) FROM accounts WHERE platform=?",
            "SELECT count(*) FROM posts WHERE platform=?", "SELECT count(*) FROM comments WHERE platform=?")]
        print(p, dict(zip(("accounts", "resolved", "posts", "comments"), counts)))
    db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT / "crawl.sqlite"))
    main(Path(ap.parse_args().out))
