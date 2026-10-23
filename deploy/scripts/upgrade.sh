#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/lib.sh"

target=/data/apps/aireview skip_backup=0 stage="前置检查"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --target)
      [ "$#" -ge 2 ] && [ -n "$2" ] && [[ "$2" != --* ]] || die '--target 缺少参数值'
      target="$2"; shift 2 ;;
    --skip-backup) skip_backup=1; shift ;;
    --yes) ASSUME_YES=1; shift ;;
    -h|--help) info '用法：sudo ./scripts/upgrade.sh [--target /data/apps/aireview] [--skip-backup] [--yes]'; exit 0 ;;
    *) die "未知参数 $1" ;;
  esac
done
trap 'die "升级在${stage}阶段失败；未自动回退，旧镜像仍保留。请检查服务状态，用 PVE 快照恢复到升级前。"' ERR
require_root
require_cmd docker sha256sum findmnt cp cmp awk mktemp
docker compose version >/dev/null 2>&1 || die "需要 Docker Compose v2"
[ -d "$target" ] || die "目标目录不存在"
target="$(cd "$target" && pwd -P)"
[ "$target" != "$(cd "$ROOT" && pwd -P)" ] || die "请在新发布包目录运行升级，不能使用目标安装目录"
[ -f "$target/.env" ] && [ -f "$target/secrets/master_key" ] || die "目标目录尚未安装系统"
package_root="$ROOT"
ROOT="$target"
backup_dir="$(resolve_path "$(env_get BACKUP_HOST_DIR)")"
old_version="$(env_get APP_VERSION)"
ROOT="$package_root"
check_storage "$target" "$backup_dir"
check_release
version="$(release_version)"
[ "$version" != "$old_version" ] || die "新版本号与已安装版本相同"
confirm '升级前请先在 PVE 中为虚拟机创建快照。已创建请输入：已创建快照' '已创建快照'
stage="升级前备份"
if [ "$skip_backup" -eq 0 ]; then
  bash "$target/scripts/backup_now.sh" --wait </dev/null || die "升级前备份失败，未继续升级；请检查 worker 与备份记录"
else
  info '已指定 --skip-backup，跳过升级前备份。'
fi
stage="导入镜像"
load_release_images "$version"
stage="更新部署文件"
cp "$ROOT/docker-compose.yml" "$ROOT/VERSION" "$ROOT/.env.example" "$target/"
cp -R "$ROOT/scripts/." "$target/scripts/"
if [ -d "$ROOT/docs" ]; then mkdir -p "$target/docs"; cp -R "$ROOT/docs/." "$target/docs/"; fi
if ! cmp -s "$ROOT/Caddyfile" "$target/Caddyfile"; then
  cp "$ROOT/Caddyfile" "$target/Caddyfile.new"
  info 'Caddyfile 有变化，已另存 Caddyfile.new，请人工比较并保留公司证书配置。'
fi
ROOT="$target"
env_set APP_VERSION "$version"
stage="启动新版本"
compose up -d --no-build --pull never </dev/null
wait_healthy app 300
info "升级完成：$old_version → $version。旧镜像已保留。"
