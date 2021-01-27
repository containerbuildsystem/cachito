#!/bin/sh
set -eu

cachito_api=$1
shift

until curl --fail --silent --show-error "${cachito_api}/api/v1/status/short"; do
    echo "Cachito is unavailable - sleeping"
    sleep 3
done

echo "Cachito is up - proceeding"

exec "$@"
