# Journeyman administrative shell environment.
#
# Journeyman tools load /etc/journeyman/journeyman.yml themselves. The
# service account shell only activates the application virtual environment.

if [ -r /opt/journeyman/venv/bin/activate ]; then
    . /opt/journeyman/venv/bin/activate
fi
