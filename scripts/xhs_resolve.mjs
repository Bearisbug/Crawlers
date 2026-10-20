// 小红书号（red_id）→ 24 位 user_id，借用 redbook 的 XhsClient.searchUsers（创作者端用户搜索）。
// 用法：REDBOOK_COOKIE_STRING=... [RAW_FILE=raw/search_user.jsonl.gz] node xhs_resolve.mjs <red_id> [red_id ...]
// 输出 JSON Lines：{red_id, matched, user_id, nickname, fans}；设了 RAW_FILE 时另存原始搜索响应（gzip 追加）
import { execSync } from "node:child_process";
import fs from "node:fs";
import zlib from "node:zlib";
import { pathToFileURL } from "node:url";

const libDir =
  process.env.REDBOOK_LIB ??
  `${execSync("npm root -g").toString().trim()}/@lucasygu/redbook/dist/lib`;
const { XhsClient } = await import(pathToFileURL(`${libDir}/client.js`).href);
const { parseCookieString } = await import(pathToFileURL(`${libDir}/cookies.js`).href);

const client = new XhsClient(parseCookieString(process.env.REDBOOK_COOKIE_STRING ?? ""));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const appendRaw = (rec) => process.env.RAW_FILE &&
  fs.appendFileSync(process.env.RAW_FILE, zlib.gzipSync(JSON.stringify({ ...rec, fetched_at: new Date().toISOString() }) + "\n"));

for (const raw of process.argv.slice(2)) {
  const redId = raw.replace(/\s+/g, "");
  let row = { red_id: redId, matched: false };
  try {
    const data = await client.searchUsers(redId);
    appendRaw({ kind: "search_user", key: redId, response: data });
    const users = data?.user_info_dtos ?? [];
    const hit = users.find((u) => u.user_base_dto?.red_id === redId);
    if (hit) {
      row = {
        red_id: redId,
        matched: true,
        user_id: hit.user_base_dto.user_id,
        nickname: hit.user_base_dto.user_nickname,
        fans: hit.fans_total,
      };
    } else {
      row.candidates = users.slice(0, 3).map((u) => [u.user_base_dto?.red_id, u.user_base_dto?.user_nickname]);
    }
  } catch (e) {
    row.error = String(e?.message ?? e);
  }
  console.log(JSON.stringify(row));
  await sleep(2000 + Math.random() * 2000);
}
