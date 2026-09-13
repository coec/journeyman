from datetime import datetime, timedelta, timezone
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app import db
from app.models import Runner
from app.services.runner_pki import (
    RunnerPkiError,
    ensure_controller_client_identity,
    generate_runner_ca,
    issue_runner_certificate,
    renew_runner_ca_certificate,
    runner_ca_status,
    sign_runner_csr,
)


def _configure_pki(app, tmp_path):
    root = tmp_path / "runner-pki"
    app.config.update(
        RUNNER_PKI_ROOT=root,
        RUNNER_CA_PRIVATE_KEY_PATH=str(root / "ca-key.pem"),
        RUNNER_CA_CERTIFICATE_PATH=str(root / "ca-cert.pem"),
        RUNNER_CA_METADATA_PATH=str(root / "ca-metadata.json"),
        RUNNER_CA_VALIDITY_DAYS=364,
        RUNNER_CA_RENEW_AFTER_DAYS=273,
        RUNNER_CERTIFICATE_VALIDITY_DAYS=30,
        RUNNER_CONTROLLER_PRIVATE_KEY_PATH=str(root / "controller-key.pem"),
        RUNNER_CONTROLLER_CERTIFICATE_PATH=str(root / "controller-cert.pem"),
    )
    return root


def _csr(common_name="runner.example.test", key_size=3072):
    key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("attacker.example.test")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    return key, csr.public_bytes(serialization.Encoding.PEM)


def test_runner_ca_initialization_and_status(app, tmp_path):
    root = _configure_pki(app, tmp_path)
    with app.app_context():
        metadata = generate_runner_ca()
        status = runner_ca_status()

    assert status["initialized"] is True
    assert status["renewal_due"] is False
    assert status["key_size"] == 4096
    assert metadata["fingerprint_sha256"] == status["fingerprint_sha256"]
    assert (root / "ca-key.pem").stat().st_mode & 0o777 == 0o600
    assert (root / "ca-cert.pem").stat().st_mode & 0o777 == 0o644


def test_runner_ca_refuses_overwrite(app, tmp_path):
    _configure_pki(app, tmp_path)
    with app.app_context():
        generate_runner_ca()
        with pytest.raises(RunnerPkiError, match="refusing to overwrite"):
            generate_runner_ca()


def test_ca_renewal_keeps_same_private_key(app, tmp_path):
    root = _configure_pki(app, tmp_path)
    created = datetime(2026, 1, 1, tzinfo=timezone.utc)
    renewed = created + timedelta(days=274)
    with app.app_context():
        generate_runner_ca(now=created)
        original_key = serialization.load_pem_private_key(
            (root / "ca-key.pem").read_bytes(), password=None
        )
        original_public = original_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        original_cert = x509.load_pem_x509_certificate((root / "ca-cert.pem").read_bytes())
        renew_runner_ca_certificate(now=renewed)
        replacement_key = serialization.load_pem_private_key(
            (root / "ca-key.pem").read_bytes(), password=None
        )
        replacement_public = replacement_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        replacement_cert = x509.load_pem_x509_certificate((root / "ca-cert.pem").read_bytes())

    assert original_public == replacement_public
    assert original_cert.serial_number != replacement_cert.serial_number


def test_runner_certificate_profile_ignores_csr_requested_names(app, tmp_path):
    _configure_pki(app, tmp_path)
    runner_key, csr_pem = _csr()
    with app.app_context():
        generate_runner_ca()
        result = sign_runner_csr(
            csr_pem,
            runner_uuid="12345678-1234-5678-1234-567812345678",
            hostname="runner01.example.test",
        )
    cert = x509.load_pem_x509_certificate(result["certificate_pem"].encode())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    constraints = cert.extensions.get_extension_for_class(x509.BasicConstraints).value

    assert "runner01.example.test" in san.get_values_for_type(x509.DNSName)
    assert "attacker.example.test" not in san.get_values_for_type(x509.DNSName)
    assert (
        "urn:journeyman:runner:12345678-1234-5678-1234-567812345678"
        in san.get_values_for_type(x509.UniformResourceIdentifier)
    )
    assert ExtendedKeyUsageOID.SERVER_AUTH in eku
    assert ExtendedKeyUsageOID.CLIENT_AUTH in eku
    assert constraints.ca is False
    assert cert.public_key().public_numbers() == runner_key.public_key().public_numbers()


def test_runner_certificate_is_signed_by_ca(app, tmp_path):
    _configure_pki(app, tmp_path)
    _, csr_pem = _csr()
    with app.app_context():
        generate_runner_ca()
        result = sign_runner_csr(
            csr_pem,
            runner_uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            hostname="10.0.0.8",
        )
    cert = x509.load_pem_x509_certificate(result["certificate_pem"].encode())
    ca_cert = x509.load_pem_x509_certificate(result["ca_certificate_pem"].encode())
    ca_cert.public_key().verify(
        cert.signature,
        cert.tbs_certificate_bytes,
        padding.PKCS1v15(),
        cert.signature_hash_algorithm,
    )
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "10.0.0.8" in [str(value) for value in san.get_values_for_type(x509.IPAddress)]


def test_issue_runner_certificate_records_expected_identity(app, tmp_path):
    _configure_pki(app, tmp_path)
    _, csr_pem = _csr()
    with app.app_context():
        generate_runner_ca()
        runner = Runner(
            name="runner01",
            hostname="runner01.example.test",
            runner_uuid="11111111-2222-3333-4444-555555555555",
            is_local=False,
        )
        db.session.add(runner)
        db.session.commit()
        result = issue_runner_certificate(runner, csr_pem)
        runner_id = runner.id

        db.session.expire_all()
        stored = db.session.get(Runner, runner_id)
        assert stored.pki_certificate_serial == result["serial"]
        assert stored.pki_certificate_fingerprint_sha256 == result["fingerprint_sha256"]
        assert stored.pki_certificate_not_after_at is not None
        assert stored.pki_certificate_issued_at is not None


def test_runner_csr_rejects_weak_rsa_key(app, tmp_path):
    _configure_pki(app, tmp_path)
    _, csr_pem = _csr(key_size=1024)
    with app.app_context():
        generate_runner_ca()
        with pytest.raises(RunnerPkiError, match="at least 2048 bits"):
            sign_runner_csr(
                csr_pem,
                runner_uuid="11111111-2222-3333-4444-555555555555",
                hostname="runner01.example.test",
            )


def test_pki_registration_consumes_token_and_records_certificate(app, client, tmp_path):
    from app.services.runners import issue_registration_token

    _configure_pki(app, tmp_path)
    _, csr_pem = _csr(common_name="ignored.example.test")
    with app.app_context():
        generate_runner_ca()
        runner = Runner(name="enrol-runner", site="perth", is_local=False)
        token = issue_registration_token(runner)
        db.session.add(runner)
        db.session.commit()
        runner_id = runner.id

    response = client.post(
        "/api/runners/register",
        json={
            "token": token,
            "hostname": "enrol01.example.test",
            "version": "0.17",
            "csr_pem": csr_pem.decode("ascii"),
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["runner_uuid"]
    assert "runner_secret" not in payload
    assert "BEGIN CERTIFICATE" in payload["certificate_pem"]
    assert "BEGIN CERTIFICATE" in payload["ca_certificate_pem"]
    assert payload["certificate_serial"]
    assert len(payload["certificate_fingerprint_sha256"]) == 64

    with app.app_context():
        stored = db.session.get(Runner, runner_id)
        assert stored.registration_token_digest == ""
        assert stored.api_secret_digest == ""
        assert stored.pki_certificate_serial == payload["certificate_serial"]
        assert (
            stored.pki_certificate_fingerprint_sha256
            == payload["certificate_fingerprint_sha256"]
        )
        assert stored.pki_certificate_not_after_at is not None


def test_pki_registration_failure_does_not_consume_token(app, client, tmp_path):
    from app.services.runners import issue_registration_token

    _configure_pki(app, tmp_path)
    _, csr_pem = _csr()
    with app.app_context():
        runner = Runner(name="retry-runner", is_local=False)
        token = issue_registration_token(runner)
        expected_digest = runner.registration_token_digest
        db.session.add(runner)
        db.session.commit()
        runner_id = runner.id

    # Deliberately do not initialize the runner CA.
    response = client.post(
        "/api/runners/register",
        json={
            "token": token,
            "hostname": "retry01.example.test",
            "version": "0.17",
            "csr_pem": csr_pem.decode("ascii"),
        },
    )
    assert response.status_code == 503

    with app.app_context():
        stored = db.session.get(Runner, runner_id)
        assert stored.registration_token_digest == expected_digest
        assert stored.runner_uuid is None
        assert stored.api_secret_digest == ""
        assert stored.pki_certificate_serial == ""


def test_controller_management_identity_is_client_only(app, tmp_path):
    root = _configure_pki(app, tmp_path)
    with app.app_context():
        generate_runner_ca()
        result = ensure_controller_client_identity()

    cert = x509.load_pem_x509_certificate((root / "controller-cert.pem").read_bytes())
    eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert result["created"] is True
    assert ExtendedKeyUsageOID.CLIENT_AUTH in eku
    assert ExtendedKeyUsageOID.SERVER_AUTH not in eku
    assert "urn:journeyman:controller" in san.get_values_for_type(
        x509.UniformResourceIdentifier
    )
    assert (root / "controller-key.pem").stat().st_mode & 0o777 == 0o600


def test_pki_registration_records_management_port(app, client, tmp_path):
    from app.services.runners import issue_registration_token

    _configure_pki(app, tmp_path)
    _, csr_pem = _csr()
    with app.app_context():
        generate_runner_ca()
        runner = Runner(name="port-runner", is_local=False)
        token = issue_registration_token(runner)
        db.session.add(runner)
        db.session.commit()
        runner_id = runner.id

    response = client.post(
        "/api/runners/register",
        json={
            "token": token,
            "hostname": "port-runner.example.test",
            "version": "0.17",
            "management_port": 9443,
            "csr_pem": csr_pem.decode("ascii"),
        },
    )
    assert response.status_code == 200
    with app.app_context():
        assert db.session.get(Runner, runner_id).management_port == 9443


def test_controller_certificate_renewal_reuses_private_key(app, tmp_path):
    from app.services.runner_pki import renew_controller_client_identity

    root = _configure_pki(app, tmp_path)
    created = datetime(2026, 1, 1, tzinfo=timezone.utc)
    renewed = created + timedelta(days=16)
    with app.app_context():
        generate_runner_ca(now=created)
        ensure_controller_client_identity(now=created)
        original_key = serialization.load_pem_private_key(
            (root / "controller-key.pem").read_bytes(), password=None
        )
        original_public = original_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        original_cert = x509.load_pem_x509_certificate(
            (root / "controller-cert.pem").read_bytes()
        )
        renew_controller_client_identity(now=renewed)
        replacement_key = serialization.load_pem_private_key(
            (root / "controller-key.pem").read_bytes(), password=None
        )
        replacement_public = replacement_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        replacement_cert = x509.load_pem_x509_certificate(
            (root / "controller-cert.pem").read_bytes()
        )

    assert original_public == replacement_public
    assert original_cert.serial_number != replacement_cert.serial_number
