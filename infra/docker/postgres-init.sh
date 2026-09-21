#!/bin/sh
#
# Apply the up migrations, once, when PostgreSQL first initialises its data
# directory.
#
# The migrations directory cannot be mounted straight into
# /docker-entrypoint-initdb.d: that runs every .sql it finds in filename order,
# which here means `001_catalog.down.sql` immediately after
# `001_catalog.up.sql`. The schema would be created and dropped, and the
# container would come up empty and say nothing.

set -eu

for migration in /migrations/*.up.sql; do
    echo "applying ${migration}"
    psql --quiet --set ON_ERROR_STOP=1 \
        --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" \
        --file "${migration}"
done
