# Credential Encryption Key Lifecycle

Journeyman supports versioned Fernet keys for encrypted live Credentials and
immutable Job credential snapshots.

## Files

The default keyring is `/etc/journeyman/credential-keys`. Each key is stored as
`<key-id>.key` with mode 0600 or stricter. The `active` file contains the key ID
used for new encryption. Existing installations with only
`/etc/journeyman/credential.key` remain compatible until the first rotation.

## Rotation

Use a stable, non-secret identifier such as `2026-08`:

    flask credential-key rotate --key-id 2026-08 --generate

Rotation creates the new key with mode 0600, makes it active, decrypts every
live Credential and Job credential snapshot with its recorded old key ID, and
re-encrypts it with the new active key in one database transaction. If
re-encryption fails, the transaction is rolled back and the previous active-key
selection is restored.

Do not delete an old key immediately after rotation. Retain old key material
until database backups, rollback copies, and any other retained data encrypted
with that key have exceeded their retention period or have themselves been
re-encrypted.

## Cryptoperiod and emergency replacement

Review and rotate the credential encryption key at least annually and after any
suspected disclosure, unauthorized key-file access, host compromise, or
cryptographic-policy change. Emergency rotation uses the same command and
should be followed by review of audit/system logs and replacement of any
underlying credentials whose plaintext may have been exposed.

Key files must be backed up using the organization's protected backup mechanism.
Loss of all copies of a key makes data encrypted under that key unrecoverable.
Key backups must receive protection equivalent to the live key files.

Key IDs are metadata, not secrets. Key contents must never be written to the
database, application logs, audit records, command output, or source control.
