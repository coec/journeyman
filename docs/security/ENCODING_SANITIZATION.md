# Encoding and Sanitization

Journeyman assesses encoding and sanitization against OWASP ASVS 5.0.0 V1.
The preferred control is to keep untrusted data separate from interpreter
syntax rather than attempting to remove strings which merely look dangerous.

## Architecture

Incoming values are parsed once by the framework or the relevant structured
parser and are then validated according to the semantics of the field. The
application does not intentionally HTML-encode or otherwise escape values
before persistence. Encoding is applied at the sink: Jinja autoescaping is
used for HTML, `tojson` is used for values embedded in JavaScript, SQLAlchemy
is used for database queries, ldap3 filter escaping is used for values placed
in LDAP filters, and subprocesses use argument vectors without shell
interpretation.

The regression suite includes the classic "Bobby Tables" SQL-looking input to
verify that suspicious punctuation remains data rather than executable SQL.
It also checks HTML escaping, JavaScript/JSON embedding, OS-command boundaries,
safe YAML loading, LDAP filter construction, and the deliberately restricted
inventory-binding grammar.

## URLs

Internal browser URLs are generated with Flask `url_for()`. Redirect targets
supplied by a request pass through `_safe_next_url()` and external, protocol-
relative, `javascript:` and similar destinations are rejected. Remote runner
control-plane URLs additionally require HTTPS.

Administratively configured external endpoints such as inventory providers and
Git repositories are trusted configuration rather than ordinary end-user
input. Full outbound destination allowlisting/SSRF protection is not yet a
universal application control and remains tracked as deferred.

## LDAP

Active Directory search values are escaped with ldap3's
`escape_filter_chars()` before interpolation into LDAP filters. The LDAP filter
syntax itself remains application-controlled.

## Regular expressions

Package administrators may define validation regular expressions. Runtime
Package input values are matched against those configured expressions and are
not interpolated into regular-expression syntax, so regex-injection by a
Package launcher is not applicable. However, an administrator can currently
configure a pathological expression with excessive backtracking. Formal ReDoS
protection for configured validation patterns is therefore deferred.

## Template and format-string boundaries

Inventory bindings support only scalar `{{ identifier }}` substitution and do
not execute general Jinja syntax. Application format strings are developer-
controlled; untrusted values are supplied as arguments rather than used as the
format specification itself.

## Parser consistency

Journeyman uses the Python standard JSON parser, PyYAML safe loaders, Flask URL
routing, and urllib URL parsing in separate contexts. A comprehensive Level 3
cross-parser differential test suite has not yet been implemented, so the ASVS
parser-consistency requirement remains deferred.
