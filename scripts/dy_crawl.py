"""抖音自建抓取：抖音号 → 搜索解析 sec_uid → 主页 → 范围内全部作品。评论由 dy_comments_self.py 抓。

依赖本机已启动的 TikTokDownloader Web API（127.0.0.1:5555），主页复用 dy_profile.py 的 User 接口。
在 TikTokDownloader 的 venv 里运行：
    DY_COOKIE_FILE=... python dy_crawl.py --ids ids.txt --out-dir data/dy_full [--since 2025/07/01 --until 2026/06/30] [--gap 120]
        [--resolved resolved.jsonl]
连续 3 个账号出错（多半是搜索接口被风控）时整轮停下。
服务器机房 IP 搜索会被要求滑块验证，这时先在本机用 dy_resolve.py 搜出 sec_uid，再用 --resolved 跳过搜索。
输出（<out-dir>/）：
    accounts.jsonl          每账号一行（解析结果、主页快照、作品数），写入即视为该账号完成
    works.jsonl             每作品一行
    accounts_errors.jsonl   出错的账号（不算完成，重跑时再试）
    raw/*.jsonl.gz          原始响应：search_user / user_profile / aweme，gzip 逐条追加
已写入 accounts.jsonl 的抖音号重跑时跳过；中途中断的账号会整号重来，作品按 id 去重即可。
"""
import argparse
import asyncio
import gzip
import json
import random
from datetime import datetime
from pathlib import Path

# dy_profile 导入时会 chdir 到 TikTokDownloader 仓库，命令行里的相对路径要按启动目录解析
CWD = Path.cwd()

from dy_profile import COOKIE, FIELDS, Params, User  # noqa: E402
from tkd_call import call  # noqa: E402


def ts(epoch) -> str | None:
    return datetime.fromtimestamp(epoch).isoformat(sep=" ") if epoch else None


def work_row(dy_id: str, a: dict) -> dict:
    st = a.get("statistics") or {}
    return {
        "platform": "douyin", "dy_id": dy_id, "sec_uid": (a.get("author") or {}).get("sec_uid"),
        "id": a["aweme_id"], "aweme_type": a.get("aweme_type"), "desc": a.get("desc"),
        "create_time": ts(a.get("create_time")), "is_top": bool(a.get("is_top")),
        "duration_ms": (a.get("video") or {}).get("duration"), "image_count": len(a.get("images") or []),
        "hashtags": [t.get("hashtag_name") for t in a.get("text_extra") or [] if t.get("hashtag_name")],
        "share_url": (a.get("share_info") or {}).get("share_url"),
        **{k: st.get(k) for k in ("digg_count", "comment_count", "collect_count", "share_count", "play_count")},
    }


def data(resp: dict) -> list:
    # 搜索被要求滑块验证时 TikTokDownloader 返回 data=None，不能当成「搜不到」
    if resp.get("data") is None:
        raise RuntimeError(resp.get("message"))
    return resp["data"]


class Store:
    def __init__(self, out: Path):
        self.out = out
        (out / "raw").mkdir(parents=True, exist_ok=True)

    def row(self, name: str, row: dict) -> None:
        with (self.out / name).open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def raw(self, kind: str, key: str, response, **extra) -> None:
        rec = {"kind": kind, "key": key, **extra, "fetched_at": datetime.now().isoformat(timespec="seconds"),
               "response": response}
        with gzip.open(self.out / "raw" / f"{kind}.jsonl.gz", "at", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def match(dy_id: str, items: list) -> tuple[dict | None, list]:
    users = [i.get("user_info") or {} for i in items]
    hit = next((u for u in users if dy_id in (u.get("unique_id"), u.get("short_id"))), None)
    return hit, [(u.get("unique_id") or u.get("short_id"), u.get("nickname")) for u in users[:3]]


def resolve(store: Store, dy_id: str) -> tuple[dict | None, list]:
    items = data(call("/douyin/search/user", {"keyword": dy_id, "count": 10, "source": True}))
    store.raw("search_user", dy_id, items)
    return match(dy_id, items)


async def crawl_account(store: Store, params, dy_id: str, args, pre: dict | None) -> dict:
    row = {"dy_id": dy_id, "resolved": False, "fetched_at": datetime.now().isoformat(timespec="seconds")}
    if pre is None:
        hit, candidates = resolve(store, dy_id)
        sec_uid = hit and hit["sec_uid"]
    else:
        sec_uid, candidates = pre["sec_uid"], pre["candidates"]
    if not sec_uid:
        return {**row, "candidates": candidates}
    profile = await User(params, cookie=COOKIE, sec_user_id=sec_uid).run() or {}
    store.raw("user_profile", sec_uid, profile, dy_id=dy_id)

    body = {"sec_user_id": sec_uid, "source": True}
    if args.since:
        body["earliest"] = args.since
    if args.until:
        body["latest"] = args.until
    # 范围内 0 条作品时 TikTokDownloader 也回 data=None（「获取数据失败」），和被拦分不开；
    # 记成 0 条，最后对 works_fetched=0 但 aweme_count>0 的账号单独复核
    awemes = call("/douyin/account", body).get("data") or []
    for a in awemes:
        store.raw("aweme", a["aweme_id"], a, dy_id=dy_id)
        store.row("works.jsonl", work_row(dy_id, a))
    return {**row, "resolved": True, "sec_uid": sec_uid, **{k: profile.get(k) for k in FIELDS},
            "works_fetched": len(awemes)}


async def main(args) -> None:
    out = CWD / args.out_dir
    store = Store(out)
    accounts_path = out / "accounts.jsonl"
    done = {json.loads(line)["dy_id"] for line in accounts_path.open()} if accounts_path.exists() else set()
    ids = [line.strip() for line in (CWD / args.ids).read_text().splitlines() if line.strip()]
    pre_map = ({(r := json.loads(line))["dy_id"]: r for line in (CWD / args.resolved).open()}
               if args.resolved else None)
    errors_in_row = 0

    async with Params() as params:
        for dy_id in ids:
            if dy_id in done or (pre_map is not None and dy_id not in pre_map):
                continue
            try:
                row = await crawl_account(store, params, dy_id, args, None if pre_map is None else pre_map[dy_id])
            except Exception as e:  # 单个账号出错不中断整轮；不写 accounts.jsonl，重跑时会再试
                store.row("accounts_errors.jsonl", {"dy_id": dy_id, "error": repr(e)[:300],
                                                    "at": datetime.now().isoformat(timespec="seconds")})
                print(json.dumps({"dy_id": dy_id, "error": repr(e)[:200]}, ensure_ascii=False), flush=True)
                errors_in_row += 1
                if errors_in_row >= 3:  # 连续出错多半是接口被风控，停下等人处理，免得继续刷被拦的接口
                    print(json.dumps({"stop": "3 consecutive account errors"}), flush=True)
                    return
                await asyncio.sleep(30)
                continue
            errors_in_row = 0
            store.row("accounts.jsonl", row)
            print(json.dumps({k: row.get(k) for k in ("dy_id", "resolved", "nickname", "follower_count", "aweme_count",
                                                       "works_fetched", "candidates")},
                             ensure_ascii=False), flush=True)
            await asyncio.sleep(random.uniform(args.gap, 2 * args.gap))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True)
    ap.add_argument("--out-dir", default="data/dy_full")
    ap.add_argument("--since", help="只抓该日期之后发布的作品，格式 YYYY/MM/DD")
    ap.add_argument("--until", help="只抓该日期之前发布的作品，格式 YYYY/MM/DD")
    ap.add_argument("--resolved", help="dy_resolve.py 输出的 resolved.jsonl；给了就不再搜索，只处理其中的抖音号")
    ap.add_argument("--gap", type=float, default=5, help="相邻两个账号之间的间隔秒数下限，实际在 gap~2×gap 之间随机")
    asyncio.run(main(ap.parse_args()))
