#!/bin/bash
#
HERE="$(dirname "$(readlink -f "${0}")")"

export LD_LIBRARY_PATH="$HERE/usr/lib:$LD_LIBRARY_PATH"
export QT_PLUGIN_PATH="$HERE/usr/plugins:$QT_PLUGIN_PATH"
export PATH="$HERE/usr/bin:$PATH"

echo "[apprun] HERE: $HERE"
echo "[apprun] QT_PLUGIN_PATH: $QT_PLUGIN_PATH"

readonly HOST_CA_BUNDLE_PATHS=(
    '/etc/ssl/certs/ca-certificates.crt'
    '/etc/pki/tls/certs/ca-bundle.crt'
    '/etc/ca-certificates/extracted/tls-ca-bundle.pem'
    '/etc/ssl/cert.pem'
)

has_certificate_arg() {
    for arg in "$@"; do
        case "$arg" in
            -C|--certificate|--certificate=*)
                return 0
                ;;
        esac
    done
    return 1
}

find_host_ca_bundle() {
    local cert_path
    local resolved_cert_path

    for cert_path in "${HOST_CA_BUNDLE_PATHS[@]}"; do
        resolved_cert_path="$(readlink -f "$cert_path" 2>/dev/null || true)"
        if [[ -z "$resolved_cert_path" ]]; then
            resolved_cert_path="$cert_path"
        fi
        if [[ -r "$resolved_cert_path" ]]; then
            printf '%s\n' "$resolved_cert_path"
            return 0
        fi
    done
    return 1
}

cert_args=()

if ! has_certificate_arg "$@"; then
    cert_path="$(find_host_ca_bundle || true)"
    if [[ -n "$cert_path" ]]; then
        cert_args=(--certificate "$cert_path")
    fi
fi

exec "$HERE/usr/bin/Ladybird" "${cert_args[@]}" "$@"
