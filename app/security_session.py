"""Browser-session hardening helpers."""

from flask.sessions import SecureCookieSessionInterface


class BoundedSecureCookieSessionInterface(SecureCookieSessionInterface):
    """Refuse to emit oversized client-side Flask session cookies.

    Journeyman deliberately keeps only small authentication metadata in the
    signed browser session. If application changes accidentally grow that
    payload beyond the configured bound, fail closed by clearing the session
    rather than emitting an oversized cookie that browsers or intermediaries
    may truncate inconsistently.
    """

    def save_session(self, app, session, response):
        if session:
            serializer = self.get_signing_serializer(app)
            if serializer is not None:
                serialized = serializer.dumps(dict(session))
                maximum = int(app.config.get("MAX_SESSION_COOKIE_BYTES", 3072))
                if maximum <= 0:
                    raise RuntimeError("MAX_SESSION_COOKIE_BYTES must be positive.")
                if len(serialized.encode("utf-8")) > maximum:
                    app.logger.error(
                        "Refusing to emit oversized authenticated session cookie."
                    )
                    session.clear()
                    session.modified = True
        return super().save_session(app, session, response)
