#!/usr/bin/env bash
# V2-C-07、V2Q-70：停服轮换，以主密钥文件的原子替换为提交点。
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/lib.sh"

usage() {
  info '用法：sudo ./scripts/rotate_key.sh [--yes] --new-key-file <新主密钥文件>'
  info '新文件须为不同的 32 字节原始密钥、权限 600。旧密钥固定取 secrets/master_key。'
}
ASSUME_YES=0
NEW_KEY=""
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --yes) ASSUME_YES=1; shift ;;
    --new-key-file)
      [ $# -ge 2 ] && [ -z "$NEW_KEY" ] || die '请只指定一个新主密钥文件'
      NEW_KEY="$2"; shift 2 ;;
    *) die "未知参数 $1（用 --help 查看用法）" ;;
  esac
done
export ASSUME_YES
[ -n "$NEW_KEY" ] || { usage >&2; exit 1; }
require_root
require_cmd docker findmnt stat cmp cp chmod chown mktemp mv sync flock realpath basename date rm bash grep
[ -f "$ROOT/.env" ] || die '未找到 .env，系统尚未安装'
[ ! -L "$NEW_KEY" ] || die '新主密钥必须为普通文件，不能是符号链接'
NEW_KEY="$(realpath -- "$NEW_KEY")"
OLD_KEY="$ROOT/secrets/master_key"
# 入口符号链接已检查，旧密钥也必须为普通文件。
for key_path in "$OLD_KEY" "$NEW_KEY"; do
  [ -f "$key_path" ] && [ ! -L "$key_path" ] || die '主密钥必须为普通文件'
  [ "$(stat -c %s -- "$key_path")" -eq 32 ] || die '主密钥文件必须恰好 32 字节'
  [ "$(stat -c %a -- "$key_path")" = 600 ] || die '主密钥文件权限必须为 600'
done
check_storage "$(resolve_path "$(env_get BACKUP_HOST_DIR)")"
exec 9>"$ROOT/secrets/.rotate-key.lock"
flock -n 9 || die '另一个密钥更换操作正在执行'
PENDING="$ROOT/secrets/master_key.pending"
RESUME=0
if [ -e "$PENDING" ] || [ -L "$PENDING" ]; then
  [ -f "$PENDING" ] && [ ! -L "$PENDING" ] \
    && [ "$(stat -c %s -- "$PENDING")" -eq 32 ] \
    && [ "$(stat -c %a -- "$PENDING")" = 600 ] \
    && cmp -s -- "$PENDING" "$NEW_KEY" \
    || die '有一次更换尚未完成，必须使用同一新密钥续做；主密钥和 pending 均未改动'
  RESUME=1
fi
cmp -s -- "$OLD_KEY" "$NEW_KEY" && die '新旧主密钥相同，拒绝更换'
ARCHIVE="$OLD_KEY.old-$(date +%Y%m%d)"
if [ -e "$ARCHIVE" ] || [ -L "$ARCHIVE" ]; then
  [ -f "$ARCHIVE" ] && [ ! -L "$ARCHIVE" ] && cmp -s -- "$OLD_KEY" "$ARCHIVE" \
    || die '当天的旧密钥副本已存在且不同，拒绝覆盖；数据未改动'
  [ "$(stat -c %a -- "$ARCHIVE")" = 600 ] || die '旧密钥副本权限必须为 600'
fi
if [ "$RESUME" -eq 0 ]; then
  RUNNING="$(compose ps --status running --services </dev/null)" || die '无法读取服务状态，数据未改动'
  printf '%s\n' "$RUNNING" | grep -qx worker \
    || die '未发现进行中的更换，数据未改动；请先正常启动服务再执行'
else
  info '续做上次未完成的更换，不再备份；第一次执行前的备份是完整的旧密钥备份。'
fi
confirm '即将备份、停止 app/worker 并更换全部数据的主密钥。请确认已离线保管新旧密钥。' '确认更换主密钥'

STAGE=before
[ "$RESUME" -eq 0 ] || STAGE=changing
STEP=准备
CANDIDATE=""
on_exit() {
  local rc=$?
  trap - EXIT
  [ -z "$CANDIDATE" ] || rm -f -- "$CANDIDATE"
  if [ "$rc" -ne 0 ]; then
    case "$STAGE" in
      before)
        rm -f -- "$PENDING"
        sync -f "$ROOT/secrets"
        info "更换失败（$STEP），数据未改动，主密钥文件未更换；尝试用原密钥重新启动 app、worker。" >&2
        compose up -d --wait app worker </dev/null || info '服务恢复启动失败，请检查 compose logs。' >&2
        ;;
      changing)
        info "更换失败（$STEP），数据可能已部分轮换，app、worker 保持停止，主密钥文件未更换。" >&2
        info '请保留新密钥和 master_key.pending，用同一新密钥重新执行 rotate_key.sh 续做，续做不再备份；第一次执行前的备份是完整的旧密钥备份。不要手工启动服务。' >&2
        ;;
      committed)
        info '数据与主密钥文件更换已完成，当前为新密钥。不要换回旧密钥；请用 docker compose logs 排查启动失败。' >&2
        ;;
    esac
  fi
  exit "$rc"
}
trap on_exit EXIT
if [ "$RESUME" -eq 0 ]; then
  STEP=备份
  bash "$ROOT/scripts/backup_now.sh" --wait
  # 备份成功后才写 pending；文件即状态，与新密钥一并持久化后才能停止服务。
  STEP=记录进行中的更换
  CANDIDATE="$(mktemp "$ROOT/secrets/.rotate-key.XXXXXX")"
  cp -- "$NEW_KEY" "$CANDIDATE"
  chmod 600 "$CANDIDATE"
  chown --reference="$OLD_KEY" "$CANDIDATE"
  sync -f "$CANDIDATE"
  mv -fT -- "$CANDIDATE" "$PENDING"
  CANDIDATE=""
  sync -f "$ROOT/secrets"
fi
CONTAINER_NEW=/run/secrets/master_key.pending
STEP=停止服务
compose stop app worker </dev/null
STAGE=changing
STEP=轮换数据
compose run --rm --no-deps -T worker python -m app.cli rotate-key --old-key-file /run/secrets/master_key --new-key-file "$CONTAINER_NEW" </dev/null
STEP=验证数据
compose run --rm --no-deps -T worker python -m app.cli verify-key --key-file "$CONTAINER_NEW" </dev/null
STEP=保存旧密钥
if [ ! -e "$ARCHIVE" ]; then
  cp --no-clobber --preserve=mode,ownership -- "$OLD_KEY" "$ARCHIVE"
fi
cmp -s -- "$OLD_KEY" "$ARCHIVE" || die '旧密钥副本校验失败，未更换主密钥文件'
chmod 600 "$ARCHIVE"
sync -f "$ARCHIVE"
STEP=切换主密钥
mv -fT -- "$PENDING" "$OLD_KEY"
STAGE=committed
sync -f "$ROOT/secrets"
STEP=启动服务
compose up -d --wait app worker </dev/null
STAGE=done
info '主密钥更换完成。请离线保管新密钥；旧密钥在旧备份全部过期前不得销毁，恢复旧备份时仍需要旧主密钥。'
