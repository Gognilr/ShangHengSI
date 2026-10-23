#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/lib.sh"
repo="$(cd "$ROOT/.." && pwd)"
version="${1:-}"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die '用法：build_release.sh 版本 [--out 目录]'
shift
out="$repo/release"
if [ "$#" -gt 0 ]; then
  [ "$#" -eq 2 ] && [ "$1" = --out ] || die '用法：build_release.sh 版本 [--out 目录]'
  out="$2"
fi
require_cmd git docker tar sha256sum
# Git Bash 自动改写容器内绝对路径；只转换明确的宿主路径（Q-165）。
host_path() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }
export MSYS_NO_PATHCONV=1
[ -z "$(git -C "$repo" status --porcelain)" ] || die '工作区不干净，请先提交或使用干净的隔离源码副本'
mkdir -p "$out"
out="$(cd "$out" && pwd)"
package="$out/aireview-$version"
[ ! -e "$package" ] && [ ! -e "$package.tar.gz" ] || die '输出已存在，请使用新的输出目录'
docker build --target runtime -t "aireview-backend:$version" "$(host_path "$repo/backend")"
docker build -t "aireview-web:$version" "$(host_path "$repo/frontend")"
docker build -t "aireview-converter:$version" "$(host_path "$repo/converter")"
docker pull postgres:16.15
docker run --rm --network none "aireview-backend:$version" sh -ec 'pip check; pg_dump --version | grep " 16\."; python -c "import app.main"; python -c '\''from pathlib import Path; import rapidocr_onnxruntime as r; import cv2,numpy as np; assert len(list((Path(r.__file__).parent / "models").glob("*.onnx"))) == 3; image=np.full((120,600,3),255,dtype=np.uint8); cv2.putText(image,"TEST 123",(20,80),cv2.FONT_HERSHEY_SIMPLEX,2,(0,0,0),3); result,_=r.RapidOCR()(image); assert result; print("OCR offline OK")'\'''
docker run --rm --network none "aireview-web:$version" test -s /srv/frontend/index.html
docker run --rm --network none --read-only --tmpfs /tmp --tmpfs /spool:uid=10002,gid=10001 --cap-drop ALL --security-opt no-new-privileges:true -v "$(host_path "$repo/backend/tests/fixtures/legacy"):/fixtures:ro" "aireview-converter:$version" sh -ec 'soffice -env:UserInstallation=file:///tmp/check --headless --convert-to docx --outdir /tmp /fixtures/contract_basic.doc >/dev/null; test -s /tmp/contract_basic.docx; echo "converter offline OK"'
mkdir -p "$package/images" "$package/scripts" "$package/docs"
images=("aireview-backend:$version" "aireview-web:$version" "aireview-converter:$version" postgres:16.15)
docker save -o "$(host_path "$package/images/aireview-images-$version.tar")" "${images[@]}"
docker image inspect --format '{{index .RepoTags 0}} {{.Id}}' "${images[@]}" > "$package/images/images.txt"
sed '/# build-begin/,/# build-end/d' "$ROOT/docker-compose.yml" > "$package/docker-compose.yml"
cp "$ROOT/Caddyfile" "$ROOT/.env.example" "$package/"
cp "$ROOT/scripts/"{lib.sh,install.sh,upgrade.sh,backup_now.sh,restore.sh} "$package/scripts/"
chmod +x "$package/scripts/"*.sh
if [ -f "$repo/docs/ops/运维说明.md" ]; then cp "$repo/docs/ops/运维说明.md" "$package/docs/"; else info '警告：M8-04 运维说明尚未生成'; fi
printf '%s\n' "$version" > "$package/VERSION"
(cd "$package" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)
tar -czf "$package.tar.gz" -C "$out" "aireview-$version"
info "发布包完成：$package.tar.gz"
