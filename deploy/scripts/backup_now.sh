#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/lib.sh"

case "${1:-}" in
  -h|--help) info '用法：sudo ./scripts/backup_now.sh [--wait]'; exit 0 ;;
esac
for arg in "$@"; do
  [ "$arg" = "--wait" ] || die "未知参数 $arg"
done
require_root
require_cmd docker findmnt
docker compose version >/dev/null 2>&1 || die "需要 Docker Compose v2"
check_storage "$(resolve_path "$(env_get BACKUP_HOST_DIR)")"
# Q-150：不需要输入的容器命令关闭标准输入，避免读走后续确认文字。
compose exec -T worker python -m app.cli backup-now "$@" </dev/null
