#!/bin/bash
#
HERE="$(dirname "$(readlink -f "${0}")")"

export LD_LIBRARY_PATH="$HERE/usr/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export QT_PLUGIN_PATH="$HERE/usr/plugins"
export PATH="$HERE/usr/bin${PATH:+:$PATH}"
export XDG_DATA_DIRS="$HERE/usr/share${XDG_DATA_DIRS:+:$XDG_DATA_DIRS}"

fontconfig_cache_root="${MESA_SHADER_CACHE_DIR:-${XDG_CACHE_HOME:-${HOME:-/tmp}/.cache}/Ladybird}"
export XDG_CACHE_HOME="$fontconfig_cache_root"
export MESA_SHADER_CACHE_DIR="$fontconfig_cache_root"
mkdir -p "$fontconfig_cache_root"

export FONTCONFIG_FILE="$HERE/usr/share/Lagom/fonts/fontconfig.conf"
export FONTCONFIG_PATH="$HERE/usr/share/Lagom/fonts"

qt_platform_theme="${QT_QPA_PLATFORMTHEME:-}"
if [[ -z "$qt_platform_theme" || ! -r "$HERE/usr/plugins/platformthemes/libq${qt_platform_theme}.so" ]]; then
    export QT_QPA_PLATFORMTHEME=xdgdesktopportal
fi

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
