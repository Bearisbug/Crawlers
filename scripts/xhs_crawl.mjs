// 小红书作品级抓取：主页快照 → 分页列出笔记 → 逐条取详情（完整互动数）。基于 redbook 的 XhsClient。
// 用法：REDBOOK_COOKIE_STRING=... node xhs_crawl.mjs --accounts resolved.jsonl --out-dir data/xhs [--since 2025-01-01] [--max-notes N]
// resolved.jsonl 每行至少含 {red_id, user_id}（xhs_resolve.mjs 的输出）。
// 输出：<out-dir>/accounts.jsonl（每账号一行）、<out-dir>/notes.jsonl（每笔记一行）。已抓过的笔记重跑时跳过。
// 遇到验证码立即退出（exit 2），避免继续请求把账号风控等级推高。
import { execSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { parseArgs } from "node:util";
import zlib from "node:zlib";
import { pathToFileURL } from "node:url";

const libDir =
  process.env.REDBOOK_LIB ??
  `${execSync("npm root -g").toString().trim()}/@lucasygu/redbook/dist/lib`;
const { XhsClient, NeedVerifyError } = await import(pathToFileURL(`${libDir}/client.js`).href);
const { parseCookieString } = await import(pathToFileURL(`${libDir}/cookies.js`).href);

const { values: args } = parseArgs({
  options: {
    accounts: { type: "string" },
    "out-dir": { type: "string", default: "data/xhs" },
    since: { type: "string" },
    "max-notes": { type: "string" },
    // 只翻列表不取详情：列表里只有点赞数，但请求量少一个数量级，用来统计篇数或给详情阶段备料
    "list-only": { type: "boolean", default: false },
    // 原始响应目录：主页 user_info.jsonl.gz、列表分页 user_posted.jsonl.gz（gzip 逐条追加）
    "raw-dir": { type: "string" },
  },
});
const sinceMs = args.since ? Date.parse(args.since) : 0;
const maxNotes = args["max-notes"] ? Number(args["max-notes"]) : Infinity;
const listOnly = args["list-only"];
fs.mkdirSync(args["out-dir"], { recursive: true });
const accountsOut = path.join(args["out-dir"], "accounts.jsonl");
const notesOut = path.join(args["out-dir"], listOnly ? "notes_list.jsonl" : "notes.jsonl");

const client = new XhsClient(parseCookieString(process.env.REDBOOK_COOKIE_STRING ?? ""));
const sleep = (min, max) => new Promise((r) => setTimeout(r, min + Math.random() * (max - min)));
const log = (...a) => console.error(new Date().toISOString(), ...a);
const append = (file, row) => fs.appendFileSync(file, JSON.stringify(row) + "\n");
if (args["raw-dir"]) fs.mkdirSync(args["raw-dir"], { recursive: true });
const appendRaw = (kind, rec) => args["raw-dir"] && fs.appendFileSync(path.join(args["raw-dir"], `${kind}.jsonl.gz`),
  zlib.gzipSync(JSON.stringify({ kind, ...rec, fetched_at: new Date().toISOString() }) + "\n"));

// "2.2万" → 22000；"1097" → 1097；空串 → 0（计数为 0 时接口返回空串，页面显示「评论」等字样）
const parseCount = (s) => {
  if (s === undefined || s === null) return null;
  if (s === "") return 0;
  const m = String(s).match(/^([\d.]+)\s*(万|w|W)?$/);
  if (!m) return null;
  return Math.round(Number(m[1]) * (m[2] ? 10000 : 1));
};
// 笔记 ID 前 8 位十六进制是创建时间（秒）
const idTime = (noteId) => parseInt(noteId.slice(0, 8), 16) * 1000;

const done = new Set(
  fs.existsSync(notesOut)
    ? fs.readFileSync(notesOut, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l).note_id)
    : []
);

async function crawlAccount({ red_id, user_id }) {
  const info = await client.getUserInfo(user_id);
  appendRaw("user_info", { key: user_id, red_id, response: info });
  const counts = Object.fromEntries((info.interactions ?? []).map((i) => [i.type, Number(i.count)]));
  append(accountsOut, {
    red_id, user_id,
    nickname: info.basic_info?.nickname,
    fans: counts.fans ?? null,
    follows: counts.follows ?? null,
    liked_and_collected: counts.interaction ?? null,
    ip_location: info.basic_info?.ip_location,
    fetched_at: new Date().toISOString(),
  });
  log(`[${red_id}] ${info.basic_info?.nickname} fans=${counts.fans}`);

  let cursor = "", fetched = 0;
  while (fetched < maxNotes) {
    const page = await client.getUserNotes(user_id, cursor);
    appendRaw("user_posted", { key: user_id, red_id, request: { cursor }, response: page });
    const notes = page.notes ?? [];
    for (const n of notes) {
      if (fetched >= maxNotes) break;
      if (idTime(n.note_id) < sinceMs || done.has(n.note_id)) continue;
      if (listOnly) {
        append(notesOut, {
          platform: "xhs", red_id, user_id,
          note_id: n.note_id, xsec_token: n.xsec_token,
          type: n.type, title: n.display_title,
          published_at: new Date(idTime(n.note_id)).toISOString(),
          is_pinned: Boolean(n.interact_info?.sticky),
          likes: parseCount(n.interact_info?.liked_count), likes_raw: n.interact_info?.liked_count,
          fetched_at: new Date().toISOString(),
        });
        done.add(n.note_id);
        fetched++;
        continue;
      }
      await sleep(3000, 6000);
      const detail = await client.getNoteById(n.note_id, n.xsec_token, "pc_user");
      const card = detail.items?.[0]?.note_card ?? {};
      const ii = card.interact_info ?? {};
      append(notesOut, {
        platform: "xhs", red_id, user_id,
        note_id: n.note_id,
        url: `https://www.xiaohongshu.com/explore/${n.note_id}?xsec_token=${encodeURIComponent(n.xsec_token)}&xsec_source=pc_user`,
        type: card.type ?? n.type,
        title: card.title ?? n.display_title,
        desc: card.desc,
        tags: (card.tag_list ?? []).map((t) => t.name),
        published_at: card.time ? new Date(card.time).toISOString() : new Date(idTime(n.note_id)).toISOString(),
        last_update_at: card.last_update_time ? new Date(card.last_update_time).toISOString() : null,
        ip_location: card.ip_location ?? null,
        is_pinned: Boolean(n.interact_info?.sticky),
        likes: parseCount(ii.liked_count), likes_raw: ii.liked_count,
        collects: parseCount(ii.collected_count), collects_raw: ii.collected_count,
        comments: parseCount(ii.comment_count), comments_raw: ii.comment_count,
        shares: parseCount(ii.share_count), shares_raw: ii.share_count,
        fetched_at: new Date().toISOString(),
      });
      done.add(n.note_id);
      fetched++;
    }
    const last = notes.at(-1);
    if (!page.has_more || !last || idTime(last.note_id) < sinceMs) break;
    cursor = page.cursor;
    await sleep(2000, 4000);
  }
  log(`[${red_id}] notes written this run: ${fetched}`);
}

const accounts = fs.readFileSync(args.accounts, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l))
  .filter((a) => a.user_id);
for (const acc of accounts) {
  try {
    await crawlAccount(acc);
  } catch (e) {
    if (e instanceof NeedVerifyError) {
      log(`captcha triggered at ${acc.red_id}: ${e.message}; stopping`);
      process.exit(2);
    }
    log(`[${acc.red_id}] error: ${e?.message ?? e}`);
  }
  await sleep(5000, 10000);
}
