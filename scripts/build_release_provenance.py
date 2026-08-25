#!/usr/bin/env python3
"""Generate and verify Journeyman release provenance.

The release provenance consists of:
* a SHA-256 source-file manifest tied to VERSION;
* a dependency-lock digest and exact dependency inventory;
* an SPDX 2.3 JSON SBOM for the locked Python dependency closure.

The generated manifest intentionally excludes runtime state, virtual
environments, source-control metadata, build output, logs, secrets, and
previously generated provenance artifacts.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]

EXCLUDED_DIRECTORY_NAMES = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    "dist",
    "instance",
    "venv",
    "venv314",
}

EXCLUDED_FILE_SUFFIXES = {
    ".key",
    ".log",
    ".pyc",
    ".pyo",
}

EXCLUDED_FILE_NAMES = {
    "journeyman.env",
}

GENERATED_PROVENANCE_SUFFIXES = (
    ".manifest.json",
    ".sbom.spdx.json",
    ".MANIFEST.sha256",
    ".asc",
)

LOCK_LINE_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[A-Za-z0-9_.+!-]+)$"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def release_version(root: Path) -> str:
    value = (root / "VERSION").read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError("VERSION is empty.")
    return value


def should_include(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if any(part in EXCLUDED_DIRECTORY_NAMES for part in relative.parts[:-1]):
        return False
    if path.name in EXCLUDED_FILE_NAMES:
        return False
    if path.suffix.lower() in EXCLUDED_FILE_SUFFIXES:
        return False
    if any(path.name.endswith(suffix) for suffix in GENERATED_PROVENANCE_SUFFIXES):
        return False
    if path.name.endswith(".tar.gz"):
        return False
    return True


def source_files(root: Path):
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            # Release provenance must never silently follow external content.
            continue
        if not path.is_file():
            continue
        if should_include(path, root):
            yield path


def parse_lock(path: Path, *, seen=None):
    """Read a pip lock file, following local -r includes."""

    seen = set() if seen is None else seen
    path = path.resolve()
    if path in seen:
        return {}
    seen.add(path)

    packages = {}
    for number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith(("-r ", "--requirement ")):
            referenced = line.split(None, 1)[1].strip()
            packages.update(parse_lock(path.parent / referenced, seen=seen))
            continue

        match = LOCK_LINE_RE.fullmatch(line)
        if not match:
            raise RuntimeError(
                "{}:{} is not an exact name==version lock entry: {!r}".format(
                    path,
                    number,
                    line,
                )
            )

        name = match.group("name")
        packages[name.casefold().replace("_", "-")] = {
            "name": name,
            "version": match.group("version"),
        }

    return packages


def dependency_inventory(root: Path, lock_name: str):
    lock_path = root / lock_name
    if not lock_path.exists():
        raise RuntimeError("Dependency lock does not exist: {}".format(lock_path))
    packages = parse_lock(lock_path)
    return lock_path, sorted(
        packages.values(),
        key=lambda row: row["name"].casefold(),
    )


def build_manifest(root: Path, *, lock_name: str):
    version = release_version(root)
    lock_path, dependencies = dependency_inventory(root, lock_name)

    files = []
    for path in source_files(root):
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
        )

    return {
        "schema": "journeyman-release-manifest/v1",
        "journeyman_version": version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hash_algorithm": "SHA-256",
        "dependency_lock": {
            "path": lock_path.relative_to(root).as_posix(),
            "sha256": sha256_file(lock_path),
            "dependencies": dependencies,
        },
        "files": files,
    }


def spdx_package(name: str, version: str):
    safe_name = re.sub(r"[^A-Za-z0-9.-]+", "-", name)
    purl_name = quote(name.lower().replace("_", "-"), safe="")
    return {
        "SPDXID": "SPDXRef-Package-{}".format(safe_name),
        "name": name,
        "versionInfo": version,
        "downloadLocation": "NOASSERTION",
        "filesAnalyzed": False,
        "licenseConcluded": "NOASSERTION",
        "licenseDeclared": "NOASSERTION",
        "copyrightText": "NOASSERTION",
        "externalRefs": [
            {
                "referenceCategory": "PACKAGE-MANAGER",
                "referenceType": "purl",
                "referenceLocator": "pkg:pypi/{}@{}".format(
                    purl_name,
                    quote(version, safe=""),
                ),
            }
        ],
    }


def build_spdx(manifest):
    version = manifest["journeyman_version"]
    packages = [
        {
            "SPDXID": "SPDXRef-Package-Journeyman",
            "name": "Journeyman",
            "versionInfo": version,
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": "Apache-2.0",
            "licenseDeclared": "Apache-2.0",
            "copyrightText": "NOASSERTION",
        }
    ]

    relationships = []
    for dependency in manifest["dependency_lock"]["dependencies"]:
        package = spdx_package(
            dependency["name"],
            dependency["version"],
        )
        packages.append(package)
        relationships.append(
            {
                "spdxElementId": "SPDXRef-Package-Journeyman",
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": package["SPDXID"],
            }
        )

    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "Journeyman-{}-SBOM".format(version),
        "documentNamespace": (
            "https://journeyman.invalid/spdx/{}/{}".format(
                quote(version, safe=""),
                manifest["dependency_lock"]["sha256"],
            )
        ),
        "creationInfo": {
            "created": manifest["generated_at"],
            "creators": [
                "Tool: Journeyman build_release_provenance.py",
            ],
        },
        "documentDescribes": [
            "SPDXRef-Package-Journeyman",
        ],
        "packages": packages,
        "relationships": relationships,
    }


def write_sha256_manifest(manifest, path: Path):
    lines = [
        "{}  {}".format(row["sha256"], row["path"])
        for row in manifest["files"]
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def generate(root: Path, output: Path, *, lock_name: str):
    output.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(root, lock_name=lock_name)
    version = manifest["journeyman_version"]

    manifest_path = output / "journeyman-{}.manifest.json".format(version)
    sbom_path = output / "journeyman-{}.sbom.spdx.json".format(version)
    hashes_path = output / "journeyman-{}.MANIFEST.sha256".format(version)

    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    sbom_path.write_text(
        json.dumps(build_spdx(manifest), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_sha256_manifest(manifest, hashes_path)

    print("Journeyman release provenance generated:")
    print("  Version:  {}".format(version))
    print("  Files:    {}".format(len(manifest["files"])))
    print(
        "  Packages: {}".format(
            len(manifest["dependency_lock"]["dependencies"])
        )
    )
    print("  Manifest: {}".format(manifest_path))
    print("  SBOM:     {}".format(sbom_path))
    print("  Hashes:   {}".format(hashes_path))
    return 0


def verify(root: Path, manifest_path: Path):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    failures = []

    expected_version = manifest.get("journeyman_version")
    actual_version = release_version(root)
    if expected_version != actual_version:
        failures.append(
            "VERSION mismatch: manifest={} installed={}".format(
                expected_version,
                actual_version,
            )
        )

    lock = manifest.get("dependency_lock") or {}
    lock_path = root / str(lock.get("path") or "")
    expected_lock_hash = lock.get("sha256")
    if not lock_path.is_file():
        failures.append("dependency lock is missing: {}".format(lock_path))
    elif sha256_file(lock_path) != expected_lock_hash:
        failures.append("dependency lock hash does not match release manifest")

    for row in manifest.get("files") or ():
        relative = Path(str(row["path"]))
        candidate = root / relative
        if not candidate.is_file():
            failures.append("missing: {}".format(relative.as_posix()))
            continue
        actual_hash = sha256_file(candidate)
        if actual_hash != row["sha256"]:
            failures.append("modified: {}".format(relative.as_posix()))

    if failures:
        print("Journeyman release integrity: FAILED")
        for failure in failures:
            print("  - {}".format(failure))
        return 1

    print("Journeyman release integrity: VERIFIED")
    print("  Version: {}".format(actual_version))
    print("  Files:   {}".format(len(manifest.get("files") or ())))
    print(
        "  Locked packages: {}".format(
            len((manifest.get("dependency_lock") or {}).get("dependencies") or ())
        )
    )
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_parser = subparsers.add_parser(
        "generate",
        help="Generate release manifest and SPDX SBOM.",
    )
    generate_parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
    )
    generate_parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "dist",
    )
    generate_parser.add_argument(
        "--lock",
        choices=("requirements.lock", "requirements-postgresql.lock"),
        default="requirements-postgresql.lock",
    )

    verify_parser = subparsers.add_parser(
        "verify",
        help="Verify an installed source tree against a release manifest.",
    )
    verify_parser.add_argument(
        "manifest",
        type=Path,
    )
    verify_parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
    )

    args = parser.parse_args(argv)
    if args.command == "generate":
        return generate(
            args.root.resolve(),
            args.output.resolve(),
            lock_name=args.lock,
        )
    return verify(
        args.root.resolve(),
        args.manifest.resolve(),
    )


if __name__ == "__main__":
    raise SystemExit(main())
