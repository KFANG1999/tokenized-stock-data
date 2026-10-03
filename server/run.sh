#!/usr/bin/env bash
# 服务器上每 10 分钟由 cron 调用一次：同步仓库 -> 采集一次快照 -> 提交并推送到 GitHub
set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ)"
git pull -q --rebase
python3 collector/collect.py

git add data collector/address-cache.json
if git diff --cached --quiet; then
  echo "没有新数据"
  exit 0
fi
git commit -q -m "data: snapshot $(date -u +%Y-%m-%dT%H:%MZ)"
for i in 1 2 3; do
  git pull -q --rebase && git push -q && exit 0
  sleep 10
done
echo "推送失败，数据已保存在本地，下次运行时会一起推送" >&2
exit 1
