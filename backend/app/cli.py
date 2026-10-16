"""运维命令：python -m app.cli <命令>。

  gen-key --out PATH            生成主密钥文件（32 字节随机值，权限 600）。目标文件已存在时拒绝覆盖。
  create-admin --username NAME  创建管理员，随机初始密码只显示一次，首次登录强制修改（SPEC 20.5）。
  set-model --base-url URL --model NAME [--extra-params JSON] [--api-key-stdin]
                                开发用：写入模型设置；API 密钥只从标准输入读取，不出现在命令行和输出中。
"""
import argparse
import os
import secrets
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


class UserExistsError(Exception):
    pass


def random_password() -> str:
    """随机初始密码：16 个 URL 安全字符，保证同时含字母和数字。"""
    from app.security.passwords import is_strong_password

    while True:
        candidate = secrets.token_urlsafe(12)
        if is_strong_password(candidate):
            return candidate


def create_admin(username: str, display_name: str) -> str:
    """创建管理员并返回初始密码。用户名已存在时抛出 UserExistsError。需要 DATABASE_URL。"""
    from sqlalchemy import select

    from app.audit import service as audit
    from app.db.base import utcnow
    from app.db.models import User
    from app.db.session import get_sessionmaker
    from app.security.passwords import hash_password

    password = random_password()
    with get_sessionmaker()() as db:
        if db.scalar(select(User.id).where(User.username == username)) is not None:
            raise UserExistsError(username)
        now = utcnow()
        user = User(
            username=username,
            display_name=display_name,
            password_hash=hash_password(password),
            role="admin",
            must_change_password=True,
            created_at=now,
            updated_at=now,
        )
        db.add(user)
        db.flush()
        audit.record(
            db, "user_create", target_type="user", target_id=str(user.id), detail={"role": "admin", "via": "cli"}
        )
        db.commit()
    return password


def set_model(base_url: str, model: str, extra_params: dict | None, api_key: str | None) -> None:
    """写入模型接口地址、模型名称、附加请求参数和 API 密钥（加密）。参数为 None 的项不改。需要 DATABASE_URL。"""
    from app.admin.settings_store import set_setting
    from app.db.session import get_sessionmaker
    from app.security import crypto

    with get_sessionmaker()() as db:
        set_setting(db, "model.base_url", base_url, None)
        set_setting(db, "model.name", model, None)
        if extra_params is not None:
            set_setting(db, "model.extra_params", extra_params, None)
        if api_key is not None:
            set_setting(db, "model.api_key_enc", crypto.encrypt_to_b64(api_key), None)
        db.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="企业商务文件AI智能审查助手 运维命令")
    sub = parser.add_subparsers(dest="command", required=True)

    p_key = sub.add_parser("gen-key", help="生成主密钥文件（32 字节，权限 600，不覆盖已有文件）")
    p_key.add_argument("--out", required=True, help="输出文件路径")

    p_admin = sub.add_parser("create-admin", help="创建管理员（随机初始密码只显示一次）")
    p_admin.add_argument("--username", required=True, help="用户名，最多 50 个字符")
    p_admin.add_argument("--display-name", default="管理员", help="显示名，默认“管理员”")

    p_model = sub.add_parser("set-model", help="开发用：设置模型接口地址、模型名称和 API 密钥")
    p_model.add_argument("--base-url", required=True, help="接口地址，如 https://api.example.com/v1")
    p_model.add_argument("--model", required=True, help="模型名称")
    p_model.add_argument("--extra-params", help="附加请求参数，JSON 对象，原样合并进请求体")
    p_model.add_argument("--api-key-stdin", action="store_true", help="从标准输入读取 API 密钥（一行）")

    args = parser.parse_args(argv)

    if args.command == "gen-key":
        try:
            gen_key(args.out)
        except FileExistsError:
            print(f"错误：{args.out} 已存在，拒绝覆盖。覆盖主密钥会导致已加密的数据全部无法解密。", file=sys.stderr)
            return 1
        print(f"已生成主密钥：{args.out}。请立即制作离线副本并交专人保管。")
        return 0

    if args.command == "create-admin":
        if not (1 <= len(args.username) <= 50) or not (1 <= len(args.display_name) <= 50):
            print("错误：用户名和显示名长度须为 1–50 个字符。", file=sys.stderr)
            return 1
        try:
            password = create_admin(args.username, args.display_name)
        except UserExistsError:
            print(f"错误：用户名 {args.username} 已存在。", file=sys.stderr)
            return 1
        print(f"已创建管理员 {args.username}。")
        print(f"初始密码（只显示这一次，首次登录须修改）：{password}")
        return 0

    if args.command == "set-model":
        import json

        if not args.base_url.startswith(("http://", "https://")) or not args.model.strip():
            print("错误：接口地址须以 http:// 或 https:// 开头，模型名称不能为空。", file=sys.stderr)
            return 1
        extra = None
        if args.extra_params is not None:
            try:
                extra = json.loads(args.extra_params)
            except json.JSONDecodeError:
                extra = None
            if not isinstance(extra, dict):
                print("错误：--extra-params 须为 JSON 对象。", file=sys.stderr)
                return 1
        api_key = None
        if args.api_key_stdin:
            # Windows PowerShell 管道会在开头加 BOM，去掉；密钥要放进 HTTP 头，只接受可见 ASCII 字符
            api_key = sys.stdin.readline().lstrip("﻿").strip()
            if not api_key:
                print("错误：没有从标准输入读到 API 密钥。", file=sys.stderr)
                return 1
            if not all("!" <= c <= "~" for c in api_key):
                print("错误：API 密钥只能包含可见的 ASCII 字符。", file=sys.stderr)
                return 1
        set_model(args.base_url.strip(), args.model.strip(), extra, api_key)
        tail = f"，API 密钥末 4 位 {api_key[-4:]}" if api_key and len(api_key) >= 8 else ""
        print(f"已设置模型：{args.base_url.strip()}，{args.model.strip()}{tail}。")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
