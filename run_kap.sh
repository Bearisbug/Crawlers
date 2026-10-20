#!/bin/bash
# 一键：建库（没有时）→ 用本机 Claude 订阅批量做评论 KAP 标注（断点续跑）→ 导出 CSV 到 outputs/export/
# 参数原样传给 scripts/kap_label.py，例如：./run_kap.sh --workers 6 --batch-size 2000
set -e
cd "$(dirname "$0")"
source .venv/bin/activate
python scripts/kap_label.py "$@"
