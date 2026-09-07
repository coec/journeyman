"""Seed filesystem-backed evaluation content.

This deliberately creates only the disk Repository record. The sample static
inventory is supplied as a file so evaluators can see and edit the exact
inventory content before adding it through the normal Journeyman UI.
"""

from pathlib import Path

from app import create_app, db
from app.models.repository import Repository


DEMO_NAME = "Journeyman examples"
DEMO_PATH = Path("/var/lib/journeyman/repositories/demo-repository")


def main():
    app = create_app()

    with app.app_context():
        repository = Repository.query.filter_by(name=DEMO_NAME).first()

        if repository is None:
            repository = Repository(
                name=DEMO_NAME,
                description=(
                    "Built-in evaluation playbooks and scripts. "
                    "Safe to edit inside the evaluation container."
                ),
                repository_type="directory",
                directory_path=str(DEMO_PATH),
                status="ready",
            )
            db.session.add(repository)
        else:
            # Keep the seeded record usable if an older evaluation image used
            # a different path.
            repository.repository_type = "directory"
            repository.directory_path = str(DEMO_PATH)

        db.session.commit()

    print("Evaluation repository ready: {}".format(DEMO_PATH))


if __name__ == "__main__":
    main()

