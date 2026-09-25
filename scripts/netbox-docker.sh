#!/usr/bin/env bash

set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
compose_file="${repo_dir}/docker-compose.local.yml"

if ! command -v docker >/dev/null 2>&1; then
    printf '%s\n' 'Docker is required. Install Docker Desktop and try again.' >&2
    exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
    printf '%s\n' 'Docker Compose v2 is required (docker compose).' >&2
    exit 1
fi

compose() {
    docker compose --project-name netbox-custom-objects --file "${compose_file}" "$@"
}

demo_data_url=${DEMO_DATA_URL:-https://raw.githubusercontent.com/netbox-community/netbox-demo-data/master/sql/netbox-demo-v4.7.sql}

database_initialized() {
    compose exec --no-TTY postgres sh -c \
        'psql --tuples-only --no-align --username "$POSTGRES_USER" "$POSTGRES_DB" \
        --command "SELECT 1 FROM information_schema.tables WHERE table_schema = '\''public'\'' AND table_name = '\''django_migrations'\''"' \
        | grep -q '^1$'
}

load_demo_data() {
    if [[ "${LOAD_DEMO_DATA:-true}" != "true" ]]; then
        return
    fi

    if database_initialized; then
        printf '%s\n' 'Existing NetBox database detected; keeping its data.'
        return
    fi

    printf '%s\n' 'Loading NetBox v4.7 demo data into the new PostgreSQL database...'
    if ! compose exec --no-TTY postgres sh -c \
        'psql --tuples-only --no-align --username "$POSTGRES_USER" postgres \
        --command "SELECT 1 FROM pg_roles WHERE rolname = '\''postgres'\''"' | grep -q '^1$'; then
        compose exec --no-TTY postgres sh -c \
            'psql --username "$POSTGRES_USER" postgres --command "CREATE ROLE postgres SUPERUSER LOGIN"'
    fi
    curl --fail --silent --show-error --location "${demo_data_url}" | \
        compose exec --no-TTY postgres sh -c \
        'psql --set ON_ERROR_STOP=1 --username "$POSTGRES_USER" "$POSTGRES_DB"'
    printf '%s\n' 'Demo data loaded.'
}

usage() {
    printf '%s\n' \
        'Usage: scripts/netbox-docker.sh [up|down|logs|status|shell|destroy]' \
        '' \
        'Commands:' \
        '  up       Build the local plugin image and start NetBox (default)' \
        '  down     Stop the containers' \
        '  logs     Follow NetBox logs' \
        '  status   Show container status' \
        '  shell    Open a shell in the NetBox container' \
        '  destroy  Stop containers and delete local database/Redis volumes'
}

command=${1:-up}

case "${command}" in
    up)
        compose up --wait -d postgres redis
        load_demo_data
        compose up --build -d
        printf '%s\n' 'NetBox is available at http://localhost:'"${NETBOX_PORT:-8000}"
        printf '%s\n' 'Demo login: admin / admin'
        ;;
    down)
        compose down
        ;;
    logs)
        compose logs --follow netbox netbox-worker
        ;;
    status)
        compose ps
        ;;
    shell)
        compose exec netbox /bin/bash
        ;;
    destroy)
        compose down --volumes
        ;;
    -h|--help|help)
        usage
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac