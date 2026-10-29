#!/bin/sh
set -eu
# 没有任何 CA 文件时仅供开发；部分缺失须拒绝启动，不能悄悄换根。
CADDY_CA_MODE=development
if [ -e /run/site-ca/root.key ] || [ -L /run/site-ca/root.key ]; then
    echo '错误：请密钥保管人取走 root.key 离线保存，并从服务器删除。' >&2
    exit 1
fi
if [ -n "$(ls -A /run/site-ca 2>/dev/null)" ]; then
    for file in root.crt intermediate.crt intermediate.key root.sha256; do
        [ -s "/run/site-ca/$file" ] || { echo '错误：secrets/ca/ 证书文件不齐全。' >&2; exit 1; }
    done
    [ -s /etc/caddy/fragments/public/index.html ] || { echo '错误：缺少证书下载说明页，请按运维说明生成。' >&2; exit 1; }
    CADDY_CA_MODE=custom
fi
export CADDY_CA_MODE
exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
