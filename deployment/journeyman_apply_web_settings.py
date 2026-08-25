#!/usr/bin/python3

import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


TLS_ROOT = Path("/etc/journeyman/tls")

NGINX_CONFIG = Path(
    "/etc/nginx/conf.d/journeyman.conf"
)

GENERATED_FULLCHAIN = Path(
    "/etc/nginx/journeyman-fullchain.pem"
)

LOCK_PATH = Path(
    "/run/lock/journeyman-nginx-apply.lock"
)

OPENSSL = "/usr/bin/openssl"
NGINX = "/usr/sbin/nginx"
SYSTEMCTL = "/usr/bin/systemctl"
RESTORECON = "/usr/sbin/restorecon"

MAX_INPUT_BYTES = 16384
MAX_PEM_BYTES = 1024 * 1024

EXPECTED_FIELDS = {
    "public_fqdn",
    "https_port",
    "tls_certificate_path",
    "tls_private_key_path",
    "tls_chain_path",
    "redirect_http_to_https",
}

FQDN_LABEL_PATTERN = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)

SAFE_PATH_PATTERN = re.compile(
    r"^/[A-Za-z0-9._/-]+$"
)


class ApplyError(RuntimeError):
    pass


def run_command(
    arguments,
    *,
    input_bytes=None,
    timeout=15,
):
    try:
        return subprocess.run(
            arguments,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            env={
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                "LANG": "C.UTF-8",
            },
        )
    except subprocess.TimeoutExpired as exc:
        raise ApplyError(
            "Command timed out: {}".format(
                arguments[0]
            )
        ) from exc


def command_error(result):
    message = (
        result.stderr.decode(
            "utf-8",
            errors="replace",
        ).strip()
        or result.stdout.decode(
            "utf-8",
            errors="replace",
        ).strip()
    )

    return message[-2000:] or "Command failed."


def require_success(
    result,
    description,
):
    if result.returncode != 0:
        raise ApplyError(
            "{}: {}".format(
                description,
                command_error(result),
            )
        )

    return result


def validate_fqdn(value):
    if not isinstance(value, str):
        raise ApplyError(
            "Public FQDN must be text."
        )

    if value != value.strip().lower().rstrip("."):
        raise ApplyError(
            "Public FQDN must already be normalized."
        )

    if not value or len(value) > 253:
        raise ApplyError(
            "Public FQDN has an invalid length."
        )

    labels = value.split(".")

    if len(labels) < 2:
        raise ApplyError(
            "Public FQDN must contain at least one dot."
        )

    if not all(
        FQDN_LABEL_PATTERN.fullmatch(label)
        for label in labels
    ):
        raise ApplyError(
            "Public FQDN contains an invalid hostname label."
        )

    return value


def validate_port(value):
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= 65535
    ):
        raise ApplyError(
            "HTTPS port must be between 1 and 65535."
        )

    if value == 80:
        raise ApplyError(
            "HTTPS port cannot be port 80."
        )

    return value


def validate_boolean(value, label):
    if not isinstance(value, bool):
        raise ApplyError(
            "{} must be true or false.".format(
                label
            )
        )

    return value


def validate_pem_path(
    value,
    *,
    label,
    required,
    private_key=False,
):
    if value == "" and not required:
        return None

    if not isinstance(value, str) or not value:
        raise ApplyError(
            "{} is required.".format(label)
        )

    if not SAFE_PATH_PATTERN.fullmatch(value):
        raise ApplyError(
            "{} contains unsupported characters."
            .format(label)
        )

    supplied_path = Path(value)

    if supplied_path.is_symlink():
        raise ApplyError(
            "{} cannot be a symbolic link."
            .format(label)
        )

    try:
        resolved_root = TLS_ROOT.resolve(
            strict=True
        )

        resolved_path = supplied_path.resolve(
            strict=True
        )
    except FileNotFoundError as exc:
        raise ApplyError(
            "{} does not exist.".format(label)
        ) from exc

    if resolved_root not in resolved_path.parents:
        raise ApplyError(
            "{} must be beneath {}."
            .format(
                label,
                TLS_ROOT,
            )
        )

    file_stat = resolved_path.stat()

    if not stat.S_ISREG(file_stat.st_mode):
        raise ApplyError(
            "{} must be a regular file."
            .format(label)
        )

    if file_stat.st_uid != 0:
        raise ApplyError(
            "{} must be owned by root."
            .format(label)
        )

    if file_stat.st_size <= 0:
        raise ApplyError(
            "{} is empty.".format(label)
        )

    if file_stat.st_size > MAX_PEM_BYTES:
        raise ApplyError(
            "{} is unexpectedly large."
            .format(label)
        )

    permission_bits = stat.S_IMODE(
        file_stat.st_mode
    )

    if private_key:
        if permission_bits & 0o077:
            raise ApplyError(
                "Private-key permissions must not "
                "grant group or other access."
            )
    elif permission_bits & 0o022:
        raise ApplyError(
            "{} must not be group or world writable."
            .format(label)
        )

    return resolved_path


def validate_certificate(
    certificate_path,
    private_key_path,
    chain_path,
    fqdn,
):
    require_success(
        run_command(
            [
                OPENSSL,
                "x509",
                "-in",
                str(certificate_path),
                "-noout",
            ]
        ),
        "Certificate validation failed",
    )

    require_success(
        run_command(
            [
                OPENSSL,
                "x509",
                "-in",
                str(certificate_path),
                "-checkend",
                "0",
                "-noout",
            ]
        ),
        "Certificate is expired or not currently valid",
    )

    require_success(
        run_command(
            [
                OPENSSL,
                "x509",
                "-in",
                str(certificate_path),
                "-checkhost",
                fqdn,
                "-noout",
            ]
        ),
        "Certificate does not cover the public FQDN",
    )

    purpose_result = require_success(
        run_command(
            [
                OPENSSL,
                "x509",
                "-in",
                str(certificate_path),
                "-purpose",
                "-noout",
            ]
        ),
        "Certificate-purpose validation failed",
    )

    purpose_text = purpose_result.stdout.decode(
        "utf-8",
        errors="replace",
    )

    if "SSL server : Yes" not in purpose_text:
        raise ApplyError(
            "Certificate is not valid for TLS server use."
        )

    require_success(
        run_command(
            [
                OPENSSL,
                "pkey",
                "-in",
                str(private_key_path),
                "-passin",
                "pass:",
                "-noout",
            ]
        ),
        "Private-key validation failed",
    )

    certificate_public_key = require_success(
        run_command(
            [
                OPENSSL,
                "x509",
                "-in",
                str(certificate_path),
                "-pubkey",
                "-noout",
            ]
        ),
        "Could not read the certificate public key",
    ).stdout

    certificate_der = require_success(
        run_command(
            [
                OPENSSL,
                "pkey",
                "-pubin",
                "-outform",
                "DER",
            ],
            input_bytes=certificate_public_key,
        ),
        "Could not normalize the certificate public key",
    ).stdout

    private_key_der = require_success(
        run_command(
            [
                OPENSSL,
                "pkey",
                "-in",
                str(private_key_path),
                "-passin",
                "pass:",
                "-pubout",
                "-outform",
                "DER",
            ]
        ),
        "Could not derive the private-key public key",
    ).stdout

    if not hashlib.sha256(
        certificate_der
    ).digest() == hashlib.sha256(
        private_key_der
    ).digest():
        raise ApplyError(
            "Certificate and private key do not match."
        )

    if chain_path is not None:
        require_success(
            run_command(
                [
                    OPENSSL,
                    "crl2pkcs7",
                    "-nocrl",
                    "-certfile",
                    str(chain_path),
                    "-outform",
                    "DER",
                ]
            ),
            "Certificate-chain validation failed",
        )


def canonical_digest(payload):
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )

    return hashlib.sha256(
        serialized.encode("utf-8")
    ).hexdigest()


def atomic_write(path, content, mode):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    descriptor, temporary_name = (
        tempfile.mkstemp(
            prefix=".{}.".format(path.name),
            dir=str(path.parent),
        )
    )

    temporary_path = Path(
        temporary_name
    )

    try:
        os.fchmod(
            descriptor,
            mode,
        )

        with os.fdopen(
            descriptor,
            "wb",
            closefd=True,
        ) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())

        os.replace(
            temporary_path,
            path,
        )
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def restore_context(path):
    if not Path(RESTORECON).exists():
        return

    run_command(
        [
            RESTORECON,
            str(path),
        ]
    )


def build_fullchain(
    certificate_path,
    chain_path,
):
    if chain_path is None:
        return certificate_path

    certificate_data = (
        certificate_path.read_bytes().rstrip()
    )

    chain_data = (
        chain_path.read_bytes().strip()
    )

    atomic_write(
        GENERATED_FULLCHAIN,
        certificate_data
        + b"\n"
        + chain_data
        + b"\n",
        0o644,
    )

    restore_context(
        GENERATED_FULLCHAIN
    )

    return GENERATED_FULLCHAIN


def render_nginx_config(
    *,
    public_fqdn,
    https_port,
    certificate_path,
    private_key_path,
    redirect_http_to_https,
):
    public_port = (
        ""
        if https_port == 443
        else ":{}".format(https_port)
    )

    sections = [
        "# Managed by Journeyman. Do not edit manually.\n"
    ]

    if redirect_http_to_https:
        sections.append(
            """server {{
    listen 80;
    server_name {fqdn};

    # Machine/API clients must not silently follow an insecure first hop.
    location ^~ /api/ {{
        return 426;
    }}

    # Human-facing browser requests may be redirected to HTTPS.
    location / {{
        return 301 https://{fqdn}{port}$request_uri;
    }}
}}
""".format(
                fqdn=public_fqdn,
                port=public_port,
            )
        )

    sections.append(
        """server {{
    listen {https_port} ssl;
    server_name {fqdn};

    ssl_certificate {certificate};
    ssl_certificate_key {private_key};

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:JOURNEYMAN_SSL:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;

    server_tokens off;
    autoindex off;
    client_max_body_size 20m;

    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy same-origin always;
    add_header Cross-Origin-Opener-Policy same-origin always;
    add_header Cross-Origin-Resource-Policy same-origin always;

    location / {{
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_redirect off;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-Port $server_port;

        proxy_connect_timeout 10s;
        proxy_send_timeout 3600s;
        proxy_read_timeout 3600s;
    }}
}}
""".format(
            https_port=https_port,
            fqdn=public_fqdn,
            certificate=certificate_path,
            private_key=private_key_path,
        )
    )

    return "\n".join(sections).encode(
        "utf-8"
    )


def rollback_config(previous_content):
    if previous_content is None:
        try:
            NGINX_CONFIG.unlink()
        except FileNotFoundError:
            pass
    else:
        atomic_write(
            NGINX_CONFIG,
            previous_content,
            0o644,
        )

    restore_context(
        NGINX_CONFIG
    )


def apply_config(config_content):
    previous_content = None

    if NGINX_CONFIG.exists():
        previous_content = (
            NGINX_CONFIG.read_bytes()
        )

    atomic_write(
        NGINX_CONFIG,
        config_content,
        0o644,
    )

    restore_context(
        NGINX_CONFIG
    )

    test_result = run_command(
        [
            NGINX,
            "-t",
        ]
    )

    if test_result.returncode != 0:
        rollback_config(
            previous_content
        )

        run_command(
            [
                NGINX,
                "-t",
            ]
        )

        raise ApplyError(
            "Generated Nginx configuration failed validation: {}"
            .format(
                command_error(test_result)
            )
        )

    reload_result = run_command(
        [
            SYSTEMCTL,
            "reload",
            "nginx",
        ],
        timeout=20,
    )

    if reload_result.returncode != 0:
        rollback_config(
            previous_content
        )

        run_command(
            [
                NGINX,
                "-t",
            ]
        )

        run_command(
            [
                SYSTEMCTL,
                "reload",
                "nginx",
            ],
            timeout=20,
        )

        raise ApplyError(
            "Nginx reload failed and the previous "
            "configuration was restored: {}"
            .format(
                command_error(reload_result)
            )
        )

    active_result = run_command(
        [
            SYSTEMCTL,
            "is-active",
            "--quiet",
            "nginx",
        ]
    )

    require_success(
        active_result,
        "Nginx is not active after reload",
    )


def read_payload():
    raw_data = sys.stdin.buffer.read(
        MAX_INPUT_BYTES + 1
    )

    if len(raw_data) > MAX_INPUT_BYTES:
        raise ApplyError(
            "Settings request is too large."
        )

    try:
        payload = json.loads(
            raw_data.decode("utf-8")
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise ApplyError(
            "Settings request is not valid JSON."
        ) from exc

    if not isinstance(payload, dict):
        raise ApplyError(
            "Settings request must be an object."
        )

    if set(payload) != EXPECTED_FIELDS:
        raise ApplyError(
            "Settings request contains unexpected fields."
        )

    return payload


def main():
    if len(sys.argv) != 1:
        raise ApplyError(
            "This helper does not accept command-line arguments."
        )

    os.umask(0o077)

    payload = read_payload()

    public_fqdn = validate_fqdn(
        payload["public_fqdn"]
    )

    https_port = validate_port(
        payload["https_port"]
    )

    redirect_http_to_https = validate_boolean(
        payload["redirect_http_to_https"],
        "HTTP redirect setting",
    )

    certificate_path = validate_pem_path(
        payload["tls_certificate_path"],
        label="Certificate path",
        required=True,
    )

    private_key_path = validate_pem_path(
        payload["tls_private_key_path"],
        label="Private-key path",
        required=True,
        private_key=True,
    )

    chain_path = validate_pem_path(
        payload["tls_chain_path"],
        label="Certificate-chain path",
        required=False,
    )

    validate_certificate(
        certificate_path,
        private_key_path,
        chain_path,
        public_fqdn,
    )

    nginx_certificate_path = build_fullchain(
        certificate_path,
        chain_path,
    )

    config_content = render_nginx_config(
        public_fqdn=public_fqdn,
        https_port=https_port,
        certificate_path=nginx_certificate_path,
        private_key_path=private_key_path,
        redirect_http_to_https=(
            redirect_http_to_https
        ),
    )

    apply_config(
        config_content
    )

    public_port = (
        ""
        if https_port == 443
        else ":{}".format(https_port)
    )

    response = {
        "ok": True,
        "message": (
            "Nginx configuration was validated "
            "and reloaded successfully."
        ),
        "configuration_sha256": (
            canonical_digest(payload)
        ),
        "public_url": (
            "https://{}{}".format(
                public_fqdn,
                public_port,
            )
        ),
    }

    print(
        json.dumps(
            response,
            separators=(",", ":"),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    try:
        LOCK_PATH.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with LOCK_PATH.open(
            "w",
            encoding="utf-8",
        ) as lock_file:
            try:
                fcntl.flock(
                    lock_file.fileno(),
                    fcntl.LOCK_EX
                    | fcntl.LOCK_NB,
                )
            except BlockingIOError as exc:
                raise ApplyError(
                    "Another Nginx configuration "
                    "application is already running."
                ) from exc

            main()
    except ApplyError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "message": str(exc),
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )

        sys.exit(1)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "message": (
                        "Unexpected helper failure: {}"
                        .format(exc)
                    ),
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )

        sys.exit(1)
