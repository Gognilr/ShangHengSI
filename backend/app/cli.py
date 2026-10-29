"""运维命令：python -m app.cli <命令>。

  gen-key --out PATH            生成主密钥文件（32 字节随机值，权限 600）。目标文件已存在时拒绝覆盖。
  create-admin --username NAME  创建管理员，随机初始密码只显示一次，首次登录强制修改（SPEC 20.5）。
  set-model --base-url URL --model NAME [--extra-params JSON] [--api-key-stdin]
                                开发用：写入模型设置；API 密钥只从标准输入读取，不出现在命令行和输出中。
  decrypt-backup --in X.bak --out X.tar [--password-file PATH]
                                解密备份文件（restore.sh 使用，SPEC 16.3）；不给密码文件时读 BACKUP_PASSWORD_FILE。
  restore-prepare --tar X.tar --work DIR
                                恢复第 1 步：校验解密后的 tar，解出 db.dump 与原件，输出一行 JSON 摘要（设计 M8.md 3.3）。
  restore-db --dump X | --drop-old
                                恢复到新库再改名（3.4）；--drop-old 删除恢复前的旧库。
  restore-files [--verify --expect-files N | --drop-old | --drop-new]
                                交换原件（3.5）；--verify 核对；--drop-old 删除旧原件；--drop-new 删除未交换的待恢复原件。
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


def decrypt_backup(src: str, out: str, password_file: str | None) -> None:
    """解密备份到新文件（O_EXCL，权限 600）。失败时删除不完整的输出并重新抛出。"""
    from app.backup.archive import decrypt_stream, load_backup_password
    from app.config import get_settings

    password = load_backup_password(password_file or get_settings().backup_password_file)
    with open(src, "rb") as s:
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as f:
                decrypt_stream(s, f, password)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            os.unlink(out)
            raise


def _restore_command(args) -> int:
    """恢复的三个子命令。错误只输出中文原因或异常类名，不输出连接串、密码和文件内容。"""
    import json

    from app.backup import restore

    try:
        if args.command == "restore-prepare":
            summary = restore.prepare(args.tar, args.work)
            print(json.dumps(summary, ensure_ascii=False))
        elif args.command == "restore-db":
            if args.drop_old:
                print(f"已删除旧数据库 {restore.drop_old()}。")
            else:
                r = restore.restore_db(args.dump)
                print(f"数据库已恢复（迁移版本 {r['revision']}）；原数据库已改名为 {r['old_database']}。")
        elif args.verify:
            if args.expect_files is None or args.expect_files < 0:
                print("错误：--verify 须同时给出 --expect-files（不小于 0 的整数）。", file=sys.stderr)
                return 2
            r = restore.verify(args.expect_files)
            if not r["ok"]:
                print(f"错误：核对不通过：原件 {r['files']} 个（应为 {r['expected']} 个），"
                      f"数据库记录中缺少文件的原件 {r['missing']} 个。", file=sys.stderr)
                return 1
            print(f"核对通过：原件 {r['files']} 个，数据库记录 {r['records']} 条。")
        elif args.drop_old:
            print("已删除旧原件目录。" if restore.drop_old_files() else "旧原件目录不存在，无需删除。")
        elif args.drop_new:
            print("已删除待恢复原件目录。" if restore.drop_new_files() else "待恢复原件目录不存在，无需删除。")
        else:
            r = restore.swap_files()
            print(f"原件已替换：移出 {r['moved_out']} 个，恢复 {r['moved_in']} 个。")
    except restore.RestoreError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001  异常消息可能带连接参数或路径，只输出类名
        print(f"错误：恢复步骤失败（{type(exc).__name__}）", file=sys.stderr)
        return 1
    return 0


def backup_now(*, wait: bool, timeout: int) -> int:
    """入队后只观察本次之后的新记录；异常不输出连接参数（Q-164）。"""
    import time

    from sqlalchemy import func, select

    from app.backup.job import enqueue_backup
    from app.db.models import BackupRecord
    from app.db.session import get_sessionmaker

    try:
        sm = get_sessionmaker()
        with sm() as db:
            job = enqueue_backup(db, trigger="manual", user_id=None)
            if job is None:
                print("已有备份正在进行")
                return 2
            # enqueue_backup 持有事务锁，此时尚未提交，worker 还不能领取本次任务。
            last_id = db.scalar(select(func.max(BackupRecord.id))) or 0
            db.commit()
        if not wait:
            print("已加入手动备份队列。")
            return 0
        deadline = time.monotonic() + timeout
        while True:
            # 每次使用新会话，避免 ORM 缓存 running 状态。
            with sm() as db:
                record = db.scalar(select(BackupRecord).where(BackupRecord.id > last_id)
                                   .order_by(BackupRecord.id.desc()).limit(1))
                if record is not None and record.status == "success":
                    print(f"备份成功：{record.file_name}，{record.size_bytes} 字节。")
                    return 0
                if record is not None and record.status == "failed":
                    print(f"备份失败：{record.error_message or '原因未记录'}", file=sys.stderr)
                    return 1
            if time.monotonic() >= deadline:
                print("等待备份超时；任务仍可能在排队或执行，请检查 worker 状态与备份记录。", file=sys.stderr)
                return 3
            time.sleep(3)
    except Exception as exc:  # noqa: BLE001  异常消息可能带密码，只输出类名
        print(f"错误：手动备份命令失败（{type(exc).__name__}），请检查 worker 与数据库状态。", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="商衡SI 运维命令")
    sub = parser.add_subparsers(dest="command", required=True)

    p_key = sub.add_parser("gen-key", help="生成主密钥文件（32 字节，权限 600，不覆盖已有文件）")
    p_key.add_argument("--out", required=True, help="输出文件路径")

    p_ca = sub.add_parser("gen-ca", help="离线生成限定私网 IP 的 HTTPS 根证书与中间证书")
    p_ca.add_argument("--ip", required=True, help="服务器 IPv4 私有地址")
    p_ca.add_argument("--net", default="172.16.0.0/12", help="根证书允许的私有网段")
    p_ca.add_argument("--out", required=True, help="输出目录，不覆盖已有目标文件")
    p_ca.add_argument("--reissue-intermediate", action="store_true", help="使用离线根重新签发中间证书")
    p_ca.add_argument("--root-cert", help="离线根证书文件")
    p_ca.add_argument("--root-key", help="离线根私钥文件")

    p_admin = sub.add_parser("create-admin", help="创建管理员（随机初始密码只显示一次）")
    p_admin.add_argument("--username", required=True, help="用户名，最多 50 个字符")
    p_admin.add_argument("--display-name", default="管理员", help="显示名，默认“管理员”")

    p_model = sub.add_parser("set-model", help="开发用：设置模型接口地址、模型名称和 API 密钥")
    p_model.add_argument("--base-url", required=True, help="接口地址，如 https://api.example.com/v1")
    p_model.add_argument("--model", required=True, help="模型名称")
    p_model.add_argument("--extra-params", help="附加请求参数，JSON 对象，原样合并进请求体")
    p_model.add_argument("--api-key-stdin", action="store_true", help="从标准输入读取 API 密钥（一行）")

    p_dec = sub.add_parser("decrypt-backup", help="解密备份文件为 tar（恢复时使用）")
    p_dec.add_argument("--in", dest="src", required=True, help="备份文件 aireview_yyyyMMdd_HHmm.bak")
    p_dec.add_argument("--out", required=True, help="输出的 tar 文件路径（不能已存在）")
    p_dec.add_argument("--password-file", help="备份密码文件；默认取环境变量 BACKUP_PASSWORD_FILE")

    p_rprep = sub.add_parser("restore-prepare", help="恢复：校验解密后的备份并解出数据库导出与原件")
    p_rprep.add_argument("--tar", required=True, help="decrypt-backup 输出的 tar 文件")
    p_rprep.add_argument("--work", required=True, help="工作目录（db.dump 解到这里）")

    p_rdb = sub.add_parser("restore-db", help="恢复：把数据库导出恢复为当前数据库（恢复到新库再改名）")
    g_rdb = p_rdb.add_mutually_exclusive_group(required=True)
    g_rdb.add_argument("--dump", help="restore-prepare 解出的 db.dump")
    g_rdb.add_argument("--drop-old", action="store_true", help="删除恢复前的旧库（核对通过后）")

    p_rfiles = sub.add_parser("restore-files", help="恢复：交换原件；或核对、清理")
    g_rfiles = p_rfiles.add_mutually_exclusive_group()
    g_rfiles.add_argument("--verify", action="store_true", help="核对原件数与数据库记录")
    g_rfiles.add_argument("--drop-old", action="store_true", help="删除恢复前的旧原件目录（核对通过后）")
    g_rfiles.add_argument("--drop-new", action="store_true", help="删除尚未交换的待恢复原件（恢复中止时）")
    p_rfiles.add_argument("--expect-files", type=int, help="--verify 时期望的原件数")

    p_backup = sub.add_parser("backup-now", help="加入手动备份队列，可等待备份完成")
    p_backup.add_argument("--wait", action="store_true", help="等待备份结果")
    p_backup.add_argument("--timeout", type=int, default=3600, help="等待超时秒数，默认 3600")

    p_rotate = sub.add_parser("rotate-key", help="停服轮换主密钥加密的数据，可用同一新密钥续做")
    p_rotate.add_argument("--old-key-file", required=True, help="旧主密钥文件")
    p_rotate.add_argument("--new-key-file", required=True, help="新主密钥文件")
    p_verify = sub.add_parser("verify-key", help="抽查加密列与原件能否用给定主密钥解密")
    p_verify.add_argument("--key-file", required=True, help="待验证主密钥文件")

    args = parser.parse_args(argv)

    if args.command in ("rotate-key", "verify-key"):
        from app.security.rotate import command
        return command(args)

    if args.command == "backup-now":
        if args.timeout <= 0:
            parser.error("--timeout 必须大于 0")
        return backup_now(wait=args.wait, timeout=args.timeout)

    if args.command in ("restore-prepare", "restore-db", "restore-files"):
        return _restore_command(args)

    if args.command == "decrypt-backup":
        from app.backup.archive import BackupFormatError

        if not os.path.isfile(args.src):
            print(f"错误：备份文件 {args.src} 不存在。", file=sys.stderr)
            return 1
        try:
            decrypt_backup(args.src, args.out, args.password_file)
        except FileExistsError:
            print(f"错误：{args.out} 已存在，拒绝覆盖。", file=sys.stderr)
            return 1
        except (BackupFormatError, RuntimeError) as exc:
            print(f"错误：{exc}", file=sys.stderr)
            return 1
        print(f"已解密到 {args.out}。")
        return 0

    if args.command == "gen-ca":
        from app.security.site_ca import generate_ca

        try:
            fingerprint = generate_ca(args.ip, args.out, net=args.net,
                reissue_intermediate=args.reissue_intermediate,
                root_cert=args.root_cert, root_key=args.root_key)
        except FileExistsError:
            print("错误：目标文件已存在，拒绝覆盖；请使用新目录。", file=sys.stderr)
            return 1
        except Exception as exc:  # 不输出文件内容或私钥解析错误详情。
            print(f"错误：证书生成失败（{type(exc).__name__}），请检查私网地址、根证书与输出目录。", file=sys.stderr)
            return 1
        print("已生成 root.crt、root.sha256、intermediate.crt、intermediate.key" +
              ("。" if args.reissue_intermediate else "、root.key。"))
        print(f"根证书 SHA-256 指纹：{fingerprint}")
        print("请密钥保管人取走 root.key 离线保存，并从服务器删除；服务器仅保留根证书、中间证书与中间私钥。")
        return 0

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
