// 小红书详情 + 评论阶段：读 notes_list.jsonl，逐篇
//   1) 打开笔记网页，从 __INITIAL_STATE__ 取完整笔记数据；
//   2) 评论数 > 0 时按平台默认排序翻一级评论，每篇最多 --max-comments 条（不抓楼中楼回复；页面评论数含回复）。
// 每次响应原样追加到 <out-dir>/raw/{note_page,comment_page}.jsonl.gz。
// 多账号轮换（.secrets/accounts/xhs_*.json），每号每天上限 --daily-cap 次请求（只在开始新笔记前检查）；
// 相邻两篇笔记间隔 --gap ~ 2×gap 秒。服务器机房 IP 上两个号 20 分钟就被拦，这个脚本要在家庭网络上跑。
// 被跳验证码 / 登录页的账号暂停，弹通知并用 verify_session.py 开窗口让人过验证，通过后换上新 cookie 继续；
// 被跳「账号异常，请稍后再试」（website-login/error，error_code=300011）的账号没有验证可过，停用 3 小时后再试；
// 其他账号照常跑，全部账号都在等验证时整轮等着。
// 一篇笔记的详情和评论都完成后才写 notes.jsonl；中途被打断的笔记重跑时整篇重来，评论按 comment_id 去重即可。
// 用法：node xhs_detail.mjs --list data/xhs_full/notes_list.jsonl --out-dir data/xhs_full
//        [--from 2025-07-01 --to 2026-06-30] [--daily-cap 3000] [--max-comments 100] [--gap 15]
// 输出：notes.jsonl、comments.jsonl、notes_failed.jsonl（笔记本身不可用）、comments_failed.jsonl（评论没抓全，留给后续补抓）、
//       account_state.json（各账号当日请求数，重启后沿用）。
import { execSync, spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { parseArgs } from "node:util";
import { fileURLToPath, pathToFileURL } from "node:url";
import zlib from "node:zlib";

const { values: args } = parseArgs({
  options: {
    list: { type: "string" },
    "out-dir": { type: "string", default: "data/xhs_full" },
    from: { type: "string", default: "2025-07-01" },
    to: { type: "string", default: "2026-06-30" },
    "daily-cap": { type: "string", default: "3000" },
    "max-comments": { type: "string", default: "100" },
    gap: { type: "string", default: "15" },
    accounts: { type: "string", default: ".secrets/accounts" },
  },
});
const libDir =
  process.env.REDBOOK_LIB ??
  `${execSync("npm root -g").toString().trim()}/@lucasygu/redbook/dist/lib`;
const { XhsClient, NeedVerifyError } = await import(pathToFileURL(`${libDir}/client.js`).href);
const { parseCookieString } = await import(pathToFileURL(`${libDir}/cookies.js`).href);

const UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36";
const DAILY_CAP = Number(args["daily-cap"]);
const MAX_COMMENTS = Number(args["max-comments"]);
const GAP_MS = Number(args.gap) * 1000;
const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const VERIFY_PY = path.join(SCRIPT_DIR, "../.venv/bin/python");
const VERIFY_RETRY_MS = 30 * 60 * 1000; // 没过验证（超时或关了窗口）的账号，30 分钟后再试，再被拦会再弹窗
const RESTRICTED_MS = 3 * 3600 * 1000;
const out = (name) => path.join(args["out-dir"], name);
const rawDir = out("raw");
fs.mkdirSync(rawDir, { recursive: true });

const sleep = (min, max = min) => new Promise((r) => setTimeout(r, min + Math.random() * (max - min)));
const log = (...a) => console.error(new Date().toISOString(), ...a);
const append = (name, row) => fs.appendFileSync(out(name), JSON.stringify(row) + "\n");
const appendRaw = (kind, rec) => fs.appendFileSync(path.join(rawDir, `${kind}.jsonl.gz`),
  zlib.gzipSync(JSON.stringify({ kind, ...rec, fetched_at: new Date().toISOString() }) + "\n"));
const readJsonl = (f) => (fs.existsSync(f) ? fs.readFileSync(f, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l)) : []);
const today = () => new Date(Date.now() + 8 * 3600 * 1000).toISOString().slice(0, 10); // 北京时间日期
const iso = (ms) => (ms ? new Date(Number(ms)).toISOString() : null);

// 计数为 0 时页面给空串；「2.2万」按万换算（平台只给到 0.1 万精度）
const parseCount = (s) => {
  if (s === undefined || s === null) return null;
  if (s === "") return 0;
  const m = String(s).match(/^([\d.]+)\s*(万|w|W)?\+?$/);
  return m ? Math.round(Number(m[1]) * (m[2] ? 10000 : 1)) : null;
};

// 页面状态是 JS 字面量：含 undefined、new Map([]) 等，替换后才能按 JSON 解析
function parseState(html) {
  const m = html.match(/window\.__INITIAL_STATE__=({.*?})<\/script>/s);
  if (!m) return null;
  return JSON.parse(m[1].replace(/\bundefined\b/g, "null").replace(/new Map\(\[\]\)/g, "{}"));
}

class AccountBlocked extends Error {
  constructor(message, url) {
    super(message);
    this.url = url;
  }
}
class AccountRestricted extends AccountBlocked {}

// ---- 账号池 ----
function loadAccount(acc) {
  acc.cookie = JSON.parse(fs.readFileSync(acc.file, "utf8")).cookie_str;
  acc.client = new XhsClient(parseCookieString(acc.cookie));
}
const accounts = fs.readdirSync(args.accounts).filter((f) => /^xhs_.*\.json$/.test(f)).sort().map((f) => {
  const acc = { id: f, file: path.join(args.accounts, f), verifying: false, retryAt: 0 };
  loadAccount(acc);
  return acc;
});
const statePath = out("account_state.json");
const state = fs.existsSync(statePath) ? JSON.parse(fs.readFileSync(statePath, "utf8")) : {};
const saveState = () => fs.writeFileSync(statePath, JSON.stringify(state, null, 1));
for (const a of accounts) state[a.id] ??= { day: today(), count: 0 };

function pickAccount() {
  for (const a of accounts) {
    const s = state[a.id];
    if (s.day !== today()) Object.assign(s, { day: today(), count: 0 });
  }
  const usable = accounts.filter((a) => !a.verifying && a.retryAt <= Date.now() && state[a.id].count < DAILY_CAP);
  return usable.sort((x, y) => state[x.id].count - state[y.id].count)[0];
}

function startVerify(acc, url) {
  acc.verifying = true;
  const child = spawn(VERIFY_PY, [path.join(SCRIPT_DIR, "verify_session.py"), "xhs", acc.file, url],
    { stdio: ["ignore", "inherit", "inherit"] });
  child.on("exit", (code) => {
    acc.verifying = false;
    if (code === 0) {
      loadAccount(acc);
      log(`account ${acc.id} verified, back in rotation`);
    } else {
      acc.retryAt = Date.now() + VERIFY_RETRY_MS;
      log(`account ${acc.id} not verified (exit ${code}); retry in 30 min`);
    }
  });
}

// 每次请求计入账号当日用量；验证码统一转成 AccountBlocked
async function api(acc, fn) {
  state[acc.id].count++;
  try {
    return await fn(acc.client);
  } catch (e) {
    if (e instanceof NeedVerifyError) throw new AccountBlocked(e.message);
    throw e;
  }
}

// ---- 详情 ----
async function fetchNotePage(n, acc) {
  state[acc.id].count++;
  const url = `https://www.xiaohongshu.com/explore/${n.note_id}?xsec_token=${encodeURIComponent(n.xsec_token)}&xsec_source=pc_user`;
  const res = await fetch(url, { headers: { "user-agent": UA, referer: "https://www.xiaohongshu.com/", cookie: acc.cookie }, redirect: "manual" });
  if (res.status >= 300 && res.status < 400) {
    const location = new URL(res.headers.get("location") ?? "/", "https://www.xiaohongshu.com").href;
    const Err = location.includes("/website-login/error") ? AccountRestricted : AccountBlocked;
    throw new Err(`redirect ${res.status} -> ${decodeURIComponent(location).replace(/redirectPath=[^&]*&?/, "").slice(0, 160)}`, location);
  }
  const html = await res.text();
  if (res.status === 461 || res.status === 471 || /website-login\/captcha|请通过验证/.test(html)) {
    throw new AccountBlocked(`captcha (HTTP ${res.status})`, url);
  }
  const entry = parseState(html)?.note?.noteDetailMap?.[n.note_id];
  if (!entry?.note) return { unavailable: `HTTP ${res.status}, note missing in page` };
  appendRaw("note_page", { key: n.note_id, response: entry });
  return { note: entry.note };
}

// ---- 评论 ----
function commentRow(n, c) {
  return {
    platform: "xhs", note_id: n.note_id, comment_id: c.id,
    content: c.content, created_at: iso(c.create_time), likes: parseCount(c.like_count),
    ip_location: c.ip_location ?? null, user_id: c.user_info?.user_id ?? null, nickname: c.user_info?.nickname ?? null,
    sub_comment_count: c.sub_comment_count != null ? Number(c.sub_comment_count) : null,
    fetched_at: new Date().toISOString(),
  };
}

async function fetchComments(n, acc) {
  let cursor = "", rows = 0;
  while (rows < MAX_COMMENTS) {
    await sleep(2000, 4000);
    const page = await api(acc, (c) => c.getComments(n.note_id, cursor, n.xsec_token));
    appendRaw("comment_page", { key: n.note_id, request: { cursor }, response: page });
    for (const c of (page.comments ?? []).slice(0, MAX_COMMENTS - rows)) {
      append("comments.jsonl", commentRow(n, c));
      rows++;
    }
    if (!page.has_more || !page.cursor) break;
    cursor = page.cursor;
  }
  return rows;
}

function noteRow(n, note, commentsStatus, commentsFetched) {
  const ii = note.interactInfo ?? {};
  return {
    platform: "xhs", red_id: n.red_id, user_id: n.user_id, note_id: n.note_id,
    url: `https://www.xiaohongshu.com/explore/${n.note_id}`,
    type: note.type, title: note.title, desc: note.desc,
    tags: (note.tagList ?? []).map((t) => t.name),
    published_at: iso(note.time) ?? n.published_at, last_update_at: iso(note.lastUpdateTime),
    ip_location: note.ipLocation ?? null, is_pinned: n.is_pinned,
    image_count: (note.imageList ?? []).length, video_duration_s: note.video?.capa?.duration ?? null,
    likes: parseCount(ii.likedCount), likes_raw: ii.likedCount,
    collects: parseCount(ii.collectedCount), collects_raw: ii.collectedCount,
    comments: parseCount(ii.commentCount), comments_raw: ii.commentCount,
    shares: parseCount(ii.shareCount), shares_raw: ii.shareCount,
    comments_status: commentsStatus, comments_fetched: commentsFetched,
    source: "self-html", fetched_at: new Date().toISOString(),
  };
}

// ---- 主循环 ----
const fromMs = Date.parse(`${args.from}T00:00:00+08:00`);
const toMs = Date.parse(`${args.to}T23:59:59+08:00`);
const done = new Set([...readJsonl(out("notes.jsonl")), ...readJsonl(out("notes_failed.jsonl"))].map((r) => r.note_id));
const todo = readJsonl(args.list).filter((n) => {
  const t = Date.parse(n.published_at);
  return t >= fromMs && t <= toMs && !done.has(n.note_id);
});
const attempts = {};
log(`accounts=${accounts.map((a) => a.id).join(",")} todo=${todo.length} done_before=${done.size}`);

for (let i = 0; i < todo.length; ) {
  const acc = pickAccount();
  if (!acc) {
    saveState();
    const waitingVerify = accounts.some((a) => a.verifying || a.retryAt > Date.now());
    if (!waitingVerify) log("all accounts hit the daily cap; sleeping 30 min");
    await sleep(waitingVerify ? 30_000 : 30 * 60_000);
    continue;
  }
  const n = todo[i];
  try {
    const r = await fetchNotePage(n, acc);
    if (r.unavailable) {
      append("notes_failed.jsonl", { ...n, reason: r.unavailable, at: new Date().toISOString() });
    } else {
      let status = "none", fetched = 0;
      if (parseCount(r.note.interactInfo?.commentCount) > 0) {
        try {
          fetched = await fetchComments(n, acc);
          status = "ok";
        } catch (e) {
          if (e instanceof AccountBlocked) throw e;
          status = "failed";
          log(`note ${n.note_id} comments error: ${e?.message ?? e}`);
          append("comments_failed.jsonl", { note_id: n.note_id, xsec_token: n.xsec_token, reason: String(e?.message ?? e).slice(0, 200), at: new Date().toISOString() });
        }
      }
      append("notes.jsonl", noteRow(n, r.note, status, fetched));
    }
    i++;
    if (i % 50 === 0) { log(`progress ${i}/${todo.length}`, JSON.stringify(state)); saveState(); }
  } catch (e) {
    if (e instanceof AccountRestricted) {
      acc.retryAt = Date.now() + RESTRICTED_MS;
      log(`account ${acc.id} restricted: ${e.message}; retry in 3h`);
      saveState();
    } else if (e instanceof AccountBlocked) {
      // 评论接口的验证码不带跳转地址，就打开这篇笔记让人过验证
      const url = e.url ?? `https://www.xiaohongshu.com/explore/${n.note_id}?xsec_token=${encodeURIComponent(n.xsec_token)}&xsec_source=pc_user`;
      log(`account ${acc.id} blocked: ${e.message}; waiting for manual verification`);
      startVerify(acc, url);
      saveState();
    } else {
      attempts[n.note_id] = (attempts[n.note_id] ?? 0) + 1;
      log(`note ${n.note_id} error (#${attempts[n.note_id]}): ${e?.message ?? e}`);
      if (attempts[n.note_id] >= 3) {
        append("notes_failed.jsonl", { ...n, reason: String(e?.message ?? e).slice(0, 200), at: new Date().toISOString() });
        i++;
      } else {
        todo.push(todo.splice(i, 1)[0]); // 网络或解析错误放到队尾，最多试 3 次
      }
      await sleep(10_000);
    }
  }
  await sleep(GAP_MS, 2 * GAP_MS);
}
saveState();
log(`finished: notes=${readJsonl(out("notes.jsonl")).length} failed=${readJsonl(out("notes_failed.jsonl")).length}`);
