#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/lib.sh"
repo="$(cd "$ROOT/.." && pwd)"
version="${1:-}"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-rc[1-9][0-9]*)?$ ]] || die '用法：build_release.sh 版本（可带 -rc1） --models 目录 [--out 目录]'
shift
out="$repo/release"
models=""
while [ "$#" -gt 0 ]; do
  [ "$#" -ge 2 ] || die '缺少参数值'
  case "$1" in
    --out) out="$2" ;;
    --models) [ -z "$models" ] || die '模型目录参数重复'; models="$2" ;;
    *) die '未知参数' ;;
  esac
  shift 2
done
[ -n "$models" ] && [ -d "$models" ] || die '必须提供 --models 离线模型目录'
require_cmd git docker tar sha256sum python3
# Git Bash 自动改写容器内绝对路径；只转换明确的宿主路径（Q-165）。
host_path() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }
export MSYS_NO_PATHCONV=1
[ -z "$(git -C "$(host_path "$repo")" status --porcelain)" ] || die '工作区不干净，请先提交或使用干净的隔离源码副本'
# 模型清单使用 YAML 的 JSON 子集，构建机只需 Python 3.11+ 标准库。
model_commands="$(python3 "$(host_path "$ROOT/scripts/prepare_models.py")" "$(host_path "$repo")" "$(host_path "$models")" | tr -d '\r')" || die '离线模型校验失败'
mkdir -p "$out"
out="$(cd "$out" && pwd)"
package="$out/aireview-$version"
[ ! -e "$package" ] && [ ! -e "$package.tar.gz" ] || die '输出已存在，请使用新的输出目录'
docker build --target runtime -t "aireview-backend:$version" "$(host_path "$repo/backend")"
docker build -t "aireview-web:$version" "$(host_path "$repo/frontend")"
docker build -t "aireview-converter:$version" "$(host_path "$repo/converter")"
docker build -t "aireview-db:$version" "$(host_path "$ROOT/db")"
if printf '%s\n' "$model_commands" | grep -q '^transcriber'; then
  docker build -t "aireview-transcriber:$version" "$(host_path "$repo/transcriber")"
fi
# 每项按清单断网冒烟。转写在独立镜像内执行，不在 worker 加载。
while IFS=$'\t' read -r model_image smoke; do
  docker run --rm --network none -v "$(host_path "$ROOT/models.yaml"):/etc/aireview/models.yaml:ro" -e LOCAL_MODELS_FILE=/etc/aireview/models.yaml "aireview-$model_image:$version" sh -ec "$smoke"
done <<< "$model_commands"

docker run --rm --network none "aireview-backend:$version" sh -ec 'pip check; pg_dump --version | grep " 16\."; python -c "import app.main"; python -c '\''from pathlib import Path; import rapidocr_onnxruntime as r; import cv2,numpy as np; assert len(list((Path(r.__file__).parent / "models").glob("*.onnx"))) == 3; image=np.full((120,600,3),255,dtype=np.uint8); cv2.putText(image,"TEST 123",(20,80),cv2.FONT_HERSHEY_SIMPLEX,2,(0,0,0),3); result,_=r.RapidOCR()(image); assert result; print("OCR offline OK")'\'''
docker run --rm --network none "aireview-web:$version" test -s /srv/frontend/index.html
docker run --rm --network none --read-only --tmpfs /tmp --tmpfs /spool:uid=10002,gid=10001 --cap-drop ALL --security-opt no-new-privileges:true -v "$(host_path "$repo/backend/tests/fixtures/legacy"):/fixtures:ro" "aireview-converter:$version" sh -ec 'soffice -env:UserInstallation=file:///tmp/check --headless --convert-to docx --outdir /tmp /fixtures/contract_basic.doc >/dev/null; test -s /tmp/contract_basic.docx; echo "converter offline OK"'
mkdir -p "$package/images" "$package/scripts" "$package/docs"
images=("aireview-backend:$version" "aireview-web:$version" "aireview-converter:$version" "aireview-db:$version")
if printf '%s\n' "$model_commands" | grep -q '^transcriber'; then
  images+=("aireview-transcriber:$version")
fi
docker save -o "$(host_path "$package/images/aireview-images-$version.tar")" "${images[@]}"
docker image inspect --format '{{index .RepoTags 0}} {{.Id}}' "${images[@]}" > "$package/images/images.txt"
sed -e '/# build-begin/,/# build-end/d' -e 's|../transcriber/models/asr/MANIFEST.sha256|./model-manifests/asr.sha256|g' "$ROOT/docker-compose.yml" > "$package/docker-compose.yml"
cp "$ROOT/Caddyfile" "$ROOT/.env.example" "$ROOT/models.yaml" "$package/"
mkdir -p "$package/licenses" "$package/model-manifests"
cp -R "$ROOT/licenses/models" "$package/licenses/"
cp "$repo/transcriber/models/asr/MANIFEST.sha256" "$package/model-manifests/asr.sha256"
# 未启用 asr 的构建，安装时同样默认关闭，避免请求不存在的镜像。
if ! printf '%s\n' "$model_commands" | grep -q '^transcriber'; then
  sed -i 's/^COMPOSE_PROFILES=.*/COMPOSE_PROFILES=/' "$package/.env.example"
fi
# 只列出模板与片段，绝不复制生成的下载页、证书或 secrets/。
mkdir -p "$package/caddy"
cp "$ROOT/caddy/"{development.global,development.tls,custom.global,custom.tls,start.sh,cert.html.template} "$package/caddy/"
cp "$ROOT/scripts/"{lib.sh,install.sh,upgrade.sh,backup_now.sh,restore.sh} "$package/scripts/"
chmod +x "$package/scripts/"*.sh
if [ -f "$repo/docs/ops/运维说明.md" ]; then cp "$repo/docs/ops/运维说明.md" "$package/docs/"; else info '警告：M8-04 运维说明尚未生成'; fi
printf '%s\n' "$version" > "$package/VERSION"
(cd "$package" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)
# M8-05：归档内属主统一为 root，避免以 root 解包时保留开发机的 uid（脚本会以 root 执行）。
tar -czf "$package.tar.gz" --owner=0 --group=0 --numeric-owner -C "$out" "aireview-$version"
info "发布包完成：$package.tar.gz"
