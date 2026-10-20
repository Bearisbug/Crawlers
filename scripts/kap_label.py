"""评论 KAP 批量标注：本机 Claude Agent SDK（走 Claude 订阅，不走 API 计费）+ 结构化输出，结果写回 outputs/crawl.sqlite。

- 编码手册：prompts/kap_codebook.md，整份作为系统提示词；手册内容的哈希记为 codebook_sha，改了手册就是一套新标注，从头标。
- 打包：同一作品的评论放在一起，附作品标题 / 文案作上下文；每个请求塞满 --batch-size 条（默认 1500）。
  单次请求的上限是输出 token（每条约 15 个），不是 1M 上下文；条数太多时模型更容易漏标，漏掉的会自动重排进下一轮。
- 去重：同一作品下文本完全相同的评论只标一次；空文本按规则记 K=0 / A=中 / P=0。
- 并行：--workers 个请求同时跑（默认 4）。订阅用量到顶时所有请求一起暂停：报错里带重置时间（如「resets 3am」）就等到那时，
  没带就每 30 分钟试一次（服务过载这类临时错误等 5 分钟），额度恢复后自动接着跑；已完成的结果不受影响。
- 断点续跑：每个请求完成即提交到 kap_labels；重跑只处理还没标的评论。一轮跑完后对漏标的再补最多 2 轮。
用法：
  python scripts/kap_label.py                     # 全量标注（可随时 Ctrl-C，重跑接着来）
  python scripts/kap_label.py --limit-batches 2   # 只跑 2 个请求试一下
  python scripts/kap_label.py --export-only       # 只导出 CSV 到 outputs/export/
"""
import argparse
import asyncio
import csv
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = {
    "type": "object",
    "properties": {
        "labels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "i": {"type": "integer"},
                    "k": {"type": "integer", "minimum": 0, "maximum": 5},
                    "a": {"type": "string", "enum": ["正", "中", "负"]},
                    "p": {"type": "integer", "minimum": 0, "maximum": 5},
                },
                "required": ["i", "k", "a", "p"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["labels"],
    "additionalProperties": False,
}
TABLES = """
CREATE TABLE IF NOT EXISTS kap_labels (
  platform TEXT, comment_id TEXT, codebook_sha TEXT, k INTEGER, a TEXT, p INTEGER,
  model TEXT, batch_id TEXT, labeled_at TEXT, PRIMARY KEY (platform, comment_id, codebook_sha));
CREATE TABLE IF NOT EXISTS kap_batches (
  batch_id TEXT PRIMARY KEY, codebook_sha TEXT, items INTEGER, labeled INTEGER, missing INTEGER,
  seconds REAL, input_tokens INTEGER, output_tokens INTEGER, error TEXT, finished_at TEXT);
"""


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


PAUSE = {"until": 0.0}  # 所有请求共用的暂停截止时间（订阅用量到顶时设置）


def reset_time(text: str) -> float | None:
    """从限额报错里读出重置时刻：「…|1792430000」这类时间戳，或「resets 3am」「resets 15:00」这类钟点（按本机时区取下一次）。"""
    m = re.search(r"\|(\d{10})\b", text)
    if m:
        return float(m[1])
    m = re.search(r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text, re.I)
    if not m:
        return None
    hour, minute = int(m[1]) % 12 if m[3] else int(m[1]), int(m[2] or 0)
    if m[3] and m[3].lower() == "pm":
        hour += 12
    t = datetime.now().replace(hour=hour % 24, minute=minute, second=0, microsecond=0)
    if t <= datetime.now():
        t += timedelta(days=1)
    return t.timestamp()


def load_todo(db: sqlite3.Connection, sha: str):
    """还没标的评论，按作品分组：{(platform, post_id): (上下文, {文本: [comment_id...]})}；空文本单独返回。"""
    rows = db.execute("""
      SELECT c.platform, c.post_id, c.comment_id, coalesce(c.text, ''), coalesce(p.title, ''), coalesce(p.text, '')
      FROM comments c JOIN posts p ON p.platform = c.platform AND p.post_id = c.post_id
      LEFT JOIN kap_labels l ON l.platform = c.platform AND l.comment_id = c.comment_id AND l.codebook_sha = ?
      WHERE l.comment_id IS NULL ORDER BY c.platform, c.post_id""", (sha,)).fetchall()
    posts, empty = {}, []
    for plat, post_id, cid, text, title, body in rows:
        text = text.strip()
        if not text:
            empty.append((plat, cid))
            continue
        ctx = re.sub(r"\s+", " ", f"{title} {body}").strip()[:150]
        posts.setdefault((plat, post_id), (ctx, defaultdict(list)))[1][text].append(cid)
    return posts, empty


def make_batches(posts: dict, size: int) -> list[dict]:
    """把作品依次装进请求，满 size 条就开新请求；大作品会被拆到多个请求里（每个都带作品上下文）。"""
    batches, cur = [], None
    for (plat, post_id), (ctx, texts) in posts.items():
        for text, cids in texts.items():
            if cur is None or len(cur["items"]) >= size:
                cur = {"items": [], "sections": []}
                batches.append(cur)
            if not cur["sections"] or cur["sections"][-1][0] != (plat, post_id):
                cur["sections"].append(((plat, post_id), ctx, []))
            cur["items"].append((plat, cids))
            cur["sections"][-1][2].append((len(cur["items"]), text))
    for n, b in enumerate(batches):
        lines = [f"下面共 {len(b['items'])} 条评论，按作品分组，编号 1–{len(b['items'])}。逐条按编码手册打分，全部编号都要输出。"]
        for (plat, _), ctx, items in b["sections"]:
            lines.append(f"\n## 作品（{'抖音' if plat == 'douyin' else '小红书'}）：{ctx or '（无标题）'}")
            lines += [f"{i}. {t[:500]}" for i, t in items]
        b["prompt"] = "\n".join(lines)
    return batches


def save(db, sha, model, batch_id, rows) -> None:
    db.executemany("INSERT OR REPLACE INTO kap_labels VALUES (?,?,?,?,?,?,?,?,?)",
                   [(plat, cid, sha, k, a, p, model, batch_id, now()) for plat, cid, k, a, p in rows])
    db.commit()


async def run_batch(b: dict, codebook: str, args) -> tuple[dict | None, dict]:
    """调一次模型；限流 / 订阅用量到顶时退避重试，其他错误返回 None 交给下一轮。"""
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

    opts = ClaudeAgentOptions(
        model=args.model, system_prompt=codebook, tools=[], setting_sources=[], max_turns=3, effort="low",
        output_format={"type": "json_schema", "schema": SCHEMA},
        env={"CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(args.max_output_tokens)},
    )
    while True:
        while time.time() < PAUSE["until"]:
            await asyncio.sleep(min(60, PAUSE["until"] - time.time()))
        started, result, err = time.time(), None, None
        try:
            async for msg in query(prompt=b["prompt"], options=opts):
                if isinstance(msg, ResultMessage):
                    result = msg
        except Exception as e:  # CLI 进程异常退出等
            err = repr(e)[:300]
        meta = {"seconds": round(time.time() - started, 1), "usage": (result.usage or {}) if result else {}}
        if result and not result.is_error and isinstance(result.structured_output, dict):
            return result.structured_output, meta
        text = f"{err or ''} {getattr(result, 'result', '') or ''} {getattr(result, 'errors', '') or ''}"
        status = getattr(result, "api_error_status", None)
        if status in (429, 529) or re.search(r"limit|rate|overloaded|quota", text, re.I):
            # 订阅额度到顶没给重置时间就 30 分钟后再试；服务过载（529）这类临时错误只等 5 分钟
            quota = re.search(r"usage limit|hit your limit|limit reached|quota", text, re.I)
            until = (reset_time(text) or time.time() + (1800 if quota else 300)) + 120
            if until > PAUSE["until"]:
                PAUSE["until"] = until
                print(f"  [{now()}] 订阅用量到顶或限流，全部请求暂停到 {time.strftime('%m-%d %H:%M', time.localtime(until))}："
                      f"{text.strip()[:150]}", flush=True)
            continue
        meta["error"] = (text.strip() or f"status {status}")[:300]
        return None, meta


async def label(db, codebook: str, sha: str, args) -> None:
    for round_no in range(1, 4):  # 第 2、3 轮只补上一轮漏标或出错的
        posts, empty = load_todo(db, sha)
        if empty:
            save(db, sha, "rule", "rule-empty", [(plat, cid, 0, "中", 0) for plat, cid in empty])
        batches = make_batches(posts, args.batch_size)
        if args.limit_batches:
            batches = batches[: args.limit_batches]
        total = sum(len(cids) for b in batches for _, cids in b["items"])
        if not batches:
            print("全部评论已标完。")
            return
        print(f"第 {round_no} 轮：{len(batches)} 个请求，{total} 条评论（去重后 {sum(len(b['items']) for b in batches)} 条文本），并行 {args.workers}", flush=True)

        sem, done, started = asyncio.Semaphore(args.workers), [0, 0], time.time()

        async def worker(n, b):
            async with sem:
                out, meta = await run_batch(b, codebook, args)
            batch_id = f"{sha[:8]}-r{round_no}-{n}-{int(started)}"
            got = {}
            for l in (out or {}).get("labels", []):
                if 1 <= l.get("i", 0) <= len(b["items"]):
                    got[l["i"]] = l
            rows = [(plat, cid, got[i]["k"], got[i]["a"], got[i]["p"])
                    for i, (plat, cids) in enumerate(b["items"], 1) if i in got for cid in cids]
            save(db, sha, args.model, batch_id, rows)
            u = meta["usage"]
            db.execute("INSERT OR REPLACE INTO kap_batches VALUES (?,?,?,?,?,?,?,?,?,?)", (
                batch_id, sha, len(b["items"]), len(got), len(b["items"]) - len(got), meta["seconds"],
                (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0),
                u.get("output_tokens"), meta.get("error"), now()))
            db.commit()
            done[0] += 1
            done[1] += len(rows)
            el = time.time() - started
            print(f"  [{now()}] 请求 {done[0]}/{len(batches)}：{len(got)}/{len(b['items'])} 条，{meta['seconds']}s"
                  f"{'，错误：' + meta['error'][:80] if meta.get('error') else ''}；已标 {done[1]}/{total}，"
                  f"预计还要 {el / done[0] * (len(batches) - done[0]) / 60:.0f} 分钟", flush=True)

        await asyncio.gather(*(worker(n, b) for n, b in enumerate(batches)))
        if args.limit_batches:
            return


def export(db, sha: str) -> None:
    out = ROOT / "outputs/export"
    out.mkdir(parents=True, exist_ok=True)
    queries = {
        "accounts.csv": "SELECT a.*, group_concat(r.response_id) AS response_ids FROM accounts a "
                        "LEFT JOIN account_responses r USING (platform, input_id) GROUP BY a.platform, a.input_id",
        "posts.csv": "SELECT p.*, a.q_type, a.q_name FROM posts p JOIN accounts a USING (platform, input_id)",
        "comments_kap.csv": "SELECT c.platform, c.comment_id, c.post_id, p.input_id, a.q_type, a.q_name, c.text, c.created_at, "
                            "c.likes, c.reply_count, l.k, l.a, l.p, l.model FROM comments c "
                            "JOIN posts p ON p.platform = c.platform AND p.post_id = c.post_id "
                            "JOIN accounts a ON a.platform = p.platform AND a.input_id = p.input_id "
                            "LEFT JOIN kap_labels l ON l.platform = c.platform AND l.comment_id = c.comment_id AND l.codebook_sha = ?",
        "accounts_kap.csv": "SELECT p.platform, p.input_id, a.q_type, a.q_name, count(*) AS comments, count(l.k) AS labeled, "
                            "round(avg(l.k), 3) AS k_mean, round(avg(l.p), 3) AS p_mean, "
                            "round(avg(l.a = '正'), 3) AS a_pos, round(avg(l.a = '中'), 3) AS a_neu, round(avg(l.a = '负'), 3) AS a_neg "
                            "FROM comments c JOIN posts p ON p.platform = c.platform AND p.post_id = c.post_id "
                            "JOIN accounts a ON a.platform = p.platform AND a.input_id = p.input_id "
                            "LEFT JOIN kap_labels l ON l.platform = c.platform AND l.comment_id = c.comment_id AND l.codebook_sha = ? "
                            "GROUP BY p.platform, p.input_id",
    }
    for name, q in queries.items():
        cur = db.execute(q, (sha,) if "?" in q else ())
        with (out / name).open("w", newline="", encoding="utf-8-sig") as f:  # 带 BOM，Excel 直接打开不乱码
            w = csv.writer(f)
            w.writerow([d[0] for d in cur.description])
            n = 0
            for row in cur:
                w.writerow(row)
                n += 1
        print(f"导出 {out / name}：{n} 行")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "outputs/crawl.sqlite"))
    ap.add_argument("--codebook", default=str(ROOT / "prompts/kap_codebook.md"))
    ap.add_argument("--model", default="claude-sonnet-5-5")
    ap.add_argument("--batch-size", type=int, default=1500)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-output-tokens", type=int, default=64000)
    ap.add_argument("--limit-batches", type=int, default=0)
    ap.add_argument("--export-only", action="store_true")
    args = ap.parse_args()

    os.environ.pop("ANTHROPIC_API_KEY", None)  # 有 API key 时 CLI 会改走 API 计费；这里固定走本机登录的订阅
    if not Path(args.db).exists():
        subprocess.run([sys.executable, str(ROOT / "scripts/build_db.py"), "--out", args.db], check=True)
    codebook = Path(args.codebook).read_text()
    sha = hashlib.sha256(codebook.encode()).hexdigest()[:16]
    db = sqlite3.connect(args.db)
    db.executescript(TABLES)
    print(f"编码手册 {args.codebook}（sha {sha}），模型 {args.model}")
    if not args.export_only:
        asyncio.run(label(db, codebook, sha, args))
    export(db, sha)


if __name__ == "__main__":
    main()
