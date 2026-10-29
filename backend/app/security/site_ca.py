"""离线生成限定私网 IP 的站点 CA；根私钥由密钥保管人离线保存。"""
import os
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address, ip_network
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def _private_network(value):
    network = ip_network(value)
    # 明确限定 RFC1918，避免 is_private 将回环、保留地址也视为私网。
    if network.version != 4 or not any(network.subnet_of(ip_network(n)) for n in
            ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")):
        raise ValueError("--net 须为 IPv4 私有网段")
    return network


def _cert(subject, key, issuer, issuer_key, network, path_length, now, expires):
    return (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(expires)
            .add_extension(x509.BasicConstraints(ca=True, path_length=path_length), True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                key_encipherment=False, data_encipherment=False, key_agreement=False,
                key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False), True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), False)
            .add_extension(x509.NameConstraints(
                permitted_subtrees=[x509.IPAddress(network), x509.DNSName("invalid")],
                excluded_subtrees=None), True).sign(issuer_key, hashes.SHA256()))


def generate_ca(ip: str, out: str, *, net: str = "172.16.0.0/12",
                reissue_intermediate: bool = False, root_cert: str | None = None,
                root_key: str | None = None) -> str:
    """返回根证书指纹；换 IP 时沿用根的约束和剩余有效期，不延长根证书。"""
    address = ip_address(ip)
    now = datetime.now(timezone.utc)
    try:
        expires = now.replace(year=now.year + 10)
    except ValueError:  # 闰年 2 月 29 日到期落在非闰年时取 2 月 28 日。
        expires = now.replace(year=now.year + 10, day=28)
    if reissue_intermediate:
        if not root_cert or not root_key:
            raise ValueError("重新签发须同时给出 --root-cert 和 --root-key")
        root = x509.load_pem_x509_certificate(Path(root_cert).read_bytes())
        signing_key = serialization.load_pem_private_key(Path(root_key).read_bytes(), None)
        if not isinstance(signing_key, ec.EllipticCurvePrivateKey) or not isinstance(signing_key.curve, ec.SECP256R1):
            raise ValueError("根私钥须为 ECDSA P-256")
        if signing_key.public_key().public_bytes(serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo) != root.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo):
            raise ValueError("根证书与私钥不匹配")
        root.verify_directly_issued_by(root)
        if not root.not_valid_before_utc <= now < root.not_valid_after_utc:
            raise ValueError("根证书已过期或尚未生效")
        if root.extensions.get_extension_for_class(x509.BasicConstraints).value != x509.BasicConstraints(True, 1):
            raise ValueError("根证书的 CA 约束不匹配")
        constraints = root.extensions.get_extension_for_class(x509.NameConstraints).value
        networks = [n.value for n in constraints.permitted_subtrees if isinstance(n, x509.IPAddress)]
        if len(networks) != 1:
            raise ValueError("根证书须有唯一的私网约束")
        network = _private_network(str(networks[0]))
        expires = min(expires, root.not_valid_after_utc)
    else:
        if root_cert or root_key:
            raise ValueError("使用根证书文件时须指定 --reissue-intermediate")
        network = _private_network(net)
        signing_key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "商衡SI 本地根证书")])
        root = _cert(subject, signing_key, subject, signing_key, network, 1, now, expires)
    if address.version != 4 or address not in network:
        raise ValueError("--ip 须为允许网段内的 IPv4 私有地址")
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "商衡SI 本地中间证书")])
    intermediate = _cert(subject, key, root.subject, signing_key,
                         ip_network(f"{address}/32"), 0, now, expires)
    fingerprint = ":".join(f"{v:02X}" for v in root.fingerprint(hashes.SHA256()))
    pem = serialization.Encoding.PEM
    def key_bytes(k):
        return k.private_bytes(pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    files = {"root.crt": root.public_bytes(pem), "root.sha256": (fingerprint + "\n").encode(),
             "intermediate.crt": intermediate.public_bytes(pem), "intermediate.key": key_bytes(key)}
    if not reissue_intermediate:
        files["root.key"] = key_bytes(signing_key)
    target = Path(out)
    # 换发目录也不能含残留 root.key，避免运维人员误把根私钥带到服务器。
    for name in {*files, "root.key"}:
        if os.path.lexists(target / name):
            raise FileExistsError(name)
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    created = []
    try:
        for name, content in files.items():
            path = target / name
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600 if name.endswith(".key") else 0o644)
            created.append(path)
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
    except BaseException:
        for path in created:
            path.unlink()
        raise
    return fingerprint
