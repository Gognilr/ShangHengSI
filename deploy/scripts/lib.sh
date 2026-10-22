# 部署脚本的公共函数（设计 docs/design/M8.md 第二节）。由各脚本在设置 ROOT 之后 source：
#   ROOT="$(cd "$(dirname "$0")/.." && pwd)"
#   source "$ROOT/scripts/lib.sh"
# ROOT 在仓库里是 deploy/，在发布包里是包根目录。已有函数的名字和行为固定，后续任务只追加。
# 约定：输出中文；不输出密码、主密钥、备份密码、数据库连接串、API 密钥；不打开命令跟踪（xtrace）。

# 运维说明中服务器检查所在的章节，检查不通过时提示
OPS_DOC_STORAGE="运维说明第 2 章“服务器检查”"

die() {
  echo "错误：$*" >&2
  exit 1
}

info() {
  echo "$*"
}

require_root() {
  [ "$(id -u)" -eq 0 ] || die "请用 sudo 运行"
}

require_cmd() {
  local c
  for c in "$@"; do
    command -v "$c" >/dev/null 2>&1 || die "缺少命令 $c"
  done
}

compose() {
  docker compose --project-directory "$ROOT" -f "$ROOT/docker-compose.yml" "$@"
}

# 从 $ROOT/.env 读取一个值（取最后一次出现的 KEY=...）。不 source .env，避免执行其中的内容。
env_get() {
  local key="$1" line value=""
  [ -f "$ROOT/.env" ] || { printf '%s' ""; return 0; }
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    case "$line" in
      "$key="*) value="${line#"$key="}" ;;
    esac
  done < "$ROOT/.env"
  # 去掉成对的引号
  if [ "${#value}" -ge 2 ]; then
    case "$value" in
      \"*\") value="${value:1:${#value}-2}" ;;
      \'*\') value="${value:1:${#value}-2}" ;;
    esac
  fi
  printf '%s' "$value"
}

# 要求原样输入指定文字；ASSUME_YES=1 时跳过（只用于演练和自动化）
confirm() {
  local prompt="$1" expected="$2" answer=""
  if [ "${ASSUME_YES:-0}" = "1" ]; then
    info "$prompt（--yes：已跳过确认）"
    return 0
  fi
  info "$prompt"
  printf '请输入“%s”继续，其他输入取消：' "$expected"
  IFS= read -r answer || true
  [ "$answer" = "$expected" ] || die "未确认，已取消"
}

# 等服务的健康检查变为 healthy，超时 die
wait_healthy() {
  local service="$1" timeout="${2:-180}" waited=0 cid status
  while :; do
    cid="$(compose ps -q "$service" 2>/dev/null || true)"
    if [ -n "$cid" ]; then
      status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null || true)"
      [ "$status" = "healthy" ] && return 0
    fi
    [ "$waited" -ge "$timeout" ] && die "等待服务 $service 就绪超时（${timeout} 秒）；查看日志：docker compose logs $service"
    sleep 3
    waited=$((waited + 3))
  done
}

# PATH 所在文件系统是根分区时 die（Q-131）。PATH 尚不存在时检查最近的已存在上级目录。
require_on_data_disk() {
  local path="$1" probe target
  probe="$path"
  while [ ! -e "$probe" ]; do
    probe="$(dirname "$probe")"
  done
  target="$(findmnt -n -o TARGET --target "$probe" 2>/dev/null | head -n 1)"
  [ -n "$target" ] || die "无法确定 $path 所在的文件系统"
  [ "$target" != "/" ] || die "$path 位于根分区上，业务数据必须放在数据盘 /data 上（见 $OPS_DOC_STORAGE）"
}

# containerd 的 root（/etc/containerd/config.toml 顶层的 root，缺省 /var/lib/containerd）。
# CONTAINERD_CONFIG 只供测试指定配置文件。
containerd_root() {
  local root="" config="${CONTAINERD_CONFIG:-/etc/containerd/config.toml}"
  if [ -f "$config" ]; then
    root="$(awk '/^[[:space:]]*\[/ { exit } /^[[:space:]]*root[[:space:]]*=/ { sub(/^[^=]*=[[:space:]]*/, ""); gsub(/["'"'"']/, ""); sub(/[[:space:]]*(#.*)?$/, ""); print; exit }' "$config")"
  fi
  printf '%s' "${root:-/var/lib/containerd}"
}

# 运行前统一检查（Q-131）：/data 已挂载；安装目录、给出的其他目录、Docker Root Dir、containerd root 都不在根分区上
check_storage() {
  local p docker_root ctr_root
  findmnt -n /data >/dev/null 2>&1 || die "/data 未挂载，请先挂载数据盘（见 $OPS_DOC_STORAGE）"
  for p in "$ROOT" "$@"; do
    require_on_data_disk "$p"
  done
  docker_root="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null)" || die "无法连接 Docker，请确认 Docker 已启动"
  [ -n "$docker_root" ] || die "无法读取 Docker 的存储位置"
  require_on_data_disk "$docker_root"
  ctr_root="$(containerd_root)"
  # containerd 未使用（目录不存在）时不检查
  if [ -d "$ctr_root" ]; then
    require_on_data_disk "$ctr_root"
  fi
}

# .env 中的路径：相对路径按 $ROOT 解析（compose 也以项目目录为基准）
resolve_path() {
  case "$1" in
    /*) printf '%s' "$1" ;;
    *) printf '%s' "$ROOT/${1#./}" ;;
  esac
}
