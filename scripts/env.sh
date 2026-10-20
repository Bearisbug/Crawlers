# 服务器上 source 本文件：补 PATH，并从 .secrets 里的 cookie JSON 导出 redbook 需要的环境变量
export PATH="$HOME/.local/bin:$HOME/.local/node/bin:$PATH"
WORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# XHS_ACCOUNT_FILE 指定用哪个账号的 cookie（.secrets/accounts/xhs_*.json），不设则用 Edge 导出的那份
REDBOOK_COOKIE_STRING="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['cookie_str'])" "${XHS_ACCOUNT_FILE:-$WORK_DIR/.secrets/xhs_cookies.json}")"
export REDBOOK_COOKIE_STRING
