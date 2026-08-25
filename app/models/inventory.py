from app import db


class Inventory(db.Model):
    """
    An inventory available to Journeyman projects.

    Inventory configuration is stored in this table while provider-
    specific behaviour is implemented by inventory services.

    Supported inventory types will include:

        static
        satellite
        zabbix
        ovirt
        composite
    """

    __tablename__ = "inventory_source"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    name = db.Column(
        db.String(120),
        unique=True,
        nullable=False,
    )

    inventory_type = db.Column(
        db.String(32),
        nullable=False,
    )

    endpoint = db.Column(
        db.String(1000),
        nullable=False,
        default="",
    )

    credential_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "credential.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )

    credential = db.relationship(
        "Credential",
        foreign_keys=[credential_id],
    )

    verify_tls = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
    )

    enabled = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
    )

    config_json = db.Column(
        db.Text,
        nullable=False,
        default="{}",
    )

    last_sync_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    status = db.Column(
        db.String(32),
        nullable=False,
        default="never_synced",
    )

    projects = db.relationship(
        "Project",
        back_populates="inventory",
    )

    project_steps = db.relationship(
        "ProjectStep",
        back_populates="inventory",
    )

    def __repr__(self):
        return (
            f"<Inventory id={self.id} "
            f"name={self.name!r} "
            f"inventory_type={self.inventory_type!r}>"
        )
