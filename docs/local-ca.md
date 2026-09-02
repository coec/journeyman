# Creating a Local CA for Journeyman

Journeyman requires a TLS private key and certificate chain for HTTPS.

In a production deployment, use your organisation's existing PKI or another properly
managed certificate authority.

If no CA is available, the procedure below can be used to create a small local CA and
a Journeyman server certificate for evaluation, lab, or bootstrap purposes.

> **Note**
>
> Journeyman consumes TLS certificates but does not manage PKI. Certificate authority
> lifecycle management, certificate renewal, revocation, and trust distribution remain
> the responsibility of the administrator.

The examples below assume the Journeyman server is:

```text
journeyman.example.com
```

Replace this with the actual FQDN of your Journeyman server.

## 1. Create a local certificate authority

Create a directory for the CA material:

```bash
mkdir -p /root/journeyman-ca
cd /root/journeyman-ca
```

Generate the CA private key:

```bash
openssl genrsa -out journeyman-ca.key 4096
```

Create the CA certificate:

```bash
openssl req -x509 \
  -new \
  -sha256 \
  -days 3650 \
  -key journeyman-ca.key \
  -out journeyman-ca.crt \
  -subj "/CN=Journeyman Local CA"
```

This creates:

```text
journeyman-ca.key
journeyman-ca.crt
```

`journeyman-ca.key` is the CA private key and must be kept private.

`journeyman-ca.crt` is the CA certificate. Systems connecting to Journeyman must trust
this certificate.

## 2. Create the Journeyman server private key

Generate a private key for the Journeyman HTTPS service:

```bash
openssl genrsa -out journeyman-key.pem 4096
```

## 3. Create the certificate signing request

Create a certificate signing request for the Journeyman FQDN:

```bash
openssl req \
  -new \
  -key journeyman-key.pem \
  -out journeyman.csr \
  -subj "/CN=journeyman.example.com"
```

Modern TLS clients validate the certificate Subject Alternative Name (SAN), not only
the Common Name.

Create the required certificate extensions:

```bash
cat > journeyman.ext <<'EOF_EXT'
subjectAltName = DNS:journeyman.example.com
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
basicConstraints = critical, CA:FALSE
EOF_EXT
```

Replace `journeyman.example.com` with the actual Journeyman FQDN.

## 4. Sign the Journeyman certificate

Use the local CA to sign the server certificate:

```bash
openssl x509 \
  -req \
  -sha256 \
  -days 825 \
  -in journeyman.csr \
  -CA journeyman-ca.crt \
  -CAkey journeyman-ca.key \
  -CAcreateserial \
  -out journeyman.crt \
  -extfile journeyman.ext
```

This creates:

```text
journeyman.crt
```

## 5. Create the certificate chain

For this simple one-level CA, create the full-chain file by combining the server
certificate and CA certificate:

```bash
cat journeyman.crt journeyman-ca.crt > journeyman-fullchain.pem
```

The Journeyman installer can then use:

```yaml
journeyman_tls_fullchain_src: /root/journeyman-ca/journeyman-fullchain.pem
journeyman_tls_key_src: /root/journeyman-ca/journeyman-key.pem
```

## 6. Verify the certificate

Verify that the server certificate chains correctly to the local CA:

```bash
openssl verify \
  -CAfile journeyman-ca.crt \
  journeyman.crt
```

Expected output:

```text
journeyman.crt: OK
```

Inspect the certificate subject, issuer, validity dates, and SAN:

```bash
openssl x509 \
  -in journeyman.crt \
  -noout \
  -subject \
  -issuer \
  -dates \
  -ext subjectAltName
```

Confirm that the SAN contains the Journeyman server FQDN.

## 7. Trust the local CA

Any system connecting to Journeyman must trust `journeyman-ca.crt`.

On RHEL-family systems:

```bash
cp journeyman-ca.crt /etc/pki/ca-trust/source/anchors/
update-ca-trust
```

Remote Journeyman runners must also trust the CA before they can securely connect to
the Journeyman server.

Browser trust must be configured according to the operating system, browser, or
organisation's normal certificate deployment mechanism.

## Security considerations

The local CA private key:

```text
journeyman-ca.key
```

can issue certificates trusted by any system on which `journeyman-ca.crt` has been
installed.

Protect the CA private key accordingly. Do not copy it to Journeyman runners or other
systems that only need to trust the CA.

For production deployments, prefer an established organisational PKI or another
properly managed certificate authority.

