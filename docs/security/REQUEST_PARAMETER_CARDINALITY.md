# Request Parameter Cardinality

Journeyman rejects duplicate values for scalar query-string and form
parameters before route logic executes. This avoids ambiguous "first value
wins" or "last value wins" behaviour when a client submits the same security-
relevant parameter more than once.

Query parameters are scalar throughout the current Journeyman HTTP interface
and therefore duplicates are rejected.

HTML forms have a small explicit allowlist of fields that are intentionally
multi-valued, such as runner capabilities, schedule weekdays, Project-step
rows, credential selections, Package rows, and filtered-inventory rule rows.
Only these known fields may contain repeated values.

New features that intentionally introduce a repeated form field must add that
field (or a narrowly-scoped pattern) to `app.request_parameters` and add a
regression test. New repeated query parameters require a design review rather
than silently changing the global rule.

Outbound redirect policy remains separately tracked. Git and Zabbix are
configured not to follow redirects; Satellite currently delegates HTTP
transport to the Foreman Ansible inventory plugin, so ASVS 15.3.2 remains
Deferred until that redirect boundary can be enforced end-to-end.
