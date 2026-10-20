// 小红书笔记列表：主页快照 → 分页列出 --since 之后的全部笔记。基于 redbook 的 XhsClient；详情和评论由 xhs_detail.mjs 抓。
// 用法：REDBOOK_COOKIE_STRING=... node xhs_list.mjs --accounts resolved.jsonl --out-dir data/xhs [--since 2025-07-01] [--raw-dir data/xhs/raw]
// resolved.jsonl 每行至少含 {red_id, user_id}（xhs_resolve.mjs 的输出）。
// 输出：<out-dir>/accounts.jsonl（每账号一行）、<out-dir>/notes_list.jsonl（每笔记一行，只有点赞数）；
//       设了 --raw-dir 时另存主页 user_info.jsonl.gz、列表分页 user_posted.jsonl.gz（gzip 逐条追加）。已列过的笔记重跑时跳过。
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
    "raw-dir": { type: "string" },
  },
});
const sinceMs = args.since ? Date.parse(args.since) : 0;
fs.mkdirSync(args["out-dir"], { recursive: true });
const accountsOut = path.join(args["out-dir"], "accounts.jsonl");
const notesOut = path.join(args["out-dir"], "notes_list.jsonl");

const client = new XhsClient(parseCookieString(process.env.REDBOOK_COOKIE_STRING ?? ""));
const sleep = (min, max) => new Promise((r) => setTimeout(r, min + Math.random() * (max - min)));
const log = (...a) => console.error(new Date().toISOString(), ...a);
const append = (file, row) => fs.appendFileSync(file, JSON.stringify(row) + "\n");
if (args["raw-dir"]) fs.mkdirSync(args["raw-dir"], { recursive: true });
const appendRaw = (kind, rec) => args["raw-dir"] && fs.appendFileSync(path.join(args["raw-dir"], `${kind}.jsonl.gz`),
  zlib.gzipSync(JSON.stringify({ kind, ...rec, fetched_at: new Date().toISOString() }) + "\n"));

// "2.2万" → 22000；"1097" → 1097；空串 → 0（计数为 0 时接口返回空串）
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

async function listAccount({ red_id, user_id }) {
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

  let cursor = "", listed = 0;
  for (;;) {
    const page = await client.getUserNotes(user_id, cursor);
    appendRaw("user_posted", { key: user_id, red_id, request: { cursor }, response: page });
    const notes = page.notes ?? [];
    for (const n of notes) {
      if (idTime(n.note_id) < sinceMs || done.has(n.note_id)) continue;
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
      listed++;
    }
    const last = notes.at(-1);
    if (!page.has_more || !last || idTime(last.note_id) < sinceMs) break;
    cursor = page.cursor;
    await sleep(2000, 4000);
  }
  log(`[${red_id}] notes listed this run: ${listed}`);
}

const accounts = fs.readFileSync(args.accounts, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l))
  .filter((a) => a.user_id);
for (const acc of accounts) {
  try {
    await listAccount(acc);
  } catch (e) {
    if (e instanceof NeedVerifyError) {
      log(`captcha triggered at ${acc.red_id}: ${e.message}; stopping`);
      process.exit(2);
    }
    log(`[${acc.red_id}] error: ${e?.message ?? e}`);
  }
  await sleep(5000, 10000);
}
