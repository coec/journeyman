#!/usr/bin/env python3
"""Migrate legacy Satellite/Zabbix credentials to the v1.1 URL/API model.

Run without --apply for a dry-run. The script must be executed from the
Journeyman installation with its normal JOURNEYMAN_CONFIG/environment loaded.
"""

import argparse

from app import create_app, db
from app.credential_types import CREDENTIAL_TYPE_SATELLITE, CREDENTIAL_TYPE_URL, CREDENTIAL_TYPE_ZABBIX
from app.models import Credential, Inventory


def _unique_name(owner, base):
    candidate = base
    suffix = 2
    while Credential.query.filter_by(owner=owner, name=candidate).first() is not None:
        candidate = "{} ({})".format(base, suffix)
        suffix += 1
    return candidate


def migrate(apply=False):
    changed = 0
    with create_app().app_context():
        legacy = Credential.query.filter(Credential.credential_type.in_([CREDENTIAL_TYPE_SATELLITE, CREDENTIAL_TYPE_ZABBIX])).order_by(Credential.id).all()
        for credential in legacy:
            data = credential.get_credential_data() if credential.encrypted_data else {}
            inventories = Inventory.query.filter_by(credential_id=credential.id).order_by(Inventory.id).all()
            if credential.credential_type == CREDENTIAL_TYPE_SATELLITE:
                target = {
                    "url": data.get("host") or "",
                    "auth_mode": "basic",
                    "password": data.get("password") or "",
                    "token_prefix": "",
                    "token_url": "",
                    "scope": "",
                }
                print('Satellite credential {!r}: convert in place to URL / API.'.format(credential.name))
                if apply:
                    credential.credential_type = CREDENTIAL_TYPE_URL
                    credential.set_credential_data(target)
                    changed += 1
                continue

            endpoints = sorted({str(row.endpoint or "").strip().rstrip("/") for row in inventories if str(row.endpoint or "").strip()})
            token = data.get("token") or ""
            if len(endpoints) <= 1:
                endpoint = endpoints[0] if endpoints else ""
                if not endpoint:
                    print('Zabbix credential {!r}: SKIP (no linked inventory endpoint).'.format(credential.name))
                    continue
                print('Zabbix credential {!r}: convert in place using {}.'.format(credential.name, endpoint))
                if apply:
                    credential.credential_type = CREDENTIAL_TYPE_URL
                    credential.set_credential_data({
                        "url": endpoint,
                        "auth_mode": "bearer",
                        "token": token,
                        "token_prefix": "Bearer",
                        "token_url": "",
                        "scope": "",
                    })
                    for row in inventories:
                        row.endpoint = ""
                    changed += 1
                continue

            print('Zabbix credential {!r}: {} endpoints; split into endpoint-specific URL credentials.'.format(credential.name, len(endpoints)))
            if apply:
                by_endpoint = {}
                for endpoint in endpoints:
                    new = Credential(
                        name=_unique_name(credential.owner, "{} - {}".format(credential.name, endpoint.split("//", 1)[-1].split("/", 1)[0])),
                        description=credential.description,
                        owner=credential.owner,
                        security_scope=credential.security_scope,
                        credential_type=CREDENTIAL_TYPE_URL,
                        username="",
                    )
                    new.set_credential_data({
                        "url": endpoint,
                        "auth_mode": "bearer",
                        "token": token,
                        "token_prefix": "Bearer",
                        "token_url": "",
                        "scope": "",
                    })
                    db.session.add(new)
                    db.session.flush()
                    by_endpoint[endpoint] = new
                for row in inventories:
                    endpoint = str(row.endpoint or "").strip().rstrip("/")
                    if endpoint in by_endpoint:
                        row.credential_id = by_endpoint[endpoint].id
                        row.endpoint = ""
                changed += len(by_endpoint)
        if apply:
            db.session.commit()
            print("Migration committed; {} credential record(s) created/converted.".format(changed))
        else:
            db.session.rollback()
            print("Dry-run only. Re-run with --apply to commit changes.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="commit the migration")
    args = parser.parse_args()
    migrate(apply=args.apply)


if __name__ == "__main__":
    main()
