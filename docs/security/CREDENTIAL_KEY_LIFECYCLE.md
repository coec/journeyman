# Credential Encryption Key Lifecycle

Journeyman v2 supports envelope encryption for secrets stored at rest. A random
AES-256-GCM data key encrypts each payload. An RSA storage key wraps that data
key with RSA-OAEP/SHA-256. Existing Fernet payloads remain readable during an
in-place upgrade and can be migrated explicitly.

## Files

The default keyring is `/etc/journeyman/credential-keys`.

For an RSA storage key `<key-id>` Journeyman stores:

* `<key-id>.private.pem` - unencrypted PKCS#8 private key, mode 0600;
* `<key-id>.public.pem` - public key;
* `<key-id>.json` - non-secret lifecycle metadata; and
* `active` - the key ID used for newly encrypted secrets.

The keyring directory is mode 0700. Key IDs and metadata are not secrets. Private
keys must never be written to the database, logs, audit records, source control,
or command output.

Legacy `<key-id>.key` Fernet files and `/etc/journeyman/credential.key` remain
supported for decryption until migration and backup-retention requirements are
complete.

## Generate and inspect keys

Generate a new RSA storage keypair:

    flask credential-key generate --key-id storage-2026-09

RSA-3072 is the default. Newly generated keys start in `decrypt-only` state and
do not affect new writes until explicitly activated or used in a rotation.

Inspect key state with:

    flask credential-key status

## Activation

Activation selects the key used for newly encrypted secrets:

    flask credential-key activate --key-id storage-2026-09

The previously active RSA key becomes `decrypt-only`. Activation alone does not
rewrite existing ciphertext; v2 envelopes are self-describing and continue to
reference the key needed to decrypt them.

## Rotation

Rotate existing v2 envelopes and activate the target key with:

    flask credential-key rotate --key-id storage-2027-09 --generate

For v2 data, rotation unwraps only the per-payload AES data key with the old RSA
private key and wraps that same data key with the new RSA public key. The secret
AES-GCM ciphertext and nonce are not decrypted or regenerated during rotation.
The previous RSA storage key is retained as `decrypt-only`.

Legacy Fernet payloads are deliberately not changed by `rotate`. This prevents
key rotation from also becoming an implicit format migration.

## Legacy migration

After an RSA key has been activated, convert all remaining Fernet payloads with:

    flask credential-key migrate-legacy

This migration necessarily decrypts each legacy payload and encrypts it into a
new v2 envelope. The database changes are committed as one transaction.

Retain legacy Fernet keys until database backups, rollback copies, and other
retained data encrypted with those keys have exceeded their retention period.

## Cryptoperiod and emergency replacement

Review and rotate the credential storage key at least annually and after any
suspected disclosure, unauthorized key-file access, host compromise, or
cryptographic-policy change. Journeyman warns administrators beginning 30 days
before the annual rotation age is reached.

Storage private keys must be backed up using the organization's protected backup
mechanism. Loss of every copy of a required private key makes envelopes wrapped
by that key unrecoverable. Key backups must receive protection equivalent to the
live private-key files.

All generate, activate, rotate, and migration operations emit audit events.
