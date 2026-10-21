#!/bin/sh
# Unprivileged entrypoint: temp/pid dirs under /tmp (writable by any UID).
set -e
mkdir -p \
  /tmp/nginx_proxy \
  /tmp/nginx_client \
  /tmp/nginx_fastcgi \
  /tmp/nginx_uwsgi \
  /tmp/nginx_scgi

exec /docker-entrypoint.sh "$@"
