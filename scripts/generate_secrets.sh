#!/usr/bin/env bash
# Génère des secrets forts pour le .env de production.
# Usage : ./scripts/generate_secrets.sh
# Copier chaque valeur dans le .env correspondant (ne jamais logger/committer
# le résultat de ce script).
set -euo pipefail

echo "SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
echo "POSTGRES_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
echo "REDIS_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
echo "DEVICE_API_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
echo "JOB_API_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
echo "KEYCLOAK_ADMIN_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
