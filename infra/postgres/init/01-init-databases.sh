#!/bin/bash
# Creates a database and a user for each microservice.
# Runs only the first time PostgreSQL starts (empty volume).
set -e

create_db() {
  echo "init: creating database $1 for user $2"
  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres \
       -v db="$1" -v usr="$2" -v pwd="$3" <<'SQL'
CREATE ROLE :"usr" LOGIN PASSWORD :'pwd';
CREATE DATABASE :"db" OWNER :"usr";
REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
SQL
}

create_db "$USER_DB_NAME"         "$USER_DB_USER"         "$USER_DB_PASSWORD"
create_db "$PRODUCT_DB_NAME"      "$PRODUCT_DB_USER"      "$PRODUCT_DB_PASSWORD"
create_db "$INVENTORY_DB_NAME"    "$INVENTORY_DB_USER"    "$INVENTORY_DB_PASSWORD"
create_db "$ORDER_DB_NAME"        "$ORDER_DB_USER"        "$ORDER_DB_PASSWORD"
create_db "$PAYMENT_DB_NAME"      "$PAYMENT_DB_USER"      "$PAYMENT_DB_PASSWORD"
create_db "$NOTIFICATION_DB_NAME" "$NOTIFICATION_DB_USER" "$NOTIFICATION_DB_PASSWORD"

# Services must not connect to the default database either
psql -U "$POSTGRES_USER" -d postgres -c "REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;"
