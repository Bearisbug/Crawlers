"""抖音一级评论（自建）：works.jsonl 里评论数 > 0、还没有评论的作品，按抖音默认的热度排序翻页，每条最多 --max-comments 条（默认 500）。

调本机 TikTokDownloader Web API（127.0.0.1:5555）。抖音评论接口没有排序参数，只能按默认热度排序翻页：
用全量数据核对过，一级评论超过 500 条的 73 条作品，默认排序的前 500 条都包含点赞最多的 100 条，导出时再按点赞排序。
多个账号轮流请求（--accounts，每个文件是 login_capture.py / export_edge_cookies.py 的输出）。
账号被风控时评论接口只回空响应：某个号连续 5 条作品（显示评论数 ≥ 3）一条都没拿到，就暂停这个号，
弹通知并用 verify_session.py 开窗口让人过验证，通过后换上新 cookie 继续；没拿到的作品放回队尾，同一条 3 次都没拿到才记失败。
用法（在 TikTokDownloader 的 venv 里）：
    python dy_comments_self.py --out-dir data/dy_full --accounts .secrets/accounts/douyin_*.json [--max-comments 500]
读：works.jsonl、comments.jsonl（已有评论的作品跳过）
写：comments.jsonl（每条评论一行）、raw/comments.jsonl.gz（每条作品的原始评论列表）、
    comments_failed.jsonl（评论数 > 0 却没拿到的作品，重跑时再试）
"""
import argparse
import gzip
import json
import math
import random
import subprocess
import time
from collections import Counter, deque
from datetime import datetime
from pathlib import Path

from dy_rows import comment_row
from tkd_call import call

HERE = Path(__file__).resolve().parent
VERIFY_PY = HERE.parent / ".venv/bin/python"  # verify_session.py 要 Playwright，在项目 venv 里
VERIFY_RETRY_S = 30 * 60  # 没过验证（超时或关了窗口）的号，30 分钟后再试，再被拦会再弹窗


class Account:
    def __init__(self, path: str):
        self.path = Path(path)
        self.empty_in_row, self.proc, self.retry_at = 0, None, 0.0
        self.load()

    def load(self) -> None:
        d = json.loads(self.path.read_text())
        self.cookie, self.label = d["cookie_str"], d.get("account_hint") or self.path.stem

    def usable(self) -> bool:
        if self.proc and self.proc.poll() is not None:
            if self.proc.returncode == 0:
                self.load()
                print(json.dumps({"verified": self.label}, ensure_ascii=False), flush=True)
            else:
                self.retry_at = time.time() + VERIFY_RETRY_S
            self.proc, self.empty_in_row = None, 0
        return self.proc is None and self.retry_at <= time.time()

    def verify(self, aweme_id: str) -> None:
        print(json.dumps({"blocked": self.label, "verify_on": aweme_id}, ensure_ascii=False), flush=True)
        self.proc = subprocess.Popen([str(VERIFY_PY), str(HERE / "verify_session.py"), "douyin", str(self.path),
                                      f"https://www.douyin.com/video/{aweme_id}"])


def append(path: Path, row: dict) -> None:
    with path.open("a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main(args) -> None:
    out = Path(args.out_dir)
    accounts = [Account(p) for p in args.accounts]
    comments_path = out / "comments.jsonl"
    have = {json.loads(line)["aweme_id"] for line in comments_path.open()} if comments_path.exists() else set()
    works = {}
    for line in (out / "works.jsonl").open():
        w = json.loads(line)
        works[w["id"]] = w
    queue = deque(w for w in works.values() if (w.get("comment_count") or 0) > 0 and w["id"] not in have)
    print(json.dumps({"todo": len(queue), "max_comments": args.max_comments,
                      "accounts": [a.label for a in accounts]}, ensure_ascii=False), flush=True)

    tries, turn, finished = Counter(), 0, 0
    while queue:
        usable = [a for a in accounts if a.usable()]
        if not usable:
            time.sleep(30)  # 所有号都在等人工验证
            continue
        acc = usable[turn % len(usable)]
        turn += 1
        w = queue.popleft()
        now = datetime.now().isoformat(timespec="seconds")
        # 页数按页面显示的评论数封顶：评论看不到的作品抖音会一直回「还有下一页」，不封顶就白翻满 25 页
        pages = min(math.ceil(args.max_comments / 20), math.ceil(w["comment_count"] / 20) + 1)
        comments = call("/douyin/comment", {"cookie": acc.cookie, "detail_id": w["id"],
                                            "pages": pages, "count": 20,
                                            "reply": False, "source": True}).get("data") or []
        with gzip.open(out / "raw" / "comments.jsonl.gz", "at", encoding="utf-8") as f:
            f.write(json.dumps({"kind": "comments", "key": w["id"], "dy_id": w["dy_id"], "account": acc.label,
                                "fetched_at": now, "response": comments}, ensure_ascii=False) + "\n")
        tries[w["id"]] += 1
        requeued = False
        if comments:
            acc.empty_in_row = 0
            for c in comments[: args.max_comments]:
                append(comments_path, comment_row(w["id"], c))
        elif w["comment_count"] >= 3 and tries[w["id"]] < 3:
            acc.empty_in_row += 1
            if acc.empty_in_row >= 5:
                acc.verify(w["id"])
            queue.append(w)  # 多半是这个号被拦了，放回队尾由别的号或验证后再试
            requeued = True
        else:
            append(out / "comments_failed.jsonl", {"dy_id": w["dy_id"], "aweme_id": w["id"],
                                                   "expected": w["comment_count"], "at": now})
        if not requeued:
            finished += 1
            if finished % 100 == 0:
                print(json.dumps({"progress": finished, "left": len(queue)}), flush=True)
        time.sleep(random.uniform(1, 3))
    print(json.dumps({"finished": finished}), flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="data/dy_full")
    ap.add_argument("--accounts", nargs="+", required=True)
    ap.add_argument("--max-comments", type=int, default=500)
    main(ap.parse_args())
