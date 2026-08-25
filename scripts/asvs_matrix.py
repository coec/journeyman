#!/usr/bin/env python3
"""Validate and summarize Journeyman's OWASP ASVS 5.0.0 control matrix."""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = ROOT / "docs" / "security" / "ASVS_MATRIX.csv"
DEFAULT_DEFERRED_REVIEW = ROOT / "docs" / "security" / "ASVS_DEFERRED.csv"
ALLOWED_DEFERRED_DISPOSITIONS = {
    "Pre-release required",
    "Pre-release desirable",
    "Accepted post-release",
}
EXPECTED_REQUIREMENTS = 345
ALLOWED_APPLICABILITY = {"Unassessed", "Applicable", "Not Applicable"}
ALLOWED_STATUS = {
    "Unassessed",
    "Automated",
    "Pipeline Verified",
    "Manually Verified",
    "Not Applicable",
    "Deferred",
}
REQUIRED_FIELDS = {
    "asvs_requirement",
    "chapter",
    "section",
    "level",
    "applicability",
    "status",
    "evidence",
    "notes",
}


def load_matrix(path: Path):
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or ())
        missing = REQUIRED_FIELDS - fields
        if missing:
            raise ValueError(
                "Matrix is missing required columns: {}".format(
                    ", ".join(sorted(missing))
                )
            )
        return list(reader)


def validate(rows):
    errors = []
    ids = Counter(row["asvs_requirement"] for row in rows)

    if len(rows) != EXPECTED_REQUIREMENTS:
        errors.append(
            "Expected {} ASVS 5.0.0 requirements, found {}.".format(
                EXPECTED_REQUIREMENTS, len(rows)
            )
        )

    duplicates = sorted(req_id for req_id, count in ids.items() if count != 1)
    if duplicates:
        errors.append(
            "Duplicate/missing-unique requirement IDs: {}".format(
                ", ".join(duplicates)
            )
        )

    for index, row in enumerate(rows, start=2):
        req_id = row["asvs_requirement"] or "<blank>"
        if not row["asvs_requirement"].startswith("v5.0.0-"):
            errors.append(
                "Line {} ({}) does not use a versioned v5.0.0 requirement ID.".format(
                    index, req_id
                )
            )
        if row["level"] not in {"1", "2", "3"}:
            errors.append(
                "Line {} ({}) has invalid level {!r}.".format(
                    index, req_id, row["level"]
                )
            )
        if row["applicability"] not in ALLOWED_APPLICABILITY:
            errors.append(
                "Line {} ({}) has invalid applicability {!r}.".format(
                    index, req_id, row["applicability"]
                )
            )
        if row["status"] not in ALLOWED_STATUS:
            errors.append(
                "Line {} ({}) has invalid status {!r}.".format(
                    index, req_id, row["status"]
                )
            )
        if row["applicability"] == "Not Applicable" and row["status"] != "Not Applicable":
            errors.append(
                "Line {} ({}) is Not Applicable but status is {!r}.".format(
                    index, req_id, row["status"]
                )
            )
        if row["status"] == "Not Applicable" and row["applicability"] != "Not Applicable":
            errors.append(
                "Line {} ({}) has Not Applicable status without matching applicability.".format(
                    index, req_id
                )
            )
        if row["status"] not in {"Unassessed", "Not Applicable", "Deferred"} and not row["evidence"].strip():
            errors.append(
                "Line {} ({}) has status {!r} but no evidence.".format(
                    index, req_id, row["status"]
                )
            )
        if row["status"] in {"Not Applicable", "Deferred"} and not row["notes"].strip():
            errors.append(
                "Line {} ({}) has status {!r} but no justification in notes.".format(
                    index, req_id, row["status"]
                )
            )

    return errors


def load_deferred_review(path: Path):
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {
            "asvs_requirement",
            "chapter",
            "level",
            "disposition",
            "remediation",
            "current_gap",
        }
        fields = set(reader.fieldnames or ())
        missing = required - fields
        if missing:
            raise ValueError(
                "Deferred review is missing required columns: {}".format(
                    ", ".join(sorted(missing))
                )
            )
        return list(reader)


def validate_deferred_review(matrix_rows, review_rows):
    errors = []
    matrix_deferred = {
        row["asvs_requirement"]
        for row in matrix_rows
        if row["status"] == "Deferred"
    }
    review_ids = [row["asvs_requirement"] for row in review_rows]
    review_set = set(review_ids)

    duplicates = sorted(
        req_id
        for req_id, count in Counter(review_ids).items()
        if count != 1
    )
    if duplicates:
        errors.append(
            "Deferred review contains duplicate requirement IDs: {}".format(
                ", ".join(duplicates)
            )
        )

    missing = sorted(matrix_deferred - review_set)
    stale = sorted(review_set - matrix_deferred)
    if missing:
        errors.append(
            "Deferred requirements missing release disposition: {}".format(
                ", ".join(missing)
            )
        )
    if stale:
        errors.append(
            "Deferred review contains requirements no longer Deferred: {}".format(
                ", ".join(stale)
            )
        )

    matrix_by_id = {row["asvs_requirement"]: row for row in matrix_rows}
    for index, row in enumerate(review_rows, start=2):
        req_id = row["asvs_requirement"] or "<blank>"
        if row["disposition"] not in ALLOWED_DEFERRED_DISPOSITIONS:
            errors.append(
                "Deferred line {} ({}) has invalid disposition {!r}.".format(
                    index, req_id, row["disposition"]
                )
            )
        if not row["remediation"].strip():
            errors.append(
                "Deferred line {} ({}) has no remediation.".format(
                    index, req_id
                )
            )
        matrix_row = matrix_by_id.get(row["asvs_requirement"])
        if matrix_row is not None:
            if row["chapter"] != matrix_row["chapter"]:
                errors.append(
                    "Deferred line {} ({}) chapter does not match matrix.".format(
                        index, req_id
                    )
                )
            if row["level"] != matrix_row["level"]:
                errors.append(
                    "Deferred line {} ({}) level does not match matrix.".format(
                        index, req_id
                    )
                )

    return errors


def print_deferred_summary(review_rows):
    dispositions = Counter(row["disposition"] for row in review_rows)
    print("\nDeferred release disposition:")
    for key in (
        "Pre-release required",
        "Pre-release desirable",
        "Accepted post-release",
    ):
        print("  {:<24} {}".format(key + ":", dispositions.get(key, 0)))


def print_summary(rows):
    statuses = Counter(row["status"] for row in rows)
    applicability = Counter(row["applicability"] for row in rows)
    unassessed_by_chapter = defaultdict(int)
    for row in rows:
        if row["status"] == "Unassessed" or row["applicability"] == "Unassessed":
            unassessed_by_chapter[row["chapter"]] += 1

    print("OWASP ASVS 5.0.0 Journeyman matrix")
    print("Requirements: {}".format(len(rows)))
    print("\nApplicability:")
    for key in ("Applicable", "Not Applicable", "Unassessed"):
        print("  {:<20} {}".format(key + ":", applicability.get(key, 0)))
    print("\nVerification status:")
    for key in (
        "Pipeline Verified",
        "Automated",
        "Manually Verified",
        "Not Applicable",
        "Deferred",
        "Unassessed",
    ):
        print("  {:<20} {}".format(key + ":", statuses.get(key, 0)))

    if unassessed_by_chapter:
        print("\nUnassessed by chapter:")
        for chapter, count in sorted(unassessed_by_chapter.items()):
            print("  {:<42} {}".format(chapter + ":", count))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--matrix",
        type=Path,
        default=DEFAULT_MATRIX,
        help="path to ASVS matrix CSV",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate matrix structure and evidence rules",
    )
    args = parser.parse_args()

    try:
        rows = load_matrix(args.matrix)
        deferred_review = load_deferred_review(DEFAULT_DEFERRED_REVIEW)
        errors = validate(rows)
        errors.extend(validate_deferred_review(rows, deferred_review))
    except (OSError, ValueError) as exc:
        print("ASVS matrix error: {}".format(exc), file=sys.stderr)
        return 2

    print_summary(rows)
    print_deferred_summary(deferred_review)

    if args.check and errors:
        print("\nValidation errors:", file=sys.stderr)
        for error in errors:
            print("  - {}".format(error), file=sys.stderr)
        return 1

    if errors:
        print("\nValidation warnings: {} (run with --check to fail)".format(len(errors)))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
