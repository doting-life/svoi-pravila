"""Create a throw-away CA and an api.telegram.org leaf certificate for prod-smoke.

Runs inside the API image (it ships ``cryptography`` and ``certifi``). Writes into the
directory given as the only argument:

    ca.crt, tls.crt, tls.key  - the stub's certificate chain and key
    cacert.pem                - certifi's bundle plus the throw-away CA

and prints the path of certifi's bundle inside the image on the last stdout line so the
smoke test can bind-mount ``cacert.pem`` over it. TLS verification stays enabled.
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

import certifi
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

_HOST = "api.telegram.org"


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def main() -> None:
    out = Path(sys.argv[1])
    now = datetime.datetime.now(datetime.UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(_name("svoi-prod-smoke-ca"))
        .issuer_name(_name("svoi-prod-smoke-ca"))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(_name(_HOST))
        .issuer_name(ca_cert.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(_HOST)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_pem = ca_cert.public_bytes(serialization.Encoding.PEM)
    (out / "ca.crt").write_bytes(ca_pem)
    (out / "tls.crt").write_bytes(leaf_cert.public_bytes(serialization.Encoding.PEM))
    (out / "tls.key").write_bytes(
        leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    bundle = Path(certifi.where())
    (out / "cacert.pem").write_bytes(bundle.read_bytes() + b"\n" + ca_pem)
    print(bundle)


if __name__ == "__main__":
    main()
