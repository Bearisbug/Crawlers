"""本机 Edge 扫码登录小红书 / 抖音，逐个账号把 cookie 存到 .secrets/accounts/。

用法：python scripts/login_capture.py xhs|douyin [--max 5] [--timeout 600]
每个账号用一个全新的隔离上下文（独立窗口）：检测到登录 → 保存 → 关窗 → 自动开下一个。
关掉浏览器窗口即结束；单个账号 timeout 秒内没登录也结束。只打印账号标识，不打印 cookie 值。
输出文件格式与 export_edge_cookies.py 一致：{"cookie_str", "cookies", ...}，权限 600。
"""
import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

SITES = {
    "xhs": {"url": "https://www.xiaohongshu.com/explore", "domain": "xiaohongshu.com"},
    "douyin": {"url": "https://www.douyin.com/", "domain": "douyin.com"},
}
OUT_DIR = Path(__file__).resolve().parent.parent / ".secrets/accounts"


def site_cookies(ctx, domain: str) -> list[dict]:
    return [c for c in ctx.cookies() if c["domain"].endswith(domain)]


def detect_login(platform: str, ctx, page, guest_session: str | None) -> str | None:
    """已登录则返回账号标识（小红书 user_id 或占位串），否则 None。"""
    values = {c["name"]: c["value"] for c in site_cookies(ctx, SITES[platform]["domain"])}
    if platform == "douyin":
        return "douyin" if values.get("sessionid") else None
    me = page.locator("xpath=//a[contains(@href, '/user/profile/')][.//span[text()='我']]")
    if me.count():
        return me.first.get_attribute("href").split("/user/profile/")[1].split("?")[0]
    if values.get("web_session") and values["web_session"] != guest_session:
        return "web_session-changed"
    return None


def douyin_nickname(page) -> str | None:
    try:
        page.goto("https://www.douyin.com/user/self", wait_until="domcontentloaded")
        time.sleep(4)
        return page.title().removesuffix(" - 抖音").removesuffix("的抖音")
    except PlaywrightError:
        return None


def save(platform: str, hint: str, cookies: list[dict]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(OUT_DIR.parent, 0o700)
    path = OUT_DIR / f"{platform}_{datetime.now():%Y%m%d-%H%M%S}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({
            "platform": platform, "account_hint": hint,
            "captured_at": datetime.now().isoformat(timespec="seconds"),
            "cookie_str": "; ".join(f"{c['name']}={c['value']}" for c in cookies),
            "cookies": cookies,
        }, f, ensure_ascii=False)
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("platform", choices=SITES)
    ap.add_argument("--max", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()
    site = SITES[args.platform]

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="msedge", headless=False, args=["--disable-blink-features=AutomationControlled"],
        )
        for i in range(1, args.max + 1):
            ctx = browser.new_context(viewport={"width": 1280, "height": 860}, locale="zh-CN")
            page = ctx.new_page()
            page.goto(site["url"], wait_until="domcontentloaded")
            time.sleep(5)
            guest = next((c["value"] for c in site_cookies(ctx, site["domain"]) if c["name"] == "web_session"), None)
            print(f"[{i}] 等待扫码登录 {args.platform}（关掉窗口即结束）", flush=True)

            hint, deadline = None, time.time() + args.timeout
            while time.time() < deadline and browser.is_connected() and ctx.pages:
                page = ctx.pages[-1]
                try:
                    hint = detect_login(args.platform, ctx, page, guest)
                except PlaywrightError as e:
                    # 抖音首屏会先过 JS 挑战再自动刷新，刷新瞬间读页面会抛错，跳过这一轮即可
                    print(f"[{i}] 页面切换中，继续等待：{str(e).splitlines()[0][:80]}", flush=True)
                if hint:
                    break
                time.sleep(2)
            if not hint:
                print(f"[{i}] 未登录，结束", flush=True)
                break

            time.sleep(3)  # 登录后还会补写几个 cookie
            if args.platform == "douyin":
                hint = douyin_nickname(page) or hint
            path = save(args.platform, hint, site_cookies(ctx, site["domain"]))
            print(f"[{i}] 已保存 {path.name}  账号标识={hint}", flush=True)
            ctx.close()
        try:
            browser.close()
        except PlaywrightError:
            pass  # 用户已经手动关掉了窗口


if __name__ == "__main__":
    main()
