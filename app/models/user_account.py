from datetime import datetime, timezone

from app import db


def utcnow():
    return datetime.now(timezone.utc)


user_account_role = db.Table(
    "user_account_role",
    db.Column(
        "user_account_id",
        db.Integer,
        db.ForeignKey("user_account.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "role_id",
        db.Integer,
        db.ForeignKey("authorization_role.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


user_account_right = db.Table(
    "user_account_right",
    db.Column(
        "user_account_id",
        db.Integer,
        db.ForeignKey("user_account.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "right_id",
        db.Integer,
        db.ForeignKey("authorization_right.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class AuthorizationRole(db.Model):
    """Journeyman-local coarse-grained user role."""

    __tablename__ = "authorization_role"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), nullable=False, unique=True)
    description = db.Column(db.String(500), nullable=False, default="")
    builtin = db.Column(db.Boolean, nullable=False, default=True)

    users = db.relationship(
        "UserAccount",
        secondary=user_account_role,
        back_populates="roles",
    )


class AuthorizationRight(db.Model):
    """Journeyman-local capability that can be granted independently of roles."""

    __tablename__ = "authorization_right"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), nullable=False, unique=True)
    description = db.Column(db.String(500), nullable=False, default="")
    builtin = db.Column(db.Boolean, nullable=False, default=True)

    users = db.relationship(
        "UserAccount",
        secondary=user_account_right,
        back_populates="rights",
    )


class UserAccount(db.Model):
    """Local Journeyman authorization record for an authenticated identity."""

    __tablename__ = "user_account"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(255), nullable=False, unique=True, index=True)
    display_name = db.Column(db.String(255), nullable=False, default="")
    directory_object_guid = db.Column(db.String(64), nullable=True, unique=True, index=True)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )
    last_login_at = db.Column(db.DateTime(timezone=True), nullable=True)

    roles = db.relationship(
        "AuthorizationRole",
        secondary=user_account_role,
        back_populates="users",
        order_by="AuthorizationRole.name",
    )
    rights = db.relationship(
        "AuthorizationRight",
        secondary=user_account_right,
        back_populates="users",
        order_by="AuthorizationRight.name",
    )
    teams = db.relationship(
        "Team",
        secondary="team_user_account",
        back_populates="members",
        order_by="Team.display_name",
    )

    def has_role(self, name):
        wanted = str(name or "").strip().casefold()
        return any(role.name.casefold() == wanted for role in self.roles)

    def has_right(self, name):
        wanted = str(name or "").strip().casefold()
        return any(right.name.casefold() == wanted for right in self.rights)
