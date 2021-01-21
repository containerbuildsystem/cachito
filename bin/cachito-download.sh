#!/bin/bash
set -euo pipefail

usage () {
    echo "Usage: $(basename "$0") <request_url> <output_dir>"
}

description () {
    cat << EOF
Download a Cachito request, inject its configuration files and cachito.env

$(usage)

Example:
    $(basename "$0") localhost:8080/api/v1/requests/1 /tmp/cachito-test
EOF
}

if [ $# -eq 0 ] || [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
    description
    exit 0
fi

if [ $# -ne 2 ]; then
    usage >&2
    exit 1
fi

request_url=$1
output_dir=$2

error () {
    echo "$1" >&2
    return 1
}

check_dependencies () {
    command -v jq >/dev/null || error "Missing dependency: jq"
}

prepare_output_dir () {
    local output_dir=$1

    if [ -e "$output_dir" ]; then
        if [ ! -d "$output_dir" ]; then
            error "Cannot download to $output_dir: not a directory"
        fi

        if [ -n "$(ls -A "$output_dir")" ]; then
            error "Cannot download to $output_dir: already exists and is not empty"
        fi

        echo "Using existing output directory $output_dir"
    else
        echo "Using new output directory $output_dir"
        mkdir -p "$output_dir"
    fi
}

download_and_extract () {
    local request_url=$1
    local output_dir=$2

    echo "Downloading archive"
    # -f: fail on HTTP error code, -s: silent, -S: show error even when silent
    curl -fsS "$request_url/download" > "$output_dir/remote-source.tar.gz"

    echo "Extracting downloaded archive to remote-source/"
    mkdir "$output_dir/remote-source"
    tar -xf "$output_dir/remote-source.tar.gz" -C "$output_dir/remote-source"
}

inject_config_files () {
    local request_url=$1
    local output_dir=$2

    local config_json="$output_dir/configuration-files.json"

    echo "Getting configuration files"
    curl -fsS "$request_url/configuration-files" > "$config_json"

    echo "Injecting configuration files to remote-source/"
    # According to shellcheck, this is the proper way to save lines in an array
    mapfile -t paths < <(jq '.[].path' -r < "$config_json")

    for path in "${paths[@]}"; do
        # Show the path indented by 4 spaces
        echo "    $path"

        jq '.[] | select(.path == "'"$path"'") | .content' -r < "$config_json" |
            base64 --decode > "$output_dir/remote-source/$path"
    done
}

generate_cachito_env () {
    local request_url=$1
    local output_dir=$2

    local env_json="$output_dir/environment-variables.json"
    local cachito_env="$output_dir/remote-source/cachito.env"

    echo "Getting environment variables"
    curl -fsS "$request_url/environment-variables" > "$env_json"

    echo "Injecting cachito.env to remote-source/"
    echo "#/bin/bash" > "$cachito_env"

    jq 'to_entries[] | "\(.value.kind) \(.key) \(.value.value)"' -r < "$env_json" |
        while read -r kind key value; do
            if [ "$kind" = "path" ]; then
                value="$(realpath "$output_dir")/remote-source/$value"
            fi
            printf "export %s=%q\n" "$key" "$value"
        done >> "$cachito_env"

    # Show the generated env file indented by 4 spaces
    sed 's/^/    /' "$cachito_env"
}

check_dependencies
prepare_output_dir "$output_dir"
download_and_extract "$request_url" "$output_dir"
inject_config_files "$request_url" "$output_dir"
generate_cachito_env "$request_url" "$output_dir"
