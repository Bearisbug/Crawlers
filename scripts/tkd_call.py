"""调用本机 TikTokDownloader Web API（127.0.0.1:5555），自动带上抖音 cookie。

cookie 文件由环境变量 DY_COOKIE_FILE 指定（.secrets/accounts/douyin_*.json），不设则用 .secrets/douyin_cookies.json。
用法：python tkd_call.py <endpoint> '<json body>'   例：python tkd_call.py /douyin/search/user '{"keyword": "94313202879"}'
响应 JSON 原样打印到 stdout。
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

API = "http://127.0.0.1:5555"
COOKIE_FILE = os.environ.get("DY_COOKIE_FILE", str(Path(__file__).parent / ".secrets/douyin_cookies.json"))
COOKIE = json.loads(Path(COOKIE_FILE).read_text())["cookie_str"]


def call(endpoint: str, body: dict, timeout: int = 3600) -> dict:
    # 一次调用里 TikTokDownloader 会自己翻完所有页，评论上千条的作品要跑十几分钟
    req = urllib.request.Request(
        API + endpoint,
        data=json.dumps({"cookie": COOKIE, **body}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


if __name__ == "__main__":
    print(json.dumps(call(sys.argv[1], json.loads(sys.argv[2])), ensure_ascii=False))
