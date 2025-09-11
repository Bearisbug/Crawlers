#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
小红书 PC 请求合集
"""

from __future__ import annotations

import json
import math
import random
from typing import Dict, Optional, Tuple
import re
from urllib.parse import urlparse, parse_qsl, quote as url_quote

import requests
import execjs


# ========= Utils =========
def trans_cookies(cookies_str: str) -> Dict[str, str]:
    """将 Cookie 字符串转为字典。"""
    if not isinstance(cookies_str, str):
        return {}
    if '; ' in cookies_str:
        parts = cookies_str.split('; ')
    else:
        parts = cookies_str.split(';')
    out = {}
    for it in parts:
        if not it:
            continue
        k, _, v = it.partition('=')
        if k:
            out[k.strip()] = v
    return out


def splice_str(api: str, params: Dict[str, str | int | None]) -> str:
    if not params:
        return api
    segs = []
    for k, v in params.items():
        v = '' if v is None else str(v)
        segs.append(f"{k}={v}")
    return api + '?' + '&'.join(segs)


def generate_x_b3_traceid(length: int = 16) -> str:
    chars = "abcdef0123456789"
    return ''.join(random.choice(chars) for _ in range(length))


def get_common_headers() -> Dict[str, str]:
    """通用请求头模板（针对 edith.xiaohongshu.com）。"""
    return {
        "authority": "edith.xiaohongshu.com",
        "accept": "application/json, text/plain, */*",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
        "cache-control": "no-cache",
        "content-type": "application/json;charset=UTF-8",
        "origin": "https://www.xiaohongshu.com",
        "pragma": "no-cache",
        "referer": "https://www.xiaohongshu.com/",
        "sec-ch-ua": '"Not A(Brand";v="99", "Microsoft Edge";v="121", "Chromium";v="121"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
        "x-b3-traceid": "",  # 动态填充
        "x-mns": "unload",
        "x-s": "",
        "x-s-common": "",
        "x-t": "",
        "x-xray-traceid": "",  # 动态填充
    }


def _load_js_engines():
    """编译签名/trace 的 JS（相对路径，向上一级或当前 static）。"""
    try:
        js1 = execjs.compile(open(r'../js/xhs/xhs_xs_xsc_56.js', 'r', encoding='utf-8').read())
    except Exception:
        js1 = execjs.compile(open(r'js/xhs/xhs_xs_xsc_56.js', 'r', encoding='utf-8').read())
    try:
        js2 = execjs.compile(open(r'../js/xhs/xhs_xray.js', 'r', encoding='utf-8').read())
    except Exception:
        js2 = execjs.compile(open(r'js/xhs/xhs_xray.js', 'r', encoding='utf-8').read())
    return js1, js2


_XS_JS, _XRAY_JS = _load_js_engines()


def generate_xs_xs_common(a1: str, api: str, data: str | dict | None = '') -> Tuple[str, int, str]:
    """调用 JS 计算 xs/xt/xs_common"""
    d = '' if data is None else data
    ret = _XS_JS.call('get_request_headers_params', api, d, a1)
    return ret['xs'], ret['xt'], ret['xs_common']


def generate_xs(a1: str, api: str, data: str | dict | None = '') -> Tuple[str, int]:
    d = '' if data is None else data
    ret = _XS_JS.call('get_xs', api, d, a1)
    return ret['X-s'], ret['X-t']


def generate_xray_traceid() -> str:
    return _XRAY_JS.call('traceId')


def generate_headers(a1: str, api: str, data: str | dict | None = '') -> Tuple[Dict[str, str], Optional[str]]:
    xs, xt, xs_common = generate_xs_xs_common(a1, api, data or '')
    x_b3_traceid = generate_x_b3_traceid()
    headers = get_common_headers()
    headers['x-s'] = xs
    headers['x-t'] = str(xt)
    headers['x-s-common'] = xs_common
    headers['x-b3-traceid'] = x_b3_traceid
    headers['x-xray-traceid'] = generate_xray_traceid()
    if data:
        data_json = json.dumps(data, separators=(',', ':'), ensure_ascii=False) if isinstance(data, dict) else str(data)
    else:
        data_json = None
    return headers, data_json


def generate_request_params(cookies_str: str, api: str, data: Optional[dict | str] = None) -> Tuple[Dict[str, str], Dict[str, str], Optional[str]]:
    """组装请求参数并计算签名，返回 (headers, cookies, data_json)。"""
    cookies = trans_cookies(cookies_str or '')
    a1 = cookies.get('a1', '')
    headers, data_json = generate_headers(a1, api, data)
    return headers, cookies, data_json


def _note_id_from_url(note_url: str) -> str:
    p = urlparse(note_url)
    return p.path.split('/')[-1]


def _kv_from_url(url: str) -> Dict[str, str]:
    try:
        p = urlparse(url)
        return dict(parse_qsl(p.query, keep_blank_values=True))
    except Exception:
        return {}


# ========= 核心请求函数 =========
class Apis:
    def __init__(self):
        self.base_url = "https://edith.xiaohongshu.com"

    # --- 辅助打印 ---
    def _print_request(self, method: str, url: str, headers: Dict[str, str], data: Optional[str] = None):
        print(f"\n>>> {method} {url}")
        # 仅打印关键头
        hlines = [
            f"User-Agent: {headers.get('user-agent','')}",
            f"Referer: {headers.get('referer','')}",
            f"x-b3-traceid: {headers.get('x-b3-traceid','')}",
        ]
        print("Headers:\n  " + "\n  ".join(hlines))
        if data is not None:
            # 打印完整请求体
            body_str = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
            print(f"Body: {body_str}")

    # --- 获取个人信息（健康检查） ---
    def get_user_self_info(self, cookies_str: str, proxies: Optional[Dict] = None):
        try:
            api = "/api/sns/web/v1/user/selfinfo"
            headers, cookies, _ = generate_request_params(cookies_str, api)
            full_url = self.base_url + api
            self._print_request("GET", full_url, headers)
            resp = requests.get(full_url, headers=headers, cookies=cookies, proxies=proxies, timeout=20)
            text = resp.text
            print(f"Status: {resp.status_code}\nResponse:\n{text}")
            res_json = None
            try:
                res_json = resp.json()
                success = bool(res_json.get("success", False)) or int(res_json.get("code", -1)) == 0
                msg = res_json.get("msg") or "ok"
            except Exception:
                success, msg = False, "not json"
            return success, msg, res_json
        except Exception as e:
            return False, str(e), None

    # --- 笔记详情 ---
    def get_note_info(self, url: str, cookies_str: str, proxies: Optional[Dict] = None):
        try:
            url_kv = _kv_from_url(url)
            note_id = _note_id_from_url(url)
            api = "/api/sns/web/v1/feed"
            body = {
                "source_note_id": note_id,
                "image_formats": ["jpg", "webp", "avif"],
                "extra": {"need_body_topic": "1"},
                "xsec_source": url_kv.get('xsec_source', 'pc_search'),
                "xsec_token": url_kv.get('xsec_token', ''),
            }
            if not body.get("xsec_token"):
                return False, "missing xsec_token in note url", None
            headers, cookies, data = generate_request_params(cookies_str, api, body)
            full_url = self.base_url + api
            self._print_request("POST", full_url, headers, data)
            resp = requests.post(full_url, headers=headers, data=data, cookies=cookies, proxies=proxies, timeout=20)
            text = resp.text
            print(f"Status: {resp.status_code}\nResponse:\n{text}")
            res_json = None
            try:
                res_json = resp.json()
                success = bool(res_json.get("success", False)) or int(res_json.get("code", -1)) == 0
                msg = res_json.get("msg") or "ok"
            except Exception:
                success, msg = False, "not json"
            return success, msg, res_json
        except Exception as e:
            return False, str(e), None

    # --- 用户主页作品列表（单页） ---
    def get_user_note_info(self, user_id: str, cursor: str, cookies_str: str,
                           xsec_token: str = '', xsec_source: str = 'pc_search',
                           proxies: Optional[Dict] = None):
        try:
            api = "/api/sns/web/v1/user_posted"
            params = {
                "num": "30",
                "cursor": cursor,
                "user_id": user_id,
                "image_formats": "jpg,webp,avif",
                "xsec_token": xsec_token,
                "xsec_source": xsec_source,
            }
            splice_api = splice_str(api, params)
            headers, cookies, _ = generate_request_params(cookies_str, splice_api)
            full_url = self.base_url + splice_api
            self._print_request("GET", full_url, headers)
            resp = requests.get(full_url, headers=headers, cookies=cookies, proxies=proxies, timeout=20)
            text = resp.text
            print(f"Status: {resp.status_code}\nResponse:\n{text}")
            res_json = None
            try:
                res_json = resp.json()
                success = bool(res_json.get("success", False)) or int(res_json.get("code", -1)) == 0
                msg = res_json.get("msg") or "ok"
            except Exception:
                success, msg = False, "not json"
            return success, msg, res_json
        except Exception as e:
            return False, str(e), None

    # --- 拉取用户全部作品 ---
    def get_user_all_notes(self, user_url: str, cookies_str: str, proxies: Optional[Dict] = None):
        try:
            p = urlparse(user_url)
            user_id = p.path.split('/')[-1]
            kv = dict(parse_qsl(p.query, keep_blank_values=True))
            xsec_token = kv.get('xsec_token', '')
            xsec_source = kv.get('xsec_source', 'pc_search')

            cursor = ''
            note_list = []
            while True:
                ok, msg, res = self.get_user_note_info(user_id, cursor, cookies_str, xsec_token, xsec_source, proxies)
                if not ok or not isinstance(res, dict):
                    return False, msg, note_list
                data = res.get('data') or {}
                notes = data.get('notes') or []
                note_list.extend(notes)
                if 'cursor' in data:
                    cursor = str(data.get('cursor') or '')
                else:
                    break
                if not notes or not data.get('has_more'):
                    break
            return True, 'ok', note_list
        except Exception as e:
            return False, str(e), []

    # --- 其他用户信息 ---
    def get_user_info(self, user_id: str, cookies_str: str, proxies: Optional[Dict] = None):
        try:
            api = "/api/sns/web/v1/user/otherinfo"
            params = {"target_user_id": user_id}
            splice_api = splice_str(api, params)
            headers, cookies, _ = generate_request_params(cookies_str, splice_api)
            full_url = self.base_url + splice_api
            self._print_request("GET", full_url, headers)
            resp = requests.get(full_url, headers=headers, cookies=cookies, proxies=proxies, timeout=20)
            text = resp.text
            print(f"Status: {resp.status_code}\nResponse:\n{text}")
            res_json = None
            try:
                res_json = resp.json()
                success = bool(res_json.get("success", False)) or int(res_json.get("code", -1)) == 0
                msg = res_json.get("msg") or "ok"
            except Exception:
                success, msg = False, "not json"
            return success, msg, res_json
        except Exception as e:
            return False, str(e), None

    # --- 顶层评论分页 ---
    def get_note_out_comment(self, note_id: str, cursor: str, xsec_token: str,
                              cookies_str: str, proxies: Optional[Dict] = None):
        try:
            api = "/api/sns/web/v2/comment/page"
            params = {
                "note_id": note_id,
                "cursor": cursor,
                "top_comment_id": "",
                "image_formats": "jpg,webp,avif",
                "xsec_token": xsec_token,
                "xsec_source": 'pc_feed',
            }
            splice_api = splice_str(api, params)
            headers, cookies, _ = generate_request_params(cookies_str, splice_api)
            full_url = self.base_url + splice_api
            self._print_request("GET", full_url, headers)
            resp = requests.get(full_url, headers=headers, cookies=cookies, proxies=proxies, timeout=20)
            text = resp.text
            print(f"Status: {resp.status_code}\nResponse:\n{text}")
            res_json = None
            try:
                res_json = resp.json()
                code = int(res_json.get("code", -1))
                success = bool(res_json.get("success", False)) and (code == 0)
                msg = res_json.get("msg") or "ok"
            except Exception:
                success, msg = False, "not json"
            return success, msg, res_json
        except Exception as e:
            return False, str(e), None

    def get_note_all_out_comment(self, note_id: str, xsec_token: str, cookies_str: str,
                                  proxies: Optional[Dict] = None, max_pages: int = 200):
        cursor = ""
        out_list = []
        pages = 0
        while True:
            pages += 1
            if pages > max_pages:
                print(f"[guard] too many out pages ({max_pages}), break")
                break
            ok, msg, res = self.get_note_out_comment(note_id, cursor, xsec_token, cookies_str, proxies)
            if not ok or not isinstance(res, dict):
                return False, msg, {"comments": out_list}
            data = res.get('data') or {}
            comments = data.get('comments') or []
            out_list.extend(comments)
            new_cursor = str(data.get('cursor') or '') if 'cursor' in data else ''
            has_more = bool(data.get('has_more'))
            print(f"out-page: got={len(comments)}, total={len(out_list)}, has_more={has_more}, cursor='{new_cursor}'")
            if not comments or not has_more:
                break
            if new_cursor == cursor:
                print("[guard] cursor stalled, break")
                break
            cursor = new_cursor
        return True, 'ok', {"comments": out_list}

    # --- 子评论分页 ---
    def get_note_inner_comment(self, comment: Dict, cursor: str, xsec_token: str,
                               cookies_str: str, proxies: Optional[Dict] = None):
        try:
            api = "/api/sns/web/v2/comment/sub/page"
            params = {
                "note_id": comment.get('note_id', ''),
                "root_comment_id": comment.get('id', ''),
                "num": "10",
                "cursor": cursor,
                "image_formats": "jpg,webp,avif",
                "top_comment_id": '',
                "xsec_token": xsec_token,
                "xsec_source": 'pc_feed',
            }
            splice_api = splice_str(api, params)
            headers, cookies, _ = generate_request_params(cookies_str, splice_api)
            full_url = self.base_url + splice_api
            self._print_request("GET", full_url, headers)
            resp = requests.get(full_url, headers=headers, cookies=cookies, proxies=proxies, timeout=20)
            text = resp.text
            print(f"Status: {resp.status_code}\nResponse:\n{text}")
            res_json = None
            try:
                res_json = resp.json()
                code = int(res_json.get("code", -1))
                success = bool(res_json.get("success", False)) and (code == 0)
                msg = res_json.get("msg") or "ok"
            except Exception:
                success, msg = False, "not json"
            return success, msg, res_json
        except Exception as e:
            return False, str(e), None

    def get_note_all_inner_comment(self, comment: Dict, xsec_token: str, cookies_str: str,
                                   proxies: Optional[Dict] = None, max_pages: int = 200):
        cursor = comment.get("sub_comment_cursor", "") or ""
        inner = list(comment.get("sub_comments") or [])
        pages = 0
        while True:
            pages += 1
            if pages > max_pages:
                print(f"[guard] too many sub pages ({max_pages}), break root={comment.get('id')}")
                break
            ok, msg, res = self.get_note_inner_comment(comment, cursor, xsec_token, cookies_str, proxies)
            if not ok or not isinstance(res, dict):
                # 返回已有进度
                comment["sub_comments"] = inner
                return False, msg, comment
            data = res.get('data') or {}
            cms = data.get('comments') or []
            inner.extend(cms)
            new_cursor = str(data.get('cursor') or '') if 'cursor' in data else ''
            has_more = bool(data.get('has_more'))
            print(f"sub-page root={comment.get('id')}: got={len(cms)}, total={len(inner)}, has_more={has_more}, cursor='{new_cursor}'")
            if not cms or not has_more:
                break
            if new_cursor == cursor:
                print("[guard] sub cursor stalled, break")
                break
            cursor = new_cursor
        comment["sub_comments"] = inner
        return True, 'ok', comment

    def get_note_all_comment(self, url: str, cookies_str: str,
                                proxies: Optional[Dict] = None):
        try:
            p = urlparse(url)
            note_id = p.path.split('/')[-1]
            kv = dict(parse_qsl(p.query, keep_blank_values=True))
            xsec_token = kv.get('xsec_token', '')
            if not note_id:
                return False, "missing note_id", []

            ok, msg, out_payload = self.get_note_all_out_comment(note_id, xsec_token, cookies_str, proxies)
            if not ok or not isinstance(out_payload, dict):
                return False, msg, []
            roots = list((out_payload or {}).get('comments') or [])
            print(f"total roots: {len(roots)}")

            final = []
            for r in roots:
                # 确保 note_id 字段存在
                r.setdefault('note_id', note_id)
                # 如果有子评标记，拉一下子评
                sub_need = bool(r.get('sub_comment_has_more')) or int(r.get('sub_comment_count', 0) or 0) > 0
                if sub_need:
                    ok2, msg2, rr = self.get_note_all_inner_comment(r, xsec_token, cookies_str, proxies)
                    final.append(rr)
                else:
                    final.append(r)

            print(f"final roots(with subs): {len(final)}")
            return True, 'ok', final
        except Exception as e:
            return False, str(e), []

    # --- 首页频道列表 ---
    def get_homefeed_all_channel(self, cookies_str: str, proxies: Optional[Dict] = None):
        try:
            api = "/api/sns/web/v1/homefeed/category"
            headers, cookies, _ = generate_request_params(cookies_str, api)
            full_url = self.base_url + api
            self._print_request("GET", full_url, headers)
            resp = requests.get(full_url, headers=headers, cookies=cookies, proxies=proxies, timeout=20)
            text = resp.text
            print(f"Status: {resp.status_code}\nResponse:\n{text}")
            res_json = None
            try:
                res_json = resp.json()
                success = bool(res_json.get("success", False))
                msg = res_json.get("msg") or "ok"
            except Exception:
                success, msg = False, "not json"
            return success, msg, res_json
        except Exception as e:
            return False, str(e), None

    # --- 首页推荐 ---
    def get_homefeed_recommend(self, category: str, cursor_score: str, refresh_type: int, note_index: int,
                               cookies_str: str, proxies: Optional[Dict] = None):
        try:
            api = "/api/sns/web/v1/homefeed"
            body = {
                "cursor_score": cursor_score,
                "num": 20,
                "refresh_type": refresh_type,
                "note_index": note_index,
                "unread_begin_note_id": "",
                "unread_end_note_id": "",
                "unread_note_count": 0,
                "category": category,
                "search_key": "",
                "need_num": 10,
                "image_formats": ["jpg", "webp", "avif"],
                "need_filter_image": False,
            }
            headers, cookies, data = generate_request_params(cookies_str, api, body)
            full_url = self.base_url + api
            self._print_request("POST", full_url, headers, data)
            resp = requests.post(full_url, headers=headers, data=data, cookies=cookies, proxies=proxies, timeout=20)
            text = resp.text
            print(f"Status: {resp.status_code}\nResponse:\n{text}")
            res_json = None
            try:
                res_json = resp.json()
                success = bool(res_json.get("success", False))
                msg = res_json.get("msg") or "ok"
            except Exception:
                success, msg = False, "not json"
            return success, msg, res_json
        except Exception as e:
            return False, str(e), None

    def get_homefeed_recommend_by_num(self, category: str, require_num: int,
                                      cookies_str: str, proxies: Optional[Dict] = None):
        cursor_score, refresh_type, note_index = "", 1, 0
        note_list = []
        try:
            while True:
                ok, msg, res = self.get_homefeed_recommend(category, cursor_score, refresh_type, note_index, cookies_str, proxies)
                if not ok:
                    return False, msg, note_list
                data = (res or {}).get("data") or {}
                items = data.get("items") or []
                note_list.extend(items)
                cursor_score = str(data.get("cursor_score", ""))
                refresh_type = 3
                note_index += 20
                if not items or len(note_list) >= require_num:
                    break
            if len(note_list) > require_num:
                note_list = note_list[:require_num]
            return True, "ok", note_list
        except Exception as e:
            return False, str(e), note_list

    # --- 自身信息 v2 ---
    def get_user_self_info2(self, cookies_str: str, proxies: Optional[Dict] = None):
        try:
            api = "/api/sns/web/v2/user/me"
            headers, cookies, _ = generate_request_params(cookies_str, api)
            full_url = self.base_url + api
            self._print_request("GET", full_url, headers)
            resp = requests.get(full_url, headers=headers, cookies=cookies, proxies=proxies, timeout=20)
            text = resp.text
            print(f"Status: {resp.status_code}\nResponse:\n{text}")
            res_json = None
            try:
                res_json = resp.json()
                success = bool(res_json.get("success", False))
                msg = res_json.get("msg") or "ok"
            except Exception:
                success, msg = False, "not json"
            return success, msg, res_json
        except Exception as e:
            return False, str(e), None

    # --- 用户喜欢/收藏 ---
    def get_user_like_note_info(self, user_id: str, cursor: str, cookies_str: str,
                                xsec_token: str = '', xsec_source: str = '', proxies: Optional[Dict] = None):
        try:
            api = "/api/sns/web/v1/note/like/page"
            params = {
                "num": "30",
                "cursor": cursor,
                "user_id": user_id,
                "image_formats": "jpg,webp,avif",
                "xsec_token": xsec_token,
                "xsec_source": xsec_source,
            }
            splice_api = splice_str(api, params)
            headers, cookies, _ = generate_request_params(cookies_str, splice_api)
            full_url = self.base_url + splice_api
            self._print_request("GET", full_url, headers)
            resp = requests.get(full_url, headers=headers, cookies=cookies, proxies=proxies, timeout=20)
            text = resp.text
            print(f"Status: {resp.status_code}\nResponse:\n{text}")
            res_json = None
            try:
                res_json = resp.json()
                success = bool(res_json.get("success", False))
                msg = res_json.get("msg") or "ok"
            except Exception:
                success, msg = False, "not json"
            return success, msg, res_json
        except Exception as e:
            return False, str(e), None

    def get_user_all_like_note_info(self, user_url: str, cookies_str: str, proxies: Optional[Dict] = None):
        cursor = ''
        note_list = []
        try:
            p = urlparse(user_url)
            user_id = p.path.split('/')[-1]
            kv = dict(parse_qsl(p.query, keep_blank_values=True))
            xsec_token = kv.get('xsec_token', '')
            xsec_source = kv.get('xsec_source', 'pc_user')
            while True:
                ok, msg, res = self.get_user_like_note_info(user_id, cursor, cookies_str, xsec_token, xsec_source, proxies)
                if not ok:
                    return False, msg, note_list
                data = (res or {}).get('data') or {}
                notes = data.get('notes') or []
                note_list.extend(notes)
                if 'cursor' in data:
                    cursor = str(data.get('cursor') or '')
                else:
                    break
                if not notes or not data.get('has_more'):
                    break
            return True, 'ok', note_list
        except Exception as e:
            return False, str(e), note_list

    def get_user_collect_note_info(self, user_id: str, cursor: str, cookies_str: str,
                                   xsec_token: str = '', xsec_source: str = '', proxies: Optional[Dict] = None):
        try:
            api = "/api/sns/web/v2/note/collect/page"
            params = {
                "num": "30",
                "cursor": cursor,
                "user_id": user_id,
                "image_formats": "jpg,webp,avif",
                "xsec_token": xsec_token,
                "xsec_source": xsec_source,
            }
            splice_api = splice_str(api, params)
            headers, cookies, _ = generate_request_params(cookies_str, splice_api)
            full_url = self.base_url + splice_api
            self._print_request("GET", full_url, headers)
            resp = requests.get(full_url, headers=headers, cookies=cookies, proxies=proxies, timeout=20)
            text = resp.text
            print(f"Status: {resp.status_code}\nResponse:\n{text}")
            res_json = None
            try:
                res_json = resp.json()
                success = bool(res_json.get("success", False))
                msg = res_json.get("msg") or "ok"
            except Exception:
                success, msg = False, "not json"
            return success, msg, res_json
        except Exception as e:
            return False, str(e), None

    def get_user_all_collect_note_info(self, user_url: str, cookies_str: str, proxies: Optional[Dict] = None):
        cursor = ''
        note_list = []
        try:
            p = urlparse(user_url)
            user_id = p.path.split('/')[-1]
            kv = dict(parse_qsl(p.query, keep_blank_values=True))
            xsec_token = kv.get('xsec_token', '')
            xsec_source = kv.get('xsec_source', 'pc_search')
            while True:
                ok, msg, res = self.get_user_collect_note_info(user_id, cursor, cookies_str, xsec_token, xsec_source, proxies)
                if not ok:
                    return False, msg, note_list
                data = (res or {}).get('data') or {}
                notes = data.get('notes') or []
                note_list.extend(notes)
                if 'cursor' in data:
                    cursor = str(data.get('cursor') or '')
                else:
                    break
                if not notes or not data.get('has_more'):
                    break
            return True, 'ok', note_list
        except Exception as e:
            return False, str(e), note_list

    # --- 搜索关键词推荐 ---
    def get_search_keyword(self, word: str, cookies_str: str, proxies: Optional[Dict] = None):
        try:
            api = "/api/sns/web/v1/search/recommend"
            params = {"keyword": url_quote(word)}
            splice_api = splice_str(api, params)
            headers, cookies, _ = generate_request_params(cookies_str, splice_api)
            full_url = self.base_url + splice_api
            self._print_request("GET", full_url, headers)
            resp = requests.get(full_url, headers=headers, cookies=cookies, proxies=proxies, timeout=20)
            text = resp.text
            print(f"Status: {resp.status_code}\nResponse:\n{text}")
            res_json = None
            try:
                res_json = resp.json()
                success = bool(res_json.get("success", False))
                msg = res_json.get("msg") or "ok"
            except Exception:
                success, msg = False, "not json"
            return success, msg, res_json
        except Exception as e:
            return False, str(e), None

    # --- 按页搜索 ---
    def search_note(self, query: str, cookies_str: str, page: int = 1, sort_type_choice: int = 0,
                    note_type: int = 0, note_time: int = 0, note_range: int = 0, pos_distance: int = 0,
                    geo: Optional[dict] = None, proxies: Optional[Dict] = None):
        def _tag(val_map: Dict[int, str], v: int, default: str) -> str:
            return val_map.get(int(v or 0), default)

        sort_tag = _tag({1: "time_descending", 2: "popularity_descending", 3: "comment_descending", 4: "collect_descending"}, sort_type_choice, "general")
        type_tag = _tag({1: "视频笔记", 2: "普通笔记"}, note_type, "不限")
        time_tag = _tag({1: "一天内", 2: "一周内", 3: "半年内"}, note_time, "不限")
        range_tag = _tag({1: "已看过", 2: "未看过", 3: "已关注"}, note_range, "不限")
        dist_tag = _tag({1: "同城", 2: "附近"}, pos_distance, "不限")
        try:
            api = "/api/sns/web/v1/search/notes"
            body = {
                "keyword": query,
                "page": int(page or 1),
                "page_size": 20,
                "search_id": generate_x_b3_traceid(21),
                "sort": "general",
                "note_type": 0,
                "ext_flags": [],
                "filters": [
                    {"tags": [sort_tag], "type": "sort_type"},
                    {"tags": [type_tag], "type": "filter_note_type"},
                    {"tags": [time_tag], "type": "filter_note_time"},
                    {"tags": [range_tag], "type": "filter_note_range"},
                    {"tags": [dist_tag], "type": "filter_pos_distance"},
                ],
                "geo": geo or "",
                "image_formats": ["jpg", "webp", "avif"],
            }
            headers, cookies, data = generate_request_params(cookies_str, api, body)
            full_url = self.base_url + api
            headers["referer"] = f"https://www.xiaohongshu.com/search_result?keyword={url_quote(query)}"
            self._print_request("POST", full_url, headers, data)
            resp = requests.post(full_url, headers=headers, data=data.encode('utf-8'), cookies=cookies, proxies=proxies, timeout=20)
            text = resp.text
            print(f"Status: {resp.status_code}\nResponse:\n{text}")
            res_json = None
            try:
                res_json = resp.json()
                success = bool(res_json.get("success", False))
                msg = res_json.get("msg") or "ok"
            except Exception:
                success, msg = False, "not json"
            return success, msg, res_json
        except Exception as e:
            return False, str(e), None

    def search_some_note(self, query: str, require_num: int, cookies_str: str,
                             sort_type_choice: int = 0, note_type: int = 0, note_time: int = 0,
                             note_range: int = 0, pos_distance: int = 0, geo: Optional[dict] = None,
                             proxies: Optional[Dict] = None):
        page = 1
        note_list = []
        try:
            while True:
                ok, msg, res = self.search_note(query, cookies_str, page, sort_type_choice, note_type, note_time, note_range, pos_distance, geo, proxies)
                if not ok:
                    return False, msg, note_list
                data = (res or {}).get('data') or {}
                items = data.get('items') or []
                note_list.extend(items)
                page += 1
                if not items or len(note_list) >= require_num or not data.get('has_more'):
                    break
            if len(note_list) > require_num:
                note_list = note_list[:require_num]
            return True, 'ok', note_list
        except Exception as e:
            return False, str(e), note_list

    # --- 站内消息 ---
    def get_unread_message(self, cookies_str: str, proxies: Optional[Dict] = None):
        try:
            api = "/api/sns/web/unread_count"
            headers, cookies, _ = generate_request_params(cookies_str, api)
            full_url = self.base_url + api
            self._print_request("GET", full_url, headers)
            resp = requests.get(full_url, headers=headers, cookies=cookies, proxies=proxies, timeout=20)
            text = resp.text
            print(f"Status: {resp.status_code}\nResponse:\n{text}")
            res_json = None
            try:
                res_json = resp.json()
                success = bool(res_json.get("success", False))
                msg = res_json.get("msg") or "ok"
            except Exception:
                success, msg = False, "not json"
            return success, msg, res_json
        except Exception as e:
            return False, str(e), None

    def get_metions(self, cursor: str, cookies_str: str, proxies: Optional[Dict] = None):
        try:
            api = "/api/sns/web/v1/you/mentions"
            params = {"num": "20", "cursor": cursor}
            splice_api = splice_str(api, params)
            headers, cookies, _ = generate_request_params(cookies_str, splice_api)
            full_url = self.base_url + splice_api
            self._print_request("GET", full_url, headers)
            resp = requests.get(full_url, headers=headers, cookies=cookies, proxies=proxies, timeout=20)
            text = resp.text
            print(f"Status: {resp.status_code}\nResponse:\n{text}")
            res_json = None
            try:
                res_json = resp.json()
                success = bool(res_json.get("success", False))
                msg = res_json.get("msg") or "ok"
            except Exception:
                success, msg = False, "not json"
            return success, msg, res_json
        except Exception as e:
            return False, str(e), None

    def get_all_metions(self, cookies_str: str, proxies: Optional[Dict] = None):
        cursor = ''
        items = []
        try:
            while True:
                ok, msg, res = self.get_metions(cursor, cookies_str, proxies)
                if not ok:
                    return False, msg, items
                data = (res or {}).get('data') or {}
                arr = data.get('message_list') or []
                items.extend(arr)
                if 'cursor' in data:
                    cursor = str(data.get('cursor') or '')
                else:
                    break
                if not arr or not data.get('has_more'):
                    break
            return True, 'ok', items
        except Exception as e:
            return False, str(e), items

    def get_likesAndcollects(self, cursor: str, cookies_str: str, proxies: Optional[Dict] = None):
        try:
            api = "/api/sns/web/v1/you/likes"
            params = {"num": "20", "cursor": cursor}
            splice_api = splice_str(api, params)
            headers, cookies, _ = generate_request_params(cookies_str, splice_api)
            full_url = self.base_url + splice_api
            self._print_request("GET", full_url, headers)
            resp = requests.get(full_url, headers=headers, cookies=cookies, proxies=proxies, timeout=20)
            text = resp.text
            print(f"Status: {resp.status_code}\nResponse:\n{text}")
            res_json = None
            try:
                res_json = resp.json()
                success = bool(res_json.get("success", False))
                msg = res_json.get("msg") or "ok"
            except Exception:
                success, msg = False, "not json"
            return success, msg, res_json
        except Exception as e:
            return False, str(e), None

    def get_all_likesAndcollects(self, cookies_str: str, proxies: Optional[Dict] = None):
        cursor = ''
        items = []
        try:
            while True:
                ok, msg, res = self.get_likesAndcollects(cursor, cookies_str, proxies)
                if not ok:
                    return False, msg, items
                data = (res or {}).get('data') or {}
                arr = data.get('message_list') or []
                items.extend(arr)
                if 'cursor' in data:
                    cursor = str(data.get('cursor') or '')
                else:
                    break
                if not arr or not data.get('has_more'):
                    break
            return True, 'ok', items
        except Exception as e:
            return False, str(e), items

    def get_new_connections(self, cursor: str, cookies_str: str, proxies: Optional[Dict] = None):
        try:
            api = "/api/sns/web/v1/you/connections"
            params = {"num": "20", "cursor": cursor}
            splice_api = splice_str(api, params)
            headers, cookies, _ = generate_request_params(cookies_str, splice_api)
            full_url = self.base_url + splice_api
            self._print_request("GET", full_url, headers)
            resp = requests.get(full_url, headers=headers, cookies=cookies, proxies=proxies, timeout=20)
            text = resp.text
            print(f"Status: {resp.status_code}\nResponse:\n{text}")
            res_json = None
            try:
                res_json = resp.json()
                success = bool(res_json.get("success", False))
                msg = res_json.get("msg") or "ok"
            except Exception:
                success, msg = False, "not json"
            return success, msg, res_json
        except Exception as e:
            return False, str(e), None

    def get_all_new_connections(self, cookies_str: str, proxies: Optional[Dict] = None):
        cursor = ''
        items = []
        try:
            while True:
                ok, msg, res = self.get_new_connections(cursor, cookies_str, proxies)
                if not ok:
                    return False, msg, items
                data = (res or {}).get('data') or {}
                arr = data.get('message_list') or []
                items.extend(arr)
                if 'cursor' in data:
                    cursor = str(data.get('cursor') or '')
                else:
                    break
                if not arr or not data.get('has_more'):
                    break
            return True, 'ok', items
        except Exception as e:
            return False, str(e), items

    # --- 无水印资源（网页解析） ---
    @staticmethod
    def _get_www_headers() -> Dict[str, str]:
        return {
            "authority": "www.xiaohongshu.com",
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "accept-language": "zh-CN,zh;q=0.9",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "referer": "https://www.xiaohongshu.com/",
            "sec-ch-ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "same-origin",
            "sec-fetch-user": "?1",
            "upgrade-insecure-requests": "1",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        }

    @staticmethod
    def get_note_no_water_video(note_id: str):
        success = True
        msg = '成功'
        video_addr = None
        try:
            headers = Apis._get_www_headers()
            url = f"https://www.xiaohongshu.com/explore/{note_id}"
            resp = requests.get(url, headers=headers, timeout=20)
            res = resp.text
            m = re.search(r'<meta name="og:video" content="(.*?)">', res)
            video_addr = m.group(1) if m else None
        except Exception as e:
            success = False
            msg = str(e)
        return success, msg, video_addr

    @staticmethod
    def get_note_no_water_img(img_url: str):
        success = True
        msg = '成功'
        new_url = None
        try:
            if '.jpg' in img_url:
                img_id = '/'.join([split for split in img_url.split('/')[-3:]]).split('!')[0]
                new_url = f'https://sns-img-qc.xhscdn.com/{img_id}'
            elif 'spectrum' in img_url:
                img_id = '/'.join(img_url.split('/')[-2:]).split('!')[0]
                new_url = f'http://sns-webpic.xhscdn.com/{img_id}?imageView2/2/w/format/jpg'
            else:
                img_id = img_url.split('/')[-1].split('!')[0]
                new_url = f'https://sns-img-qc.xhscdn.com/{img_id}'
        except Exception as e:
            success = False
            msg = str(e)
        return success, msg, new_url


if __name__ == "__main__":
    DEMO_COOKIE = ""  # 在这里粘贴你的 Cookie 字符串
    api = Apis()

    # 示例资源（请替换为你的实际 URL，需包含 xsec_token）
    demo_note_url = "https://www.xiaohongshu.com/explore/NOTE_ID_EXAMPLE?xsec_token=TOKEN&xsec_source=pc_feed"
    demo_user_url = "https://www.xiaohongshu.com/user/profile/USER_ID_EXAMPLE?xsec_token=TOKEN&xsec_source=pc_search"

    def try_call(title, fn, *args, **kwargs):
        print(f"\n==== {title} ====")
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            print(f"[ERROR] {title}: {e}")
            return None

    # 用户信息
    try_call("user/selfinfo", api.get_user_self_info, DEMO_COOKIE)
    try_call("user/me (v2)", api.get_user_self_info2, DEMO_COOKIE)

    # 用户资料 + 作品
    user_id_demo = urlparse(demo_user_url).path.split('/')[-1]
    try_call("user/otherinfo", api.get_user_info, user_id_demo, DEMO_COOKIE)
    try_call("user_posted (page)", api.get_user_note_info, user_id_demo, "", DEMO_COOKIE, "", "pc_search")
    try_call("user_posted (all)", api.get_user_all_notes, demo_user_url, DEMO_COOKIE)

    # 首页
    try_call("homefeed/category", api.get_homefeed_all_channel, DEMO_COOKIE)
    try_call("homefeed (page)", api.get_homefeed_recommend, "homefeed_recommend", "", 1, 0, DEMO_COOKIE)
    try_call("homefeed (some)", api.get_homefeed_recommend_by_num, "homefeed_recommend", 5, DEMO_COOKIE)

    # 笔记详情
    try_call("feed", api.get_note_info, demo_note_url, DEMO_COOKIE)

    # 评论：顶层/二级/两阶段
    note_id_demo = urlparse(demo_note_url).path.split('/')[-1]
    kv_demo = dict(parse_qsl(urlparse(demo_note_url).query, keep_blank_values=True))
    xst_demo = kv_demo.get('xsec_token', '')
    try_call("comment/out (page)", api.get_note_out_comment, note_id_demo, "", xst_demo, DEMO_COOKIE)
    out_tuple = try_call("comment/out (all)", api.get_note_all_out_comment, note_id_demo, xst_demo, DEMO_COOKIE)

    # 如需二级评论测试，尝试取一条根评论作为示例
    try:
        if isinstance(out_tuple, tuple):
            payload = out_tuple[2]
            if isinstance(payload, dict) and isinstance(payload.get('comments'), list) and payload['comments']:
                root = payload['comments'][0]
                root.setdefault('note_id', note_id_demo)
                try_call("comment/sub (page)", api.get_note_inner_comment, root, "", xst_demo, DEMO_COOKIE)
                try_call("comment/sub (all)", api.get_note_all_inner_comment, root, xst_demo, DEMO_COOKIE)
    except Exception as _:
        pass
    try_call("comment two-phase", api.get_note_all_comment, demo_note_url, DEMO_COOKIE)

    # 喜欢/收藏
    try_call("user like (page)", api.get_user_like_note_info, user_id_demo, "", DEMO_COOKIE, "", "pc_user")
    try_call("user like (all)", api.get_user_all_like_note_info, demo_user_url, DEMO_COOKIE)
    try_call("user collect (page)", api.get_user_collect_note_info, user_id_demo, "", DEMO_COOKIE, "", "pc_user")
    try_call("user collect (all)", api.get_user_all_collect_note_info, demo_user_url, DEMO_COOKIE)

    # 搜索
    try_call("search/recommend", api.get_search_keyword, "关键词", DEMO_COOKIE)
    try_call("search/notes (v1 page)", api.search_note, "关键词", DEMO_COOKIE, 1, 0, 0, 0, 0, None)
    try_call("search/notes (v1 some)", api.search_some_note, "关键词", 5, DEMO_COOKIE)

    # 站内消息
    try_call("unread_count", api.get_unread_message, DEMO_COOKIE)
    try_call("mentions (page)", api.get_metions, "", DEMO_COOKIE)
    try_call("mentions (all)", api.get_all_metions, DEMO_COOKIE)
    try_call("likes (page)", api.get_likesAndcollects, "", DEMO_COOKIE)
    try_call("likes (all)", api.get_all_likesAndcollects, DEMO_COOKIE)
    try_call("connections (page)", api.get_new_connections, "", DEMO_COOKIE)
    try_call("connections (all)", api.get_all_new_connections, DEMO_COOKIE)

    # 无水印资源
    try_call("note no-watermark video", api.get_note_no_water_video, note_id_demo)
    try_call("note no-watermark image", api.get_note_no_water_img, "https://sns-webpic-qc.xhscdn.com/202401011200/abc/def!nd_dft_wgth_webp_3")