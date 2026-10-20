"""账号被风控时，开一个带该账号 cookie 的 Edge 窗口让人过验证，过了之后把新 cookie 写回账号文件。

由 xhs_detail.mjs / dy_comments_self.py / dy_resolve.py 检测到风控时作为子进程调用，也可以手动跑：
    python scripts/verify_session.py xhs|douyin <账号文件> <要打开的网址> [--timeout 7200]
会弹 macOS 通知提醒；窗口里出现验证码就手动过，出现登录页就用同一个号重新扫码，过完关掉窗口即可。
自动判定通过：小红书——页面离开验证码 / 登录页，并且登录的是同一个号；抖音——页面里的评论或搜索接口返回了数据。
自动判定通过或窗口被关掉，都把最新 cookie 写回账号文件（权限 600）并退出码 0，调用方接着跑，还被拦会再弹；超时退出码 1。
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from login_capture import SITES, detect_login, site_cookies

DOUYIN_APIS = ("/aweme/v1/web/comment/list/", "/aweme/v1/web/discover/search/", "/aweme/v1/web/general/search/",
               "/aweme/v1/web/search/")


def notify(title: str, text: str) -> None:
    script = f"display notification {json.dumps(text, ensure_ascii=False)} with title {json.dumps(title, ensure_ascii=False)} sound name \"Glass\""
    subprocess.run(["osascript", "-e", script], check=False)


def douyin_ok(responses: list) -> bool:
    while responses:
        r = responses.pop()
        try:
            body = r.json()
        except (PlaywrightError, ValueError):
            continue
        if body.get("comments") or body.get("user_list") or body.get("data"):
            return True
    return False


def xhs_ok(ctx, page, expected: str) -> bool:
    if "website-login" in page.url or "/login" in page.url:
        return False
    hint = detect_login("xhs", ctx, page, None)
    if hint and hint not in (expected, "web_session-changed"):
        print(f"登录的是另一个号（{hint}），请用 {expected} 扫码", flush=True)
        return False
    return bool(hint)


def save(path: Path, account: dict, cookies: list[dict]) -> None:
    account.update(cookie_str="; ".join(f"{c['name']}={c['value']}" for c in cookies), cookies=cookies,
                   verified_at=datetime.now().isoformat(timespec="seconds"))
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(account, f, ensure_ascii=False)
    tmp.replace(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("platform", choices=SITES)
    ap.add_argument("account_file")
    ap.add_argument("url")
    ap.add_argument("--timeout", type=int, default=7200)
    args = ap.parse_args()
    path = Path(args.account_file)
    account = json.loads(path.read_text())
    label = account.get("account_hint") or path.stem
    domain = SITES[args.platform]["domain"]
    name = {"xhs": "小红书", "douyin": "抖音"}[args.platform]

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=False,
                                    args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(viewport={"width": 1280, "height": 860}, locale="zh-CN")
        ctx.add_cookies(account["cookies"])
        responses = []
        ctx.on("response", lambda r: responses.append(r) if any(a in r.url for a in DOUYIN_APIS) else None)
        page = ctx.new_page()
        page.goto(args.url, wait_until="domcontentloaded")
        notify(f"{name}账号被风控", f"{label}：请在弹出的窗口里过验证码（掉登录就用同一个号扫码），过完关掉窗口")
        print(f"等待验证：{name} {label}", flush=True)

        latest = site_cookies(ctx, domain)  # 用户直接关窗时浏览器已经退出，只能用最后一次读到的 cookie
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            time.sleep(3)
            try:
                if not (browser.is_connected() and ctx.pages):
                    break
                latest = site_cookies(ctx, domain)
                page = ctx.pages[-1]
                ok = douyin_ok(responses) if args.platform == "douyin" else xhs_ok(ctx, page, label)
            except PlaywrightError:
                continue  # 页面跳转或正在关窗，下一轮再看
            if ok:
                time.sleep(3)  # 验证后还会补写几个 cookie
                save(path, account, site_cookies(ctx, domain))
                print(f"已通过验证并写回 {path.name}", flush=True)
                browser.close()
                return 0
        else:
            print("超时，未通过验证", flush=True)
            browser.close()
            return 1
        save(path, account, latest)
        print(f"窗口已关闭，写回最新 cookie 到 {path.name}", flush=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
