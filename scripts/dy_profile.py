"""取抖音账号主页快照（粉丝数、作品数、获赞数），复用 TikTokDownloader 的 User 接口与签名。

用法（在 TikTokDownloader 的 venv 里跑）：python dy_profile.py <sec_uid> [sec_uid ...]
输出 JSON Lines：{sec_uid, nickname, unique_id, follower_count, aweme_count, total_favorited}
"""
import asyncio
import json
import os
import sys
from pathlib import Path

from tkd_call import COOKIE  # 与 Web API 调用共用同一个 DY_COOKIE_FILE

REPO = Path(os.environ.get("TKD_REPO", Path.home() / "crawlers/TikTokDownloader"))

os.chdir(REPO)
sys.path.insert(0, str(REPO))

from src.interface.user import User  # noqa: E402
from src.testers import Params  # noqa: E402

FIELDS = ("nickname", "unique_id", "follower_count", "aweme_count", "total_favorited")


async def main(sec_uids: list[str]) -> None:
    async with Params() as params:
        for sec_uid in sec_uids:
            user = await User(params, cookie=COOKIE, sec_user_id=sec_uid).run() or {}
            print(json.dumps({"sec_uid": sec_uid, **{k: user.get(k) for k in FIELDS}}, ensure_ascii=False))
            await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
