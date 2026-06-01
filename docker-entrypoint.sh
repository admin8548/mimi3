#!/bin/sh
set -eu

mkdir -p /app/users /app/logs /app/data
chown -R app:app /app/users /app/logs /app/data

if [ ! -f /app/data/model_mapping.json ]; then
    cp /app/model_mapping.default.json /app/data/model_mapping.json
fi

chown app:app /app/data/model_mapping.json

exec python - "$@" <<'PY'
import os
import pwd
import sys

user = pwd.getpwnam("app")
os.setgid(user.pw_gid)
os.setuid(user.pw_uid)
os.execvp(sys.argv[1], sys.argv[1:])
PY
