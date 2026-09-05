"""Per-user Journeyman UI preferences."""
from datetime import datetime, timezone
from app import db
def utcnow(): return datetime.now(timezone.utc)
class UserPreference(db.Model):
    __tablename__ = "user_preferences"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(255), nullable=False, unique=True, index=True)
    hide_disabled_projects = db.Column(db.Boolean, nullable=False, default=False, server_default="0")
    hide_disabled_packages = db.Column(db.Boolean, nullable=False, default=False, server_default="0")
    rows_per_page = db.Column(db.Integer, nullable=False, default=50, server_default="50")
    idle_session_timeout_minutes = db.Column(
        db.Integer, nullable=False, default=480, server_default="480"
    )
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
