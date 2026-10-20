"""抖音一级评论的行格式，各评论抓取脚本共用。"""
from datetime import datetime


def ts(epoch) -> str | None:
    return datetime.fromtimestamp(epoch).isoformat(sep=" ") if epoch else None


def comment_row(aweme_id: str, c: dict) -> dict:
    u = c.get("user") or {}
    return {
        "platform": "douyin", "aweme_id": aweme_id, "cid": c.get("cid"),
        "text": c.get("text"), "create_time": ts(c.get("create_time")), "digg_count": c.get("digg_count"),
        "reply_comment_total": c.get("reply_comment_total"), "ip_label": c.get("ip_label"),
        "is_author_digged": c.get("is_author_digged"), "is_hot": c.get("is_hot"),
        "user_uid": u.get("uid"), "user_sec_uid": u.get("sec_uid"), "user_nickname": u.get("nickname"),
    }
