# Journeyman source/bootstrap RPM. The Ansible installer performs deployment.
# Build source: tar -czf ~/rpmbuild/SOURCES/journeyman-2.0.0.tar.gz --transform='s,^journeyman-main,journeyman-2.0.0,' journeyman-main
# NOTE: Set Version to match VERSION in the source archive.
%global debug_package %{nil}
%global jmroot /opt/journeyman
%global collectionbase %{_datadir}/ansible/collections/ansible_collections/journeyman

Name:           journeyman
Version:        2.0.0
Release:        1%{?dist}
Summary:        Journeyman deployment source and Ansible installer
License:        Apache-2.0
URL:            https://github.com/coec/journeyman
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch
Requires:       ansible-core
Requires:       git
Requires:       python3
Requires:       python3-pip
Requires:       python3-virtualenv
Requires:       openssl

%description
Journeyman source, configuration examples and deployment playbooks. Installing
this RPM stages the application under /opt/journeyman; run the bundled Ansible
playbook to install or update the services, Python environment and database.

%package devel
Summary:        Journeyman tests and developer resources
Requires:       %{name} = %{version}-%{release}

%description devel
Tests and developer resources for Journeyman. Development dependencies are
managed separately and are not installed by this RPM.

%package collection
Summary:        Journeyman configuration and operation Ansible collections
Requires:       ansible-core

%description collection
The independently installable journeyman.configuration and
journeyman.operation Ansible collections.

%prep
%setup -q

%build
# Python dependencies are installed by deploy/ansible/install-journeyman.yml.

%install
install -d %{buildroot}%{jmroot} %{buildroot}%{_datadir}/journeyman-devel
install -d %{buildroot}%{collectionbase}
# Stage application source and installer, not runtime state or collection files.
for entry in app bin config contrib deploy docs migrations scripts static templates \
             VERSION LICENSE README.md INSTALL.md INSTALL.PostgreSQL.md \
             INSTALL.SQLite.md UPGRADE.md requirements.txt requirements.lock \
             requirements-postgresql.txt requirements-postgresql.lock \
             pyproject.toml; do
    if test -e "$entry"; then cp -a "$entry" %{buildroot}%{jmroot}/; fi
done
# Keep tests and developer helpers in the devel RPM.
for entry in tests pytest.ini tox.ini CONTRIBUTING.md; do
    if test -e "$entry"; then cp -a "$entry" %{buildroot}%{_datadir}/journeyman-devel/; fi
done
# Preserve the collection names actually used by the upstream repository.
for collection in configuration operation; do
    test -f "ansible_collections/journeyman/$collection/galaxy.yml"
    cp -a "ansible_collections/journeyman/$collection" %{buildroot}%{collectionbase}/
done

%files
%license LICENSE
%dir %{jmroot}
%{jmroot}/*

%files devel
%{_datadir}/journeyman-devel

%files collection
%{collectionbase}/configuration
%{collectionbase}/operation

%changelog
* Fri Oct 09 2026 Journeyman maintainers - 2.0.0-1
- Stage Journeyman source and installer
- Package existing configuration and operation collections

