#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/lib.sh"
repo="$(cd "$ROOT/.." && pwd)"
version="${1:-}"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-rc[1-9][0-9]*)?$ ]] || die '用法：build_release.sh 版本（可带 -rc1） --ner-models 目录 --embedding-models 目录 [--out 目录]'
shift
out="$repo/release"
ner_models=""
embedding_models=""
while [ "$#" -gt 0 ]; do
  [ "$#" -ge 2 ] || die '缺少参数值'
  case "$1" in
    --out) out="$2" ;;
    --ner-models) [ -z "$ner_models" ] || die '模型目录参数重复'; ner_models="$2" ;;
    --embedding-models) [ -z "$embedding_models" ] || die '向量模型目录参数重复'; embedding_models="$2" ;;
    *) die '未知参数' ;;
  esac
  shift 2
done
[ -n "$ner_models" ] && [ -d "$ner_models" ] || die '必须提供 --ner-models 离线模型目录'
[ -n "$embedding_models" ] && [ -d "$embedding_models" ] || die '必须提供 --embedding-models 离线向量模型目录'
require_cmd git docker tar sha256sum
# Git Bash 自动改写容器内绝对路径；只转换明确的宿主路径（Q-165）。
host_path() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }
export MSYS_NO_PATHCONV=1
[ -z "$(git -C "$(host_path "$repo")" status --porcelain)" ] || die '工作区不干净，请先提交或使用干净的隔离源码副本'
# 以仓库中的清单核对来源，不能信任外部模型目录自带的清单。
manifest="$repo/backend/models/ner/MANIFEST.sha256"
(cd "$ner_models" && sha256sum -c "$manifest") || die '实体识别模型校验失败'
mkdir -p "$repo/backend/models/ner"
for model_file in model.onnx tokenizer.json tokenizer_config.json special_tokens_map.json added_tokens.json gliner_config.json; do
  if [ "$(cd "$ner_models" && pwd)" != "$(cd "$repo/backend/models/ner" && pwd)" ]; then
    cp "$ner_models/$model_file" "$repo/backend/models/ner/$model_file"
  fi
done
(cd "$repo/backend/models/ner" && sha256sum -c MANIFEST.sha256) || die '模型复制后校验失败'
embedding_manifest="$repo/backend/models/embedding/MANIFEST.sha256"
(cd "$embedding_models" && sha256sum -c "$embedding_manifest") || die '向量模型校验失败'
mkdir -p "$repo/backend/models/embedding"
for model_file in model.onnx tokenizer.json; do
  if [ "$(cd "$embedding_models" && pwd)" != "$(cd "$repo/backend/models/embedding" && pwd)" ]; then
    cp "$embedding_models/$model_file" "$repo/backend/models/embedding/$model_file"
  fi
done
(cd "$repo/backend/models/embedding" && sha256sum -c MANIFEST.sha256) || die '向量模型复制后校验失败'
mkdir -p "$out"
out="$(cd "$out" && pwd)"
package="$out/aireview-$version"
[ ! -e "$package" ] && [ ! -e "$package.tar.gz" ] || die '输出已存在，请使用新的输出目录'
docker build --target runtime -t "aireview-backend:$version" "$(host_path "$repo/backend")"
docker build -t "aireview-web:$version" "$(host_path "$repo/frontend")"
docker build -t "aireview-converter:$version" "$(host_path "$repo/converter")"
docker build -t "aireview-db:$version" "$(host_path "$ROOT/db")"
docker run --rm --network none "aireview-backend:$version" sh -ec 'pip check; pg_dump --version | grep " 16\."; python -c "import app.main"; python -c '\''from pathlib import Path; import rapidocr_onnxruntime as r; import cv2,numpy as np; assert len(list((Path(r.__file__).parent / "models").glob("*.onnx"))) == 3; image=np.full((120,600,3),255,dtype=np.uint8); cv2.putText(image,"TEST 123",(20,80),cv2.FONT_HERSHEY_SIMPLEX,2,(0,0,0),3); result,_=r.RapidOCR()(image); assert result; print("OCR offline OK")'\'''
docker run --rm --network none "aireview-web:$version" test -s /srv/frontend/index.html
docker run --rm --network none --read-only --tmpfs /tmp --tmpfs /spool:uid=10002,gid=10001 --cap-drop ALL --security-opt no-new-privileges:true -v "$(host_path "$repo/backend/tests/fixtures/legacy"):/fixtures:ro" "aireview-converter:$version" sh -ec 'soffice -env:UserInstallation=file:///tmp/check --headless --convert-to docx --outdir /tmp /fixtures/contract_basic.doc >/dev/null; test -s /tmp/contract_basic.docx; echo "converter offline OK"'
mkdir -p "$package/images" "$package/scripts" "$package/docs"
images=("aireview-backend:$version" "aireview-web:$version" "aireview-converter:$version" "aireview-db:$version")
docker save -o "$(host_path "$package/images/aireview-images-$version.tar")" "${images[@]}"
docker image inspect --format '{{index .RepoTags 0}} {{.Id}}' "${images[@]}" > "$package/images/images.txt"
sed '/# build-begin/,/# build-end/d' "$ROOT/docker-compose.yml" > "$package/docker-compose.yml"
cp "$ROOT/Caddyfile" "$ROOT/.env.example" "$package/"
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
