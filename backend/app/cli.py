"""运维命令：python -m app.cli <命令>。

  gen-key --out PATH   生成主密钥文件（32 字节随机值，权限 600）。目标文件已存在时拒绝覆盖。
"""
import argparse
import os
import sys

from app.security.crypto import KEY_SIZE


def gen_key(out: str) -> None:
    """生成主密钥。用 O_EXCL 创建，绝不覆盖已有密钥：覆盖主密钥会导致全部加密数据无法解密。"""
    key = os.urandom(KEY_SIZE)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
        f.flush()
        os.fsync(f.fileno())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="企业商务文件AI智能审查助手 运维命令")
    sub = parser.add_subparsers(dest="command", required=True)

    p_key = sub.add_parser("gen-key", help="生成主密钥文件（32 字节，权限 600，不覆盖已有文件）")
    p_key.add_argument("--out", required=True, help="输出文件路径")

    args = parser.parse_args(argv)

    if args.command == "gen-key":
        try:
            gen_key(args.out)
        except FileExistsError:
            print(f"错误：{args.out} 已存在，拒绝覆盖。覆盖主密钥会导致已加密的数据全部无法解密。", file=sys.stderr)
            return 1
        print(f"已生成主密钥：{args.out}。请立即制作离线副本并交专人保管。")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
