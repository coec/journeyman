# ASVS 5.0.0 V1/V2 evidence pass

This file records the first evidence-producing ASVS assessment pass for
Journeyman. It is intentionally conservative: requirements remain
`Unassessed` unless the current code and tests provide evidence strong enough
to make a defensible statement.

The initial pass concentrates on ASVS V1 **Encoding and Sanitization** and V2
**Validation and Business Logic**. It does not claim that those chapters are
complete.

## Automated hostile-input evidence

The security regression suite exercises representative input-to-sink paths:

- a classic "Bobby Tables" SQL-looking repository name is submitted through
  the real repository creation route and must remain ordinary persisted data;
- a script-tag repository name must be HTML-escaped when rendered;
- application templates are checked for explicit Jinja auto-escaping bypasses;
- application/runner/deployment Python is checked for `os.system()`,
  `os.popen()`, and `subprocess` calls that explicitly enable `shell=True`;
- Python source is checked for dynamic `eval()` / `exec()` use;
- YAML deserialization is checked for unsafe PyYAML loader entry points;
- inventory bindings accept only the deliberately narrow `{{ identifier }}`
  syntax and reject general Jinja expressions;
- Package choice inputs are checked server-side against the configured
  allowlist, independent of what the browser presented;
- Ansible configuration paths reject shell metacharacters, parent traversal,
  relative paths and invalid file suffixes.

These tests supplement, rather than replace, source review. In particular, a
representative ORM regression demonstrates the desired SQL-injection property
but future introduction of raw/textual queries still requires review.

## Deliberate Not Applicable decisions in this pass

Several V1 controls concern technologies Journeyman does not currently expose:
XPath, LaTeX processing, spreadsheet/CSV export, WYSIWYG HTML input, uploaded
SVG, user-supplied Markdown/CSS/XSL/BBCode processors, JNDI, memcache, mail
protocol submission, unmanaged/native application code, and XML parsing.
Those controls are recorded as Not Applicable with the technology-specific
reason in the matrix. If one of those technologies is introduced, its ASVS
applicability must be revisited.

## Still unassessed

Important V1/V2 areas intentionally remain open after this pass, including
canonical decoding, URL/JavaScript-context encoding, LDAP injection coverage,
SSRF, regular-expression safety/ReDoS, validation documentation completeness,
transaction/locking guarantees, high-value multi-user approval, and
anti-automation/rate-limiting controls.

Those are subsequent assessment tasks, not implicit passes.
