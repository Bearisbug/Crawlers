"""从本机 Edge（macOS）导出指定站点的 cookie，供服务器上的抓取工具使用。

每个站点写一个 JSON：{"cookie_str": "k=v; ...", "cookies": [Playwright 格式]}，文件权限 600。
只打印 cookie 名字和数量，不打印值。首次运行 macOS 会弹钥匙串授权框。

用法：python export_edge_cookies.py [--profile Default] [--out .secrets]
"""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

EDGE_DIR = Path.home() / "Library/Application Support/Microsoft Edge"
SITES = {"douyin": "douyin.com", "xhs": "xiaohongshu.com"}
CHROMIUM_EPOCH_OFFSET = 11644473600


def edge_key() -> bytes:
    password = subprocess.run(
        ["security", "find-generic-password", "-w", "-s", "Microsoft Edge Safe Storage"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return hashlib.pbkdf2_hmac("sha1", password.encode(), b"saltysalt", 1003, 16)


def decrypt(blob: bytes, key: bytes, strip_domain_hash: bool) -> str:
    if not blob.startswith(b"v10"):
        return blob.decode()
    dec = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).decryptor()
    raw = dec.update(blob[3:]) + dec.finalize()
    raw = raw[: -raw[-1]]
    # Chromium 130+（meta.version >= 24）在明文前加了 32 字节的 SHA256(host_key)
    if strip_domain_hash:
        raw = raw[32:]
    return raw.decode()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="Default")
    ap.add_argument("--out", default=".secrets")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(mode=0o700, exist_ok=True)
    key = edge_key()

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "Cookies"
        shutil.copy(EDGE_DIR / args.profile / "Cookies", db)
        con = sqlite3.connect(db)
        meta_version = int(con.execute("select value from meta where key='version'").fetchone()[0])
        for site, domain in SITES.items():
            rows = con.execute(
                "select host_key, name, encrypted_value, path, expires_utc, is_secure, is_httponly "
                "from cookies where host_key like ?",
                (f"%{domain}",),
            ).fetchall()
            cookies = []
            for host, name, enc, path, expires, secure, httponly in rows:
                cookies.append({
                    "name": name,
                    "value": decrypt(enc, key, meta_version >= 24),
                    "domain": host,
                    "path": path,
                    "expires": expires / 1_000_000 - CHROMIUM_EPOCH_OFFSET if expires else -1,
                    "secure": bool(secure),
                    "httpOnly": bool(httponly),
                })
            cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
            target = out_dir / f"{site}_cookies.json"
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump({"cookie_str": cookie_str, "cookies": cookies}, f, ensure_ascii=False)
            print(f"{site}: {len(cookies)} cookies -> {target}  names: {sorted({c['name'] for c in cookies})}")


if __name__ == "__main__":
    main()
