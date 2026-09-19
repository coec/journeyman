# Journeyman administrative shell environment.
#
# Journeyman tools load /etc/journeyman/journeyman.yml themselves. The
# service account shell only activates the application virtual environment.

JOURNEYMAN_VENV=${JOURNEYMAN_VENV:-/opt/journeyman/venv}

if [ -r "${JOURNEYMAN_VENV}/bin/activate" ]; then
    . "${JOURNEYMAN_VENV}/bin/activate"
fi
