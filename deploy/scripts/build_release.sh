#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/lib.sh"
repo="$(cd "$ROOT/.." && pwd)"
version="${1:-}"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-rc[1-9][0-9]*)?$ ]] || die '用法：build_release.sh 版本（可带 -rc1） --models 目录 [--out 目录] [--base 上一版发布包目录]'
shift
out="$repo/release"
models="" base=""
while [ "$#" -gt 0 ]; do
  [ "$#" -ge 2 ] || die '缺少参数值'
  case "$1" in
    --out) out="$2" ;;
    --models) [ -z "$models" ] || die '模型目录参数重复'; models="$2" ;;
    --base) [ -z "$base" ] || die '基准包参数重复'; base="$2" ;;
    *) die '未知参数' ;;
  esac
  shift 2
done
[ -n "$models" ] && [ -d "$models" ] || die '必须提供 --models 离线模型目录'
require_cmd git docker tar sha256sum
# 构建机 Python 3.11+（V33-A-01）：以能真正执行为准，Windows 应用商店的占位程序会执行失败而被跳过；PYTHON=命令 可指定。
py_ok() { "$@" -c 'import sys; sys.exit(sys.version_info < (3, 11))' >/dev/null 2>&1 </dev/null; }
if [ -n "${PYTHON:-}" ]; then
  read -r -a py <<< "$PYTHON"
  py_ok "${py[@]}" || die "PYTHON 指定的命令不可用或版本低于 3.11：$PYTHON"
else
  py=()
  for candidate in python3 python 'py -3'; do
    read -r -a try <<< "$candidate"
    if command -v "${try[0]}" >/dev/null 2>&1 && py_ok "${try[@]}"; then py=("${try[@]}"); break; fi
  done
  [ "${#py[@]}" -gt 0 ] || die '找不到可用的 Python 3.11+（已试 python3、python、py -3），可用 PYTHON=命令 指定'
fi
# Git Bash 自动改写容器内绝对路径；只转换明确的宿主路径（Q-165）。
host_path() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }
export MSYS_NO_PATHCONV=1
[ -z "$(git -C "$(host_path "$repo")" status --porcelain)" ] || die '工作区不干净，请先提交或使用干净的隔离源码副本'
# 模型清单使用 YAML 的 JSON 子集，构建机只需 Python 3.11+ 标准库。
model_commands="$("${py[@]}" "$(host_path "$ROOT/scripts/prepare_models.py")" "$(host_path "$repo")" "$(host_path "$models")" | tr -d '\r')" || die '离线模型校验失败'
names=(backend web converter db)
if printf '%s\n' "$model_commands" | grep -q '^transcriber'; then names+=(transcriber); fi
if printf '%s\n' "$model_commands" | grep -q '^translator'; then names+=(translator); fi
# 增量包（V33-A-02）：按各镜像构建目录相对基准版本标签的源码差异决定重建或沿用，不按镜像 ID（构建不可复现）。
# 模型文件不进仓库，但 MANIFEST 在构建目录里，换模型必然产生差异；backend/tests 的改动也触发重建，宁多勿漏。
declare -A image_dir=([backend]=backend [web]=frontend [converter]=converter [db]=deploy/db [transcriber]=transcriber [translator]=translator)
declare -A base_id=() base_from=()
build=() reuse=() base_version=""
if [ -n "$base" ]; then
  [ -f "$base/VERSION" ] && [ -f "$base/images/images.txt" ] || die '--base 须为上一版发布包解包后的目录（含 VERSION 与 images/images.txt）'
  base_version="$(tr -d '\r\n' < "$base/VERSION")"
  [[ "$base_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-rc[1-9][0-9]*)?$ ]] || die '基准包 VERSION 格式不正确'
  [ "$base_version" != "$version" ] || die '新版本号与基准版本相同'
  git -C "$(host_path "$repo")" rev-parse -q --verify "refs/tags/v$base_version^{commit}" >/dev/null || die "本地缺少基准版本标签 v$base_version"
  while read -r tag id; do
    [ -n "${id:-}" ] || continue
    name="${tag#aireview-}"; name="${name%%:*}"
    base_id[$name]="$id"; base_from[$name]="$base_version"
  done < <(tr -d '\r' < "$base/images/images.txt")
  # 基准包本身是增量包时，它沿用的镜像可以继续沿用（服务器上按 ID 核对）。
  if [ -f "$base/images/reuse.txt" ]; then
    while read -r name from id; do
      [ -n "${id:-}" ] || continue
      base_id[$name]="$id"; base_from[$name]="$from"
    done < <(tr -d '\r' < "$base/images/reuse.txt")
  fi
  for name in "${names[@]}"; do
    if [ -n "${base_id[$name]:-}" ] && git -C "$(host_path "$repo")" diff --quiet "v$base_version" HEAD -- "${image_dir[$name]}"; then
      reuse+=("$name")
    else
      build+=("$name")
    fi
  done
  info "沿用 $base_version 的镜像：${reuse[*]:-无}；重建：${build[*]:-无}"
else
  build=("${names[@]}")
fi
built() { local n; for n in "${build[@]}"; do [ "$n" = "$1" ] && return 0; done; return 1; }
mkdir -p "$out"
out="$(cd "$out" && pwd)"
package_name="aireview-$version${base_version:+-from-$base_version}"
package="$out/$package_name"
[ ! -e "$package" ] && [ ! -e "$package.tar.gz" ] || die '输出已存在，请使用新的输出目录'
if built backend; then docker build --target runtime -t "aireview-backend:$version" "$(host_path "$repo/backend")"; fi
if built web; then docker build -t "aireview-web:$version" "$(host_path "$repo/frontend")"; fi
if built converter; then docker build -t "aireview-converter:$version" "$(host_path "$repo/converter")"; fi
if built db; then docker build -t "aireview-db:$version" "$(host_path "$ROOT/db")"; fi
if built transcriber; then docker build -t "aireview-transcriber:$version" "$(host_path "$repo/transcriber")"; fi
if built translator; then docker build -t "aireview-translator:$version" "$(host_path "$repo/translator")"; fi
# 每项按清单断网冒烟；沿用的镜像已在基准版本冒烟过，不再重复。转写在独立镜像内执行，不在 worker 加载。
while IFS=$'\t' read -r model_image smoke; do
  built "$model_image" || continue
  docker run --rm --network none -v "$(host_path "$ROOT/models.yaml"):/etc/aireview/models.yaml:ro" -e LOCAL_MODELS_FILE=/etc/aireview/models.yaml "aireview-$model_image:$version" sh -ec "$smoke"
done <<< "$model_commands"

if built backend; then
  docker run --rm --network none "aireview-backend:$version" sh -ec 'pip check; pg_dump --version | grep " 16\."; python -c "import app.main"; python -c '\''from pathlib import Path; import rapidocr_onnxruntime as r; import cv2,numpy as np; assert len(list((Path(r.__file__).parent / "models").glob("*.onnx"))) == 3; image=np.full((120,600,3),255,dtype=np.uint8); cv2.putText(image,"TEST 123",(20,80),cv2.FONT_HERSHEY_SIMPLEX,2,(0,0,0),3); result,_=r.RapidOCR()(image); assert result; print("OCR offline OK")'\'''
fi
if built web; then docker run --rm --network none "aireview-web:$version" test -s /srv/frontend/index.html; fi
if built converter; then
  docker run --rm --network none --read-only --tmpfs /tmp --tmpfs /spool:uid=10002,gid=10001 --cap-drop ALL --security-opt no-new-privileges:true -v "$(host_path "$repo/backend/tests/fixtures/legacy"):/fixtures:ro" "aireview-converter:$version" sh -ec 'soffice -env:UserInstallation=file:///tmp/check --headless --convert-to docx --outdir /tmp /fixtures/contract_basic.doc >/dev/null; test -s /tmp/contract_basic.docx; echo "converter offline OK"'
fi
mkdir -p "$package/images" "$package/scripts" "$package/docs"
images=()
for name in "${build[@]}"; do images+=("aireview-$name:$version"); done
# 只有部署文件变化时没有镜像 tar，images.txt 为空。
if [ "${#images[@]}" -gt 0 ]; then
  docker save -o "$(host_path "$package/images/aireview-images-$version.tar")" "${images[@]}"
  docker image inspect --format '{{index .RepoTags 0}} {{.Id}}' "${images[@]}" > "$package/images/images.txt"
else
  : > "$package/images/images.txt"
fi
# 每行“镜像名 基准版本 镜像 ID”；升级时按 ID 核对服务器已有该镜像，再打上新版本标签。
if [ -n "$base" ]; then
  : > "$package/images/reuse.txt"
  for name in "${reuse[@]}"; do printf '%s %s %s\n' "$name" "${base_from[$name]}" "${base_id[$name]}" >> "$package/images/reuse.txt"; done
fi
sed -e '/# build-begin/,/# build-end/d' -e 's|../transcriber/models/asr/MANIFEST.sha256|./model-manifests/asr.sha256|g' -e 's|../translator/models/mt/MANIFEST.sha256|./model-manifests/mt.sha256|g' "$ROOT/docker-compose.yml" > "$package/docker-compose.yml"
cp "$ROOT/Caddyfile" "$ROOT/.env.example" "$ROOT/models.yaml" "$package/"
mkdir -p "$package/licenses" "$package/model-manifests"
cp -R "$ROOT/licenses/models" "$package/licenses/"
cp "$repo/transcriber/models/asr/MANIFEST.sha256" "$package/model-manifests/asr.sha256"
cp "$repo/translator/models/mt/MANIFEST.sha256" "$package/model-manifests/mt.sha256"
# 未启用 asr 的构建，安装时同样默认关闭，避免请求不存在的镜像。
if ! printf '%s\n' "$model_commands" | grep -q '^transcriber'; then
  sed -i 's/^COMPOSE_PROFILES=.*/COMPOSE_PROFILES=/' "$package/.env.example"
fi
# 只列出模板与片段，绝不复制生成的下载页、证书或 secrets/。
mkdir -p "$package/caddy"
cp "$ROOT/caddy/"{development.global,development.tls,custom.global,custom.tls,start.sh,cert.html.template} "$package/caddy/"
cp "$ROOT/scripts/"{lib.sh,install.sh,upgrade.sh,backup_now.sh,restore.sh,rotate_key.sh} "$package/scripts/"
chmod +x "$package/scripts/"*.sh
if [ -f "$repo/docs/ops/运维说明.md" ]; then cp "$repo/docs/ops/运维说明.md" "$package/docs/"; else info '警告：M8-04 运维说明尚未生成'; fi
printf '%s\n' "$version" > "$package/VERSION"
(cd "$package" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)
# M8-05：归档内属主统一为 root，避免以 root 解包时保留开发机的 uid（脚本会以 root 执行）。
tar -czf "$package.tar.gz" --owner=0 --group=0 --numeric-owner -C "$out" "$package_name"
info "发布包完成：$package.tar.gz"
