"""T-55：限定 IP 的离线 CA 与拒绝覆盖。只使用虚构地址。"""
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from cryptography.x509.verification import PolicyBuilder, Store, VerificationError

from app.cli import main


def test_t55_scoped_chain_and_reissue(tmp_path):
    assert main(["gen-ca", "--ip", "172.16.20.30", "--out", str(tmp_path)]) == 0
    root = x509.load_pem_x509_certificate((tmp_path / "root.crt").read_bytes())
    inter = x509.load_pem_x509_certificate((tmp_path / "intermediate.crt").read_bytes())
    key = serialization.load_pem_private_key((tmp_path / "intermediate.key").read_bytes(), None)
    assert root.extensions.get_extension_for_class(x509.BasicConstraints).value.path_length == 1
    assert inter.extensions.get_extension_for_class(x509.BasicConstraints).value.path_length == 0
    assert root.extensions.get_extension_for_class(x509.NameConstraints).critical
    assert inter.extensions.get_extension_for_class(x509.NameConstraints).critical
    assert isinstance(key.curve, ec.SECP256R1)
    assert (tmp_path / "root.sha256").read_text().strip() == ":".join(
        f"{v:02X}" for v in root.fingerprint(hashes.SHA256()))
    assert (tmp_path / "root.key").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "intermediate.key").stat().st_mode & 0o777 == 0o600

    def verify(name):
        now = datetime.now(timezone.utc)
        leaf_key = ec.generate_private_key(ec.SECP256R1())
        leaf = (x509.CertificateBuilder().subject_name(x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "虚构站点")]))
            .issuer_name(inter.subject).public_key(leaf_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName([name]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)
            .sign(key, hashes.SHA256()))
        return PolicyBuilder().store(Store([root])).build_server_verifier(name).verify(leaf, [inter])

    verify(x509.IPAddress(ip_address("172.16.20.30")))
    for name in [x509.IPAddress(ip_address("172.16.20.31")),
                 x509.IPAddress(ip_address("192.168.20.30")), x509.DNSName("example.com")]:
        with pytest.raises(VerificationError):
            verify(name)
    target = tmp_path / "reissued"
    assert main(["gen-ca", "--reissue-intermediate", "--ip", "172.16.20.31",
                 "--root-cert", str(tmp_path / "root.crt"), "--root-key", str(tmp_path / "root.key"),
                 "--out", str(target)]) == 0
    renewed = x509.load_pem_x509_certificate((target / "intermediate.crt").read_bytes())
    renewed.verify_directly_issued_by(root)
    assert not (target / "root.key").exists()
    assert renewed.not_valid_after_utc <= root.not_valid_after_utc


def test_t55_refuses_existing_targets_and_outside_network(tmp_path, capsys):
    for filename in ["root.crt", "root.key", "intermediate.crt", "intermediate.key", "root.sha256"]:
        target = tmp_path / filename.replace(".", "-")
        target.mkdir()
        (target / filename).write_bytes(b"keep")
        assert main(["gen-ca", "--ip", "172.16.20.30", "--out", str(target)]) == 1
        assert list(target.iterdir()) == [target / filename]
        assert (target / filename).read_bytes() == b"keep"
    target = tmp_path / "outside"
    assert main(["gen-ca", "--ip", "192.168.20.30", "--out", str(target)]) == 1
    assert not target.exists()
    assert "PRIVATE KEY" not in capsys.readouterr().out
