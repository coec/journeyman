# Building Journeyman RPMs

This directory contains `journeyman.spec`, which builds three RPMs:

| Package | Contents |
| --- | --- |
| `journeyman` | Application source and Ansible deployment files under `/opt/journeyman` |
| `journeyman-devel` | Tests and developer resources under `/usr/share/journeyman-devel` |
| `journeyman-collection` | The `journeyman.configuration` and `journeyman.operation` Ansible collections under `/usr/share/ansible/collections/ansible_collections/journeyman` |

The RPMs **stage files only**. Installing or upgrading the application, configuring services, Python environments, and databases remain the responsibility of the existing Ansible installer. The spec does not download or bundle a Python wheelhouse and does not automatically run the installer.

## Prerequisites

Build on the intended target RHEL major version (for example, RHEL 9 for an `.el9` build). Use a dedicated build host or clean build environment where possible. Install the build tools:

```bash
dnf install -y rpm-build rpmdevtools
```

The examples below assume the extracted source tree is named `journeyman-main`, and the commands are run as the user building the RPMs. Root is not required for `rpmbuild` itself.

## 1. Prepare the RPM build tree

```bash
rpmdev-setuptree
```

This creates `~/rpmbuild/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}`.

## 2. Check the version

From the root of the extracted Journeyman source:

```bash
cd /path/to/journeyman-main
cat VERSION
grep -E '^(Name|Version|Release):' contrib/RPM/journeyman.spec
```

Set `Version:` in `contrib/RPM/journeyman.spec` to the exact version in `VERSION`. Do not use an older version from example commands. For example, for Journeyman **2.0.0**, the spec must say `Version: 2.0.0`.

## 3. Create the source archive

Run the following **from inside the extracted source root**. It creates the archive with the directory name expected by `%setup` in the spec:

```bash
VERSION=$(cat VERSION)
SRC_DIR=$(basename "$PWD")
cd ..
tar -czf "$HOME/rpmbuild/SOURCES/journeyman-${VERSION}.tar.gz" \
  --transform="s,^${SRC_DIR},journeyman-${VERSION}," \
  "$SRC_DIR"
```

Check the archive before building:

```bash
tar -tzf "$HOME/rpmbuild/SOURCES/journeyman-${VERSION}.tar.gz" | head
```

The paths should begin with `journeyman-<version>/` (for example, `journeyman-2.0.0/`). The source tree must include both existing Ansible collections at `ansible_collections/journeyman/configuration` and `ansible_collections/journeyman/operation`.

## 4. Copy the spec and build

From the parent directory of the extracted source:

```bash
cp "$SRC_DIR/contrib/RPM/journeyman.spec" "$HOME/rpmbuild/SPECS/"
rpmbuild -ba "$HOME/rpmbuild/SPECS/journeyman.spec"
```

`-ba` builds the source RPM and all three binary RPMs. The output is under:

```text
~/rpmbuild/SRPMS/journeyman-<version>-<release>.src.rpm
~/rpmbuild/RPMS/noarch/journeyman-<version>-<release>.noarch.rpm
~/rpmbuild/RPMS/noarch/journeyman-devel-<version>-<release>.noarch.rpm
~/rpmbuild/RPMS/noarch/journeyman-collection-<version>-<release>.noarch.rpm
```

The release normally includes a distribution suffix such as `.el9`.

## 5. Inspect the packages

```bash
ls -lh "$HOME/rpmbuild/RPMS/noarch/"

for package in "$HOME"/rpmbuild/RPMS/noarch/*.rpm; do
    echo "===== $(basename "$package") ====="
    rpm -qpl "$package"      # packaged files
    rpm -qpR "$package"      # declared and generated dependencies
    rpm -qp --scripts "$package" # RPM lifecycle scripts
 done
```

Confirm the collection RPM contains both `configuration` and `operation` collections, and that the production RPM contains `deploy/ansible/install-journeyman.yml`.

Optional linting:

```bash
dnf install -y rpmlint
rpmlint "$HOME/rpmbuild/SPECS/journeyman.spec" "$HOME"/rpmbuild/RPMS/noarch/*.rpm
```

**Known exception:** The production RPM intentionally installs under `/opt/journeyman`, matching Journeyman's existing deployment and service paths. `rpmlint` reports `dir-or-file-in-opt` for these files. The project has chosen to retain `/opt/journeyman` rather than restructure the application solely to satisfy that check. Review other warnings and errors independently; do not assume all lint output is harmless.

## 6. Installation and upgrades

Test the RPM on a disposable VM before deploying to a running Journeyman server. Installing the RPM stages the source files but **does not run the application installation playbook**:

```bash
dnf install ./journeyman-<version>-<release>.noarch.rpm
```

Then run the existing installer using your site's inventory and configuration, following the repository's `INSTALL.md`, `INSTALL.PostgreSQL.md`, and `UPGRADE.md`. Do not assume the example inventory name is valid for your installation.

**Upgrade caution:** The RPM owns application files under `/opt/journeyman`. Before an RPM upgrade, back up site-specific configuration and application data, check for local modifications to RPM-owned files, and verify that the Ansible installer will preserve them. RPM upgrades can replace RPM-owned files; `rpm -V journeyman` can be used to detect changes to packaged files. Do not run `dnf upgrade` against a production installation until this interaction has been tested.

## Rebuilding after source changes

After changing the source or spec, increment `Release:` for another build of the same application version, or update `VERSION` and `Version:` together for a new release. Regenerate the source tarball, copy the updated spec into `~/rpmbuild/SPECS/`, and rerun `rpmbuild -ba`.

## Troubleshooting

- **`File ... No such file or directory` in `%prep`:** Check that the archive filename matches `Name-Version.tar.gz` and its top-level directory is `journeyman-<version>`.
- **Collection missing in `%install`:** Verify both `ansible_collections/journeyman/*/galaxy.yml` files are in the source archive.
- **Inspecting an RPM prints nothing:** Check the actual version and release in `~/rpmbuild/RPMS/noarch/`; avoid reusing an older version in the `rpm -qp...` command.
- **Build fails on a different RHEL version:** Check the available build prerequisites and spec dependencies for that RHEL version; an EL9 build does not establish EL10 compatibility.

