#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/lib.sh"

usage() {
  info '用法：sudo ./scripts/install.sh [--site <服务器IP或内网域名>] [--admin admin] [--backup-dir /data/apps/aireview/backup] [--yes]'
}
site="" admin=admin backup_dir="$ROOT/backup" stage="前置检查"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --site|--admin|--backup-dir)
      [ "$#" -ge 2 ] && [ -n "$2" ] && [[ "$2" != --* ]] || die "$1 缺少参数值"
      case "$1" in --site) site="$2" ;; --admin) admin="$2" ;; --backup-dir) backup_dir="$2" ;; esac
      shift 2 ;;
    --yes) ASSUME_YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "未知参数 $1" ;;
  esac
done
trap 'die "安装在${stage}阶段失败；可能已有部分配置，未自动删除。请检查服务状态，参照运维说明处理后再运行。"' ERR
require_root
require_cmd docker sha256sum findmnt ss getent groupadd chown chmod od tr awk mktemp curl hostname
docker compose version >/dev/null 2>&1 || die "需要 Docker Compose v2"
[ ! -e "$ROOT/.env" ] && [ ! -e "$ROOT/secrets/master_key" ] && [ ! -e "$ROOT/secrets/backup_password" ] || die "系统已安装，升级请用 upgrade.sh"
backup_dir="$(resolve_path "$backup_dir")"
check_storage "$backup_dir"
check_release
check_site_ca
version="$(release_version)"
if [ -z "$site" ]; then
  for candidate in $(hostname -I); do
    if [[ "$candidate" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] && [[ "$candidate" != 127.* ]]; then
      site="$candidate"; break
    fi
  done
fi
# Q-164：只接收单个主机名/IP，不允许 URL、端口及 .env 插值字符。
[[ "$site" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "访问地址须为证书对应的固定 IPv4 私网地址"
[[ "$admin" =~ ^[a-zA-Z0-9_.@-]{1,50}$ ]] || die "管理员用户名格式不正确"
[[ "$backup_dir" =~ ^/[a-zA-Z0-9_./-]+$ ]] || die "备份目录须为不含空格和插值字符的绝对路径"
[ -z "$(ss -H -ltn '( sport = :80 or sport = :443 )')" ] || die "80 或 443 端口已被占用"
group_entry="$(getent group aireview || true)"
if [ -n "$group_entry" ]; then
  [ "$(printf '%s' "$group_entry" | cut -d: -f3)" = 10001 ] || die "aireview 组的 gid 不是 10001"
else
  [ -z "$(getent group 10001 || true)" ] || die "gid 10001 已被其他组占用"
  groupadd --gid 10001 aireview
fi
stage="导入镜像"
load_release_images "$version"
stage="生成配置与密钥"
umask 077
mkdir -p "$backup_dir" "$ROOT/secrets" "$ROOT/certs"
chown 10001:10001 "$backup_dir" "$ROOT/secrets"
chmod 2750 "$backup_dir"
chmod 700 "$ROOT/secrets"
cp "$ROOT/.env.example" "$ROOT/.env"
chown root:root "$ROOT/.env"
chmod 600 "$ROOT/.env"
env_set POSTGRES_PASSWORD "$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
env_set APP_VERSION "$version"
env_set SITE_ADDRESS "$site"
prepare_cert_page "$site"
env_set HTTP_PORT 80
env_set HTTPS_PORT 443
env_set BACKUP_HOST_DIR "$backup_dir"
docker run --rm --pull never -v "$ROOT/secrets:/out" "aireview-backend:$version" python -m app.cli gen-key --out /out/master_key </dev/null
docker run --rm --pull never -v "$ROOT/secrets:/out" "aireview-backend:$version" python -c 'import os,secrets; f=os.open("/out/backup_password",os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600); os.write(f,secrets.token_hex(16).encode()); os.fsync(f); os.close(f)' </dev/null
chown 10001:10001 "$ROOT/secrets/master_key" "$ROOT/secrets/backup_password"
chmod 600 "$ROOT/secrets/master_key" "$ROOT/secrets/backup_password"
info '请把 secrets/master_key 和 secrets/backup_password 拷到离线介质交专人保管，丢失任一个都无法恢复备份。'
stage="启动服务"
compose up -d --no-build --pull never </dev/null
wait_healthy app 300
require_running worker
check_https "$site"
stage="创建管理员"
compose exec -T app python -m app.cli create-admin --username "$admin" </dev/null
info "安装完成，访问 https://$site/"
info '首次登录修改密码 → 设置 - 模型填写接口与密钥 → 设置 - 数据确认备份时间并点一次“立即备份” → 配置异地拉取（必做）。'
info "启动 / 停止 / 状态：cd $ROOT；docker compose up -d / docker compose stop / docker compose ps"
