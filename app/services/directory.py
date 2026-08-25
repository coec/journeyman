import ssl
import uuid
from dataclasses import dataclass


AD_MATCHING_RULE_IN_CHAIN = "1.2.840.113556.1.4.1941"


class DirectoryError(RuntimeError):
    """Base error for LDAP directory operations."""


class DirectoryConfigurationError(DirectoryError):
    """Raised when LDAP settings are incomplete or unusable."""


class DirectoryUnavailableError(DirectoryError):
    """Raised when all configured directory servers are unavailable."""


class DirectoryQueryError(DirectoryError):
    """Raised when AD rejects or cannot complete a query."""


class DirectoryAuthenticationError(DirectoryError):
    """Raised when an AD username or password is not accepted."""


@dataclass(frozen=True)
class DirectoryUser:
    object_guid: str
    distinguished_name: str
    username: str
    display_name: str
    user_principal_name: str
    mail: str
    role: str = "User"


@dataclass(frozen=True)
class DirectoryGroup:
    object_guid: str
    distinguished_name: str
    sam_account_name: str
    display_name: str
    description: str


@dataclass(frozen=True)
class AuthenticatedDirectoryUser:
    user: DirectoryUser
    role: str
    groups: tuple


def _load_ldap3():
    try:
        import ldap3
        from ldap3.core.exceptions import LDAPException
        from ldap3.utils.conv import escape_filter_chars
    except ImportError as exc:
        raise DirectoryConfigurationError(
            "The ldap3 Python package is not installed."
        ) from exc

    return ldap3, LDAPException, escape_filter_chars


def _guid_from_entry(entry):
    raw_values = getattr(
        getattr(entry, "objectGUID", None),
        "raw_values",
        (),
    )

    if raw_values:
        raw_value = raw_values[0]

        if isinstance(raw_value, bytes) and len(raw_value) == 16:
            return str(uuid.UUID(bytes_le=raw_value))

    value = str(
        getattr(
            getattr(entry, "objectGUID", None),
            "value",
            "",
        )
        or ""
    ).strip()

    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError):
        return value


def _attribute(entry, name):
    attribute = getattr(entry, name, None)

    if attribute is None:
        return ""

    value = getattr(attribute, "value", "")

    if value is None:
        return ""

    return str(value)


def _directory_user(entry, *, role="User"):
    object_guid = _guid_from_entry(entry)

    if not object_guid:
        raise DirectoryQueryError(
            "An Active Directory user result did not include objectGUID."
        )

    return DirectoryUser(
        object_guid=object_guid,
        distinguished_name=str(entry.entry_dn),
        username=_attribute(entry, "sAMAccountName"),
        display_name=(
            _attribute(entry, "displayName")
            or _attribute(entry, "name")
            or _attribute(entry, "sAMAccountName")
        ),
        user_principal_name=_attribute(
            entry,
            "userPrincipalName",
        ),
        mail=_attribute(entry, "mail"),
        role=role,
    )


def _directory_group(entry):
    object_guid = _guid_from_entry(entry)

    if not object_guid:
        raise DirectoryQueryError(
            "An Active Directory group result did not include objectGUID."
        )

    return DirectoryGroup(
        object_guid=object_guid,
        distinguished_name=str(entry.entry_dn),
        sam_account_name=_attribute(
            entry,
            "sAMAccountName",
        ),
        display_name=(
            _attribute(entry, "displayName")
            or _attribute(entry, "name")
            or _attribute(entry, "sAMAccountName")
        ),
        description=_attribute(entry, "description"),
    )


class DirectoryClient:
    """
    Small Active Directory client with ordered LDAP-server failover.

    A fresh connection is used for each high-level request. This keeps
    failure handling deterministic and avoids retaining bind credentials
    in a long-lived global connection object.
    """

    def __init__(self, settings):
        self.settings = settings

    def _enabled_servers(self):
        return [
            server
            for server in self.settings.servers
            if server.enabled
        ]

    def _validate_configuration(self, *, require_enabled=True):
        if require_enabled and not self.settings.enabled:
            raise DirectoryConfigurationError(
                "LDAP directory integration is disabled."
            )

        if len(self._enabled_servers()) < 2:
            raise DirectoryConfigurationError(
                "At least two enabled directory servers are required."
            )

        if not self.settings.bind_username:
            raise DirectoryConfigurationError(
                "LDAP bind username is not configured."
            )

        if not self.settings.has_bind_password():
            raise DirectoryConfigurationError(
                "LDAP bind password is not configured."
            )

    def _connection(self, server_row):
        ldap3, _, _ = _load_ldap3()

        tls = ldap3.Tls(
            validate=ssl.CERT_REQUIRED,
            ca_certs_file=(
                self.settings.ca_certificate_path
                or None
            ),
            version=ssl.PROTOCOL_TLS_CLIENT,
        )

        server = ldap3.Server(
            server_row.host,
            port=server_row.port,
            use_ssl=server_row.use_ssl,
            tls=tls,
            connect_timeout=(
                self.settings.connect_timeout_seconds
            ),
            get_info=ldap3.NONE,
        )

        return ldap3.Connection(
            server,
            user=self.settings.bind_username,
            password=self.settings.get_bind_password(),
            auto_bind=ldap3.AUTO_BIND_NO_TLS,
            receive_timeout=(
                self.settings.operation_timeout_seconds
            ),
            raise_exceptions=True,
        )

    def _with_failover(self, operation, *, require_enabled=True):
        self._validate_configuration(
            require_enabled=require_enabled
        )

        _, LDAPException, _ = _load_ldap3()
        failures = []

        for server_row in self._enabled_servers():
            connection = None

            try:
                connection = self._connection(server_row)
                return operation(connection, server_row)
            except (LDAPException, OSError, ssl.SSLError) as exc:
                failures.append(
                    "{}:{}: {}".format(
                        server_row.host,
                        server_row.port,
                        str(exc).replace("\x00", ""),
                    )
                )
            finally:
                if connection is not None:
                    try:
                        connection.unbind()
                    except Exception:
                        pass

        raise DirectoryUnavailableError(
            "All configured directory servers failed. {}"
            .format("; ".join(failures))
        )

    def test_servers(self):
        """Test every enabled server independently."""

        self._validate_configuration(require_enabled=False)

        _, LDAPException, _ = _load_ldap3()
        results = []

        for server_row in self._enabled_servers():
            connection = None

            try:
                connection = self._connection(server_row)
                ok = bool(connection.bound)
                message = (
                    "Bind successful."
                    if ok
                    else "Bind did not complete."
                )
            except (LDAPException, OSError, ssl.SSLError) as exc:
                ok = False
                message = str(exc).replace("\x00", "")
            finally:
                if connection is not None:
                    try:
                        connection.unbind()
                    except Exception:
                        pass

            results.append(
                {
                    "server": server_row,
                    "ok": ok,
                    "message": message,
                }
            )

        return results

    def _search(
        self,
        connection,
        *,
        base,
        ldap_filter,
        attributes,
        size_limit=0,
        search_scope=None,
    ):
        ldap3, _, _ = _load_ldap3()

        ok = connection.search(
            search_base=base,
            search_filter=ldap_filter,
            search_scope=(
                ldap3.SUBTREE
                if search_scope is None
                else search_scope
            ),
            attributes=attributes,
            size_limit=size_limit,
            time_limit=(
                self.settings.operation_timeout_seconds
            ),
        )

        if not ok and connection.result.get("result") not in (0, 4):
            raise DirectoryQueryError(
                connection.result.get(
                    "message",
                    "LDAP search failed.",
                )
            )

        return list(connection.entries)

    def _group_by_name_on_connection(
        self,
        connection,
        group_name,
    ):
        _, _, escape_filter_chars = _load_ldap3()
        escaped = escape_filter_chars(str(group_name))

        ldap_filter = (
            "(&(objectCategory=group)"
            "(|(sAMAccountName={0})(cn={0})(name={0})))"
        ).format(escaped)

        entries = self._search(
            connection,
            base=self.settings.group_search_base,
            ldap_filter=ldap_filter,
            attributes=[
                "objectGUID",
                "distinguishedName",
                "sAMAccountName",
                "displayName",
                "name",
                "description",
            ],
            size_limit=2,
        )

        if len(entries) != 1:
            raise DirectoryQueryError(
                "AD group {!r} did not resolve uniquely."
                .format(group_name)
            )

        return _directory_group(entries[0])

    def find_group_by_name(self, group_name):
        return self._with_failover(
            lambda connection, _server: (
                self._group_by_name_on_connection(
                    connection,
                    group_name,
                )
            )
        )

    def _validate_search_base_on_connection(
        self,
        connection,
        distinguished_name,
        label,
    ):
        ldap3, _, _ = _load_ldap3()

        entries = self._search(
            connection,
            base=distinguished_name,
            ldap_filter="(objectClass=*)",
            attributes=["distinguishedName"],
            size_limit=1,
            search_scope=ldap3.BASE,
        )

        if len(entries) != 1:
            raise DirectoryQueryError(
                "{} {!r} did not resolve to exactly one AD object."
                .format(label, distinguished_name)
            )

    def validate_role_groups(self, *, require_enabled=True):
        def operation(connection, _server):
            return (
                self._group_by_name_on_connection(
                    connection,
                    self.settings.administrator_group_name,
                ),
                self._group_by_name_on_connection(
                    connection,
                    self.settings.user_group_name,
                ),
            )

        return self._with_failover(
            operation,
            require_enabled=require_enabled,
        )

    def validate_directory_configuration(
        self,
        *,
        require_enabled=True,
    ):
        """Validate search bases, role groups, and user enumeration."""

        def operation(connection, _server):
            self._validate_search_base_on_connection(
                connection,
                self.settings.base_dn,
                "Base DN",
            )
            self._validate_search_base_on_connection(
                connection,
                self.settings.user_search_base,
                "User search base",
            )
            self._validate_search_base_on_connection(
                connection,
                self.settings.group_search_base,
                "Group search base",
            )

            admin_group = self._group_by_name_on_connection(
                connection,
                self.settings.administrator_group_name,
            )
            user_group = self._group_by_name_on_connection(
                connection,
                self.settings.user_group_name,
            )

            users_by_guid = {}

            for user in self._group_users_on_connection(
                connection,
                user_group,
                role="User",
            ):
                users_by_guid[user.object_guid] = user

            for user in self._group_users_on_connection(
                connection,
                admin_group,
                role="Administrator",
            ):
                users_by_guid[user.object_guid] = user

            return {
                "administrator_group": admin_group,
                "user_group": user_group,
                "eligible_user_count": len(users_by_guid),
            }

        return self._with_failover(
            operation,
            require_enabled=require_enabled,
        )

    def find_group_by_dn(self, distinguished_name):
        _, _, escape_filter_chars = _load_ldap3()
        escaped = escape_filter_chars(
            str(distinguished_name)
        )

        ldap_filter = (
            "(&(objectCategory=group)"
            "(distinguishedName={}))"
        ).format(escaped)

        def operation(connection, _server):
            entries = self._search(
                connection,
                base=self.settings.group_search_base,
                ldap_filter=ldap_filter,
                attributes=[
                    "objectGUID",
                    "distinguishedName",
                    "sAMAccountName",
                    "displayName",
                    "name",
                    "description",
                ],
                size_limit=2,
            )

            if len(entries) != 1:
                raise DirectoryQueryError(
                    "The selected AD group no longer resolves uniquely."
                )

            return _directory_group(entries[0])

        return self._with_failover(operation)

    def search_groups(self, query, *, limit=50):
        query = str(query or "").strip()

        if len(query) < 2:
            return []

        _, _, escape_filter_chars = _load_ldap3()
        escaped = escape_filter_chars(query)

        ldap_filter = (
            "(&(objectCategory=group)"
            "(|(sAMAccountName=*{0}*)"
            "(cn=*{0}*)(displayName=*{0}*)))"
        ).format(escaped)

        def operation(connection, _server):
            entries = self._search(
                connection,
                base=self.settings.group_search_base,
                ldap_filter=ldap_filter,
                attributes=[
                    "objectGUID",
                    "distinguishedName",
                    "sAMAccountName",
                    "displayName",
                    "name",
                    "description",
                ],
                size_limit=limit,
            )

            groups = [
                _directory_group(entry)
                for entry in entries
            ]

            return sorted(
                groups,
                key=lambda group: (
                    group.display_name.casefold(),
                    group.sam_account_name.casefold(),
                ),
            )

        return self._with_failover(operation)

    def _group_users_on_connection(
        self,
        connection,
        group,
        *,
        role="User",
    ):
        _, _, escape_filter_chars = _load_ldap3()
        escaped_dn = escape_filter_chars(
            group.distinguished_name
        )

        if self.settings.include_nested_groups:
            membership_filter = (
                "(memberOf:{}:={})"
                .format(
                    AD_MATCHING_RULE_IN_CHAIN,
                    escaped_dn,
                )
            )
        else:
            membership_filter = "(memberOf={})".format(
                escaped_dn
            )

        ldap_filter = (
            "(&(objectCategory=person)(objectClass=user){}"
            "(!(userAccountControl:1.2.840.113556.1.4.803:=2)))"
        ).format(membership_filter)

        entries = self._search(
            connection,
            base=self.settings.user_search_base,
            ldap_filter=ldap_filter,
            attributes=[
                "objectGUID",
                "distinguishedName",
                "sAMAccountName",
                "userPrincipalName",
                "displayName",
                "name",
                "mail",
            ],
        )

        users = [
            _directory_user(entry, role=role)
            for entry in entries
            if _attribute(entry, "sAMAccountName")
        ]

        return sorted(
            users,
            key=lambda user: (
                user.display_name.casefold(),
                user.username.casefold(),
            ),
        )

    def group_users(self, group, *, role="User"):
        return self._with_failover(
            lambda connection, _server: (
                self._group_users_on_connection(
                    connection,
                    group,
                    role=role,
                )
            )
        )

    def _find_user_on_connection(self, connection, username):
        _, _, escape_filter_chars = _load_ldap3()
        raw_username = str(username or "").strip()

        if "\\" in raw_username:
            raw_username = raw_username.rsplit("\\", 1)[-1]

        escaped_full = escape_filter_chars(raw_username)
        escaped_sam = escape_filter_chars(raw_username.split("@", 1)[0])
        ldap_filter = (
            "(&(objectCategory=person)(objectClass=user)"
            "(!(userAccountControl:1.2.840.113556.1.4.803:=2))"
            "(|(sAMAccountName={0})(userPrincipalName={1})))"
        ).format(escaped_sam, escaped_full)

        entries = self._search(
            connection,
            base=self.settings.user_search_base,
            ldap_filter=ldap_filter,
            attributes=[
                "objectGUID",
                "distinguishedName",
                "sAMAccountName",
                "userPrincipalName",
                "displayName",
                "name",
                "mail",
            ],
            size_limit=2,
        )

        if len(entries) != 1:
            raise DirectoryAuthenticationError(
                "The supplied Active Directory identity was not accepted."
            )

        return _directory_user(entries[0])

    def _user_groups_on_connection(self, connection, user):
        _, _, escape_filter_chars = _load_ldap3()
        escaped_dn = escape_filter_chars(user.distinguished_name)

        if self.settings.include_nested_groups:
            membership_filter = (
                "(member:{}:={})".format(
                    AD_MATCHING_RULE_IN_CHAIN,
                    escaped_dn,
                )
            )
        else:
            membership_filter = "(member={})".format(escaped_dn)

        entries = self._search(
            connection,
            base=self.settings.group_search_base,
            ldap_filter="(&(objectCategory=group){})".format(membership_filter),
            attributes=[
                "objectGUID",
                "distinguishedName",
                "sAMAccountName",
                "displayName",
                "name",
                "description",
            ],
        )

        return tuple(_directory_group(entry) for entry in entries)

    def _resolve_user_access_on_connection(self, connection, user):
        groups = self._user_groups_on_connection(
            connection,
            user,
        )
        groups_by_name = {
            group.sam_account_name.casefold(): group
            for group in groups
        }

        admin_key = self.settings.administrator_group_name.casefold()
        user_key = self.settings.user_group_name.casefold()

        if admin_key in groups_by_name:
            role = "Administrator"
        elif user_key in groups_by_name:
            role = "User"
        else:
            raise DirectoryAuthenticationError(
                "The user is not assigned a Journeyman role."
            )

        return AuthenticatedDirectoryUser(
            user=user,
            role=role,
            groups=groups,
        )

    def resolve_user_access(self, username):
        """Resolve an existing AD user's current enabled state, role, and groups.

        This uses the configured directory service account and does not require
        the user's password. Disabled/deleted/unresolvable users and users that
        no longer belong to a Journeyman role group are rejected.
        """

        def operation(connection, _server):
            user = self._find_user_on_connection(connection, username)
            return self._resolve_user_access_on_connection(connection, user)

        return self._with_failover(operation, require_enabled=True)

    def authenticate_user(self, username, password):
        """Authenticate one AD user and resolve role and Team groups."""

        self._validate_configuration(require_enabled=True)

        if not str(username or "").strip() or not str(password or ""):
            raise DirectoryAuthenticationError(
                "The supplied Active Directory identity was not accepted."
            )

        ldap3, LDAPException, _ = _load_ldap3()
        failures = []

        for server_row in self._enabled_servers():
            service_connection = None
            user_connection = None

            try:
                service_connection = self._connection(server_row)
                user = self._find_user_on_connection(
                    service_connection,
                    username,
                )

                tls = ldap3.Tls(
                    validate=ssl.CERT_REQUIRED,
                    ca_certs_file=self.settings.ca_certificate_path or None,
                    version=ssl.PROTOCOL_TLS_CLIENT,
                )
                server = ldap3.Server(
                    server_row.host,
                    port=server_row.port,
                    use_ssl=server_row.use_ssl,
                    tls=tls,
                    connect_timeout=self.settings.connect_timeout_seconds,
                    get_info=ldap3.NONE,
                )
                user_connection = ldap3.Connection(
                    server,
                    user=user.distinguished_name,
                    password=password,
                    receive_timeout=self.settings.operation_timeout_seconds,
                    raise_exceptions=False,
                )

                if not user_connection.bind():
                    result_code = int(user_connection.result.get("result", -1))
                    if result_code == 49:
                        raise DirectoryAuthenticationError(
                            "The supplied Active Directory identity was not accepted."
                        )
                    raise DirectoryQueryError(
                        user_connection.result.get("message")
                        or "Active Directory user bind failed."
                    )

                return self._resolve_user_access_on_connection(
                    service_connection,
                    user,
                )
            except DirectoryAuthenticationError:
                raise
            except (LDAPException, OSError, ssl.SSLError, DirectoryQueryError) as exc:
                failures.append(
                    "{}:{}: {}".format(
                        server_row.host,
                        server_row.port,
                        str(exc).replace("\x00", ""),
                    )
                )
            finally:
                for connection in (user_connection, service_connection):
                    if connection is not None:
                        try:
                            connection.unbind()
                        except Exception:
                            pass

        raise DirectoryUnavailableError(
            "All configured directory servers failed. {}".format(
                "; ".join(failures)
            )
        )

    def role_users(self):
        def operation(connection, _server):
            admin_group = self._group_by_name_on_connection(
                connection,
                self.settings.administrator_group_name,
            )
            user_group = self._group_by_name_on_connection(
                connection,
                self.settings.user_group_name,
            )

            users_by_guid = {}

            for user in self._group_users_on_connection(
                connection,
                user_group,
                role="User",
            ):
                users_by_guid[user.object_guid] = user

            for user in self._group_users_on_connection(
                connection,
                admin_group,
                role="Administrator",
            ):
                users_by_guid[user.object_guid] = user

            return sorted(
                users_by_guid.values(),
                key=lambda user: (
                    user.display_name.casefold(),
                    user.username.casefold(),
                ),
            )

        return self._with_failover(operation)


def get_directory_client(settings):
    return DirectoryClient(settings)
