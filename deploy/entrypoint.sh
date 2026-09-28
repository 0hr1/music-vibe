#!/bin/sh
# Starts the app as appuser. With LITESTREAM_REPLICA_URL set, first restores the database from
# the replica if this disk has none (a new volume), then runs the app under Litestream.
set -e

if [ "$(id -u)" = 0 ]; then
    # Fresh volumes (e.g. on Fly) are owned by root; hand the data folder to appuser.
    find "$DATA_DIR" ! -user appuser -exec chown appuser:appuser {} +
    exec setpriv --reuid=appuser --regid=appuser --init-groups "$0" "$@"
fi

if [ -n "$LITESTREAM_REPLICA_URL" ]; then
    litestream restore -config /etc/litestream.yml -if-db-not-exists -if-replica-exists "$DATA_DIR/music_vibe.db"
    exec litestream replicate -config /etc/litestream.yml -exec "$*"
fi
exec "$@"
