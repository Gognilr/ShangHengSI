#!/usr/bin/env bash
# 从备份恢复全部数据（SPEC 16.3，设计 docs/design/M8.md 3.2，open_questions Q-117～Q-119）。
# 用法：sudo ./scripts/restore.sh [--yes] /data/apps/aireview/backup/aireview_20261022_0200.bak
#
# 第 1～5 步（检查、校验、解密、解包、确认）失败时系统不做任何改动，app、worker 不停止。
# 第 6 步停止 app、worker 之后任何一步失败：不自动回滚，打印当前状态和处理办法（Q-117）。
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=lib.sh
source "$ROOT/scripts/lib.sh"

DB_NAME="aireview"          # docker-compose.yml 中固定的库名
APP_UID=10001               # 容器内 appuser（Q-120）
CONTAINER_BACKUP_DIR="/backup"

usage() {
  cat <<'EOF'
用法：sudo ./scripts/restore.sh [--yes] <备份文件>

  <备份文件>  备份目录下的 aireview_yyyyMMdd_HHmm.bak（同目录下须有同名 .sha256）
  --yes       跳过输入确认（只用于演练和自动化）
  -h, --help  显示本说明

恢复会用备份覆盖当前全部数据，所有用户需要重新登录。需要备份密码和主密钥两者都与备份时一致。
恢复前建议先在 PVE 中为虚拟机创建快照。
EOF
}

ASSUME_YES=0
BAK=""
while [ $# -gt 0 ]; do
  case "$1" in
    --yes) ASSUME_YES=1 ;;
    -h|--help) usage; exit 0 ;;
    -*) die "未知参数 $1（用 --help 查看用法）" ;;
    *) [ -z "$BAK" ] || die "只能指定一个备份文件"; BAK="$1" ;;
  esac
  shift
done
export ASSUME_YES
[ -n "$BAK" ] || { usage >&2; exit 1; }

STAGE="check"
WORK=""
FILES_EXPECTED=""

# 容器命令的标准输入一律接 /dev/null：-T 只关闭 TTY，docker 仍会读走脚本的标准输入，
# 使第 5 步的确认读不到运维人员输入（或提前键入）的“确认恢复”。
worker_cli() {
  compose run --rm --no-deps -T worker python -m app.cli "$@" </dev/null
}

app_cli() {
  compose exec -T app python -m app.cli "$@" </dev/null
}

on_exit() {
  local rc=$?
  set +e
  trap - EXIT
  if [ "$rc" -ne 0 ]; then
    echo >&2
    case "$STAGE" in
      check)
        echo "恢复已停止（第 1～5 步）：系统未做任何改动，app、worker 仍在运行。" >&2
        ;;
      prepared)
        worker_cli restore-files --drop-new >/dev/null 2>&1
        echo "恢复已停止（第 1～5 步）：系统未做任何改动，app、worker 仍在运行。" >&2
        ;;
      stopped)
        cat >&2 <<EOF
恢复失败（第 6～7 步 停止服务、恢复数据库）。app、worker 目前已停止。
  数据库：restore-db 失败时当前数据库保持不变；若已出现 ${DB_NAME}_pre_restore 库，说明数据库已替换，请按运维说明第 10 章“恢复失败怎么办”处理。
  原件：未改动（待恢复的原件在原件卷的 .restore-new/ 中，下次恢复时会自动清除）。
处理办法：查明原因后重新执行恢复；或执行以下命令恢复服务（继续使用恢复前的数据）：
  cd $ROOT && docker compose start app worker
EOF
        ;;
      db_done)
        cat >&2 <<EOF
恢复失败（第 8 步 替换原件）。app、worker 目前已停止。
  数据库：已换成备份的数据；恢复前的数据库改名为 ${DB_NAME}_pre_restore。
  原件：替换未完成；恢复前的原件在原件卷的 .restore-old/，备份中的原件在 .restore-new/（可能已部分移出）。
处理办法：按运维说明第 10 章“恢复失败怎么办”手动回退，或用 PVE 快照恢复到恢复前。
EOF
        ;;
      files_done)
        cat >&2 <<EOF
恢复失败（第 9～10 步 启动服务与核对）。
  数据库与原件都已换成备份的数据；恢复前的数据库 ${DB_NAME}_pre_restore 与原件卷中的 .restore-old/ 仍保留，未删除。
处理办法：查看日志 cd $ROOT && docker compose logs app worker；按运维说明第 10 章“恢复失败怎么办”处理，或用 PVE 快照恢复到恢复前。
EOF
        ;;
    esac
  fi
  # 解密后的 tar 和 db.dump 一律删除
  [ -n "$WORK" ] && rm -rf -- "$WORK"
  exit "$rc"
}
trap on_exit EXIT

# ---------------------------------------------------------------- 1. 前置检查
require_root
require_cmd docker sha256sum findmnt stat df awk install
[ -f "$ROOT/.env" ] || die "未找到 $ROOT/.env，系统尚未安装"
info '证书不在备份中；恢复到新机器时，请密钥保管人提供 secrets/ca/ 中的根证书、中间证书与中间私钥，或用离线根重新签发；根私钥不能放在服务器。'
BACKUP_DIR="$(env_get BACKUP_HOST_DIR)"
BACKUP_DIR="$(resolve_path "${BACKUP_DIR:-./backup}")"
[ -d "$BACKUP_DIR" ] || die "备份目录 $BACKUP_DIR 不存在"
check_storage "$BACKUP_DIR"

NAME="$(basename -- "$BAK")"
[[ "$NAME" =~ ^aireview_[0-9]{8}_[0-9]{4}\.bak$ ]] || die "文件名应为 aireview_yyyyMMdd_HHmm.bak：$NAME"
[ -f "$BAK" ] && [ ! -L "$BAK" ] || die "备份文件不存在：$BAK"
[ "$(cd "$(dirname -- "$BAK")" && pwd -P)" = "$(cd "$BACKUP_DIR" && pwd -P)" ] \
  || die "备份文件必须位于备份目录 $BACKUP_DIR 下"
SHA_FILE="$BACKUP_DIR/$NAME.sha256"
[ -f "$SHA_FILE" ] && [ ! -L "$SHA_FILE" ] || die "缺少校验文件 $NAME.sha256"
compose ps --status running --services 2>/dev/null | grep -qx db \
  || die "数据库服务 db 未运行；请先执行 cd $ROOT && docker compose up -d db"
check_pgvector

size="$(stat -c %s -- "$BACKUP_DIR/$NAME")"
avail="$(df -P -B1 -- "$BACKUP_DIR" | awk 'NR == 2 { print $4 }')"
if [ $((avail * 10)) -lt $((size * 22)) ]; then
  die "备份目录所在磁盘剩余空间不足：需要至少 $(( size * 22 / 10 / 1048576 + 1 )) MB（备份文件大小的 2.2 倍），当前剩余 $(( avail / 1048576 )) MB"
fi

# ---------------------------------------------------------------- 2. 校验 SHA-256
info "[1/6] 校验 SHA-256……"
sha_lines="$(wc -l < "$SHA_FILE")"
read -r sha_digest sha_name < "$SHA_FILE" || true
[ "$sha_lines" -eq 1 ] && [[ "${sha_digest:-}" =~ ^[0-9a-f]{64}$ ]] && [ "${sha_name:-}" = "$NAME" ] \
  || die "校验文件 $NAME.sha256 的内容不正确"
(cd "$BACKUP_DIR" && sha256sum -c --strict --status -- "$NAME.sha256") \
  || die "SHA-256 校验不通过：备份文件已损坏或被修改，请换一份备份（或从异地副本重新拉取）"

# ---------------------------------------------------------------- 3. 解密
info "[2/6] 解密备份（文件较大时需要几分钟）……"
WORK="$BACKUP_DIR/.restore"
rm -rf -- "$WORK"
install -d -m 700 -o "$APP_UID" -g "$APP_UID" -- "$WORK"
worker_cli decrypt-backup --in "$CONTAINER_BACKUP_DIR/$NAME" --out "$CONTAINER_BACKUP_DIR/.restore/backup.tar" >/dev/null \
  || die "解密失败（原因见上一行）。恢复需要备份时使用的备份密码（secrets/backup_password）"

# ---------------------------------------------------------------- 4. 校验并解包
info "[3/6] 校验备份内容并解出数据……"
STAGE="prepared"
SUMMARY="$(worker_cli restore-prepare --tar "$CONTAINER_BACKUP_DIR/.restore/backup.tar" --work "$CONTAINER_BACKUP_DIR/.restore" | tail -n 1)" \
  || die "备份校验未通过（原因见上一行）"
rm -f -- "$WORK/backup.tar"

json_field() {
  printf '%s' "$SUMMARY" | sed -n "s/.*\"$1\": *\"\{0,1\}\([^\",}]*\)\"\{0,1\}[,}].*/\1/p"
}
FILES_EXPECTED="$(json_field files_extracted)"
[[ "$FILES_EXPECTED" =~ ^[0-9]+$ ]] || die "无法读取备份摘要"
created="$(json_field created_at)"
created_local="$(TZ=Asia/Shanghai date -d "$created" '+%Y-%m-%d %H:%M' 2>/dev/null || printf '%s' "$created")"

# ---------------------------------------------------------------- 5. 确认
cat <<EOF

备份摘要
  备份时间：$created_local
  系统版本：$(json_field app_version)（数据库迁移版本 $(json_field revision)）
  任务数：  $(json_field task_count)
  原件数：  $FILES_EXPECTED$( [ "$(json_field key_checked)" = "true" ] && printf '（主密钥已核对）' || printf '（备份中没有原件，未核对主密钥）' )

EOF
confirm "恢复会用这份备份覆盖当前全部数据，所有用户需要重新登录；建议先在 PVE 中为虚拟机创建快照。" "确认恢复"

# ---------------------------------------------------------------- 6. 停止 app、worker
info "[4/6] 停止 app、worker……"
STAGE="stopped"
compose stop app worker

# ---------------------------------------------------------------- 7. 恢复数据库
info "[5/6] 恢复数据库与原件……"
worker_cli restore-db --dump "$CONTAINER_BACKUP_DIR/.restore/db.dump"
STAGE="db_done"

# ---------------------------------------------------------------- 8. 替换原件
worker_cli restore-files
STAGE="files_done"
rm -f -- "$WORK/db.dump"

# ---------------------------------------------------------------- 9. 启动
info "[6/6] 启动服务并核对……"
compose up -d app worker
wait_healthy app 300

# ---------------------------------------------------------------- 10. 核对与清理
app_cli restore-files --verify --expect-files "$FILES_EXPECTED"
app_cli restore-db --drop-old
app_cli restore-files --drop-old
STAGE="done"

info ""
info "恢复完成；所有用户需要重新登录。"
