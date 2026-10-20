"""抖音号 → sec_uid（自建搜索），结果给 dy_crawl.py --resolved 用。

服务器机房 IP 搜索会被要求滑块验证（响应里 search_nil_info.search_nil_type = verify_check），这一步放在本机家庭网络跑。
直接调用 TikTokDownloader 的 Search 接口，不用起它的 Web API。在 TikTokDownloader 的 venv 里运行：
    TKD_REPO=... DY_COOKIE_FILE=... python dy_resolve.py --ids ids.txt --out-dir data/dy_resolve [--gap 60]
输出（<out-dir>/）：resolved.jsonl（每个抖音号一行）、raw/search_user.jsonl.gz（原始搜索结果）。
--names inputs/douyin_meta.json：改用问卷里的账号名称搜（抖音号搜不到时用），抖音号或昵称和名称完全一致才算对上。
已在 resolved.jsonl 里的抖音号重跑时跳过；遇到 verify_check 就弹通知、用 verify_session.py 开窗口让人过验证，
通过后换上新 cookie 重搜这个号，没通过就停。
"""
import argparse
import asyncio
import json
import random
import subprocess
from datetime import datetime
from pathlib import Path

from dy_crawl import CWD, Store, match  # dy_crawl 导入时会 chdir 到 TikTokDownloader 仓库
from src.interface.search import Search
from src.testers import Params
from tkd_call import COOKIE, COOKIE_FILE


class VerifyCheck(Exception):
    pass


class UserSearch(Search):
    # TikTokDownloader 把要求滑块验证的响应当成空结果，这里单独拦下，区分「搜不到」和「被要求验证」
    def check_response(self, data_dict: dict, *args, **kwargs):
        if (data_dict.get("search_nil_info") or {}).get("search_nil_type") == "verify_check":
            raise VerifyCheck(self.keyword)
        return super().check_response(data_dict, *args, **kwargs)


async def main(args) -> None:
    out = CWD / args.out_dir
    store = Store(out)
    path = out / "resolved.jsonl"
    done = {json.loads(line)["dy_id"] for line in path.open()} if path.exists() else set()
    ids = [line.strip() for line in (CWD / args.ids).read_text().splitlines() if line.strip()]
    names = json.loads((CWD / args.names).read_text()) if args.names else None

    cookie = COOKIE
    verify = [str(Path(__file__).resolve().parent.parent / ".venv/bin/python"),  # verify_session.py 要 Playwright
              str(Path(__file__).resolve().parent / "verify_session.py"), "douyin", COOKIE_FILE]
    async with Params() as params:
        for dy_id in ids:
            if dy_id in done:
                continue
            keyword = names[dy_id]["name"] if names else dy_id
            try:
                data = await UserSearch(params, cookie=cookie, keyword=keyword, channel=2, pages=1).run()
            except VerifyCheck:
                print(json.dumps({"verify_check": dy_id}), flush=True)
                if subprocess.run([*verify, f"https://www.douyin.com/search/{keyword}?type=user"]).returncode:
                    print(json.dumps({"stop": "not verified", "dy_id": dy_id}), flush=True)
                    return
                cookie = json.loads(Path(COOKIE_FILE).read_text())["cookie_str"]
                data = await UserSearch(params, cookie=cookie, keyword=keyword, channel=2, pages=1).run()
            items = [i for i in data if isinstance(i, dict)]  # 没结果时 TikTokDownloader 返回 [[]]
            store.raw("search_user", dy_id, items, query=keyword)
            hit, candidates = match(dy_id, items)
            if names and not hit:
                hit = next((u for i in items if ((u := i.get("user_info") or {}).get("nickname") or "").strip()
                            == keyword.strip()), None)
            row = {"dy_id": dy_id, "query": keyword, "resolved": bool(hit), "sec_uid": hit and hit["sec_uid"],
                   "nickname": hit and hit.get("nickname"), "candidates": candidates,
                   "fetched_at": datetime.now().isoformat(timespec="seconds")}
            store.row("resolved.jsonl", row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            await asyncio.sleep(random.uniform(args.gap, 2 * args.gap))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True)
    ap.add_argument("--out-dir", default="data/dy_resolve")
    ap.add_argument("--names", help="抖音号 → {name: 账号名称} 的 JSON；给了就按名称搜")
    ap.add_argument("--gap", type=float, default=60, help="相邻两次搜索的间隔秒数下限，实际在 gap~2×gap 之间随机")
    asyncio.run(main(ap.parse_args()))
