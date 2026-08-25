"""Per-user preference helpers."""
from app import db
from app.models import UserPreference
def get_or_create_user_preferences(username):
    username = str(username or "").strip()
    preference = UserPreference.query.filter_by(username=username).first()
    if preference is None:
        preference = UserPreference(username=username)
        db.session.add(preference)
        db.session.commit()
    return preference
