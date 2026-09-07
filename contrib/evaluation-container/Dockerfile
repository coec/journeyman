# Journeyman evaluation container.
#
# This is deliberately not the production deployment model. Journeyman's
# supported operational design remains a native Linux/systemd installation.

FROM python:3.14-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/journeyman/bin:/opt/journeyman/venv/bin:${PATH} \
    JOURNEYMAN_DATABASE_URI=sqlite:////var/lib/journeyman/journeyman.db \
    JOURNEYMAN_REPOSITORY_ROOT=/var/lib/journeyman/repositories \
    JOURNEYMAN_RUNNER_ARTIFACT_ROOT=/var/lib/journeyman/runner-artifacts \
    JOURNEYMAN_LOG_ROOT=/var/log/journeyman \
    JOURNEYMAN_SESSION_SIGNING_KEY_FILE=/var/lib/journeyman/secrets/session-signing.key \
    JOURNEYMAN_FALLBACK_ADMIN_PASSWORD_HASH_FILE=/var/lib/journeyman/secrets/fallback-admin-password.hash \
    JOURNEYMAN_PUBLIC_FQDN=localhost \
    JOURNEYMAN_AUTHENTICATION_DISABLED=false \
    ANSIBLE_HOST_KEY_CHECKING=False \
    ANSIBLE_LIBSSH_HOST_KEY_CHECKING=False \
    ANSIBLE_LIBSSH_HOST_KEY_AUTO_ADD=False

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ansible-core \
        bash \
        ca-certificates \
        git \
        openssh-client \
        procps \
        tini \
        sudo \
        openssh-client \
        openssh-server \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash svc_journeyman \
    && printf '%s\n' \
       'svc_journeyman ALL=(ALL) NOPASSWD: ALL' \
       > /etc/sudoers.d/journeyman-demo \
    && chmod 0440 /etc/sudoers.d/journeyman-demo \
    && visudo -cf /etc/sudoers.d/journeyman-demo \
    && install -d -m 0700 \
         -o svc_journeyman \
         -g svc_journeyman \
         /home/svc_journeyman/.ssh \
    && runuser -u svc_journeyman -- \
         ssh-keygen \
           -q \
           -t ed25519 \
           -N "" \
           -f /home/svc_journeyman/.ssh/id_ed25519 \
    && cp \
         /home/svc_journeyman/.ssh/id_ed25519.pub \
         /home/svc_journeyman/.ssh/authorized_keys \
    && chown svc_journeyman:svc_journeyman \
         /home/svc_journeyman/.ssh/authorized_keys \
    && chmod 0600 \
         /home/svc_journeyman/.ssh/authorized_keys

WORKDIR /opt/journeyman

COPY requirements.lock /opt/journeyman/requirements.lock

RUN python -m venv /opt/journeyman/venv \
    && /opt/journeyman/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/journeyman/venv/bin/pip install --no-cache-dir -r /opt/journeyman/requirements.lock

COPY . /opt/journeyman

RUN chmod +x \
        /opt/journeyman/bin/journeyman-runner \
        /opt/journeyman/bin/journeyman-environment-builder \
        /opt/journeyman/contrib/evaluation-container/entrypoint.sh \
        /opt/journeyman/contrib/evaluation-container/seed-repository/scripts/*.sh \
    && mkdir -p \
        /var/lib/journeyman/repositories \
        /var/lib/journeyman/runner-artifacts \
        /var/lib/journeyman/secrets \
        /var/lib/journeyman/repositories/demo-repository \
        /var/log/journeyman

# The supplied Ansible collections are part of the application checkout.
ENV ANSIBLE_COLLECTIONS_PATH=/opt/journeyman/ansible_collections:/usr/share/ansible/collections

EXPOSE 5000

VOLUME ["/var/lib/journeyman"]

ENTRYPOINT ["/usr/bin/tini", "--", "/opt/journeyman/contrib/evaluation-container/entrypoint.sh"]
