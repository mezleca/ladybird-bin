#!/usr/bin/env python3

import os
import sys
import argparse
import subprocess
import shutil
import json
import shlex
import tarfile

from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
LADYBIRD_DIR = ROOT_DIR / "ladybird"
BUILD_DIR = LADYBIRD_DIR / "Build"
RELEASE_DIR = BUILD_DIR / "release"
BUILD_CACHE_DIR = BUILD_DIR / "caches"
VCPKG_DIR = BUILD_DIR / "vcpkg"
OUTPUT_DIR = ROOT_DIR / "output"
INSTALL_DIR = OUTPUT_DIR / "ladybird"
PATCHES_DIR = ROOT_DIR / "patches"
APPIMAGE_TOOL = ROOT_DIR / "appimagetool-x86_64.AppImage"
APPIMAGE_TOOL_URL = "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
APP_ID = "org.ladybird.Ladybird"
APP_ICON_NAME = APP_ID
APP_DESKTOP_NAME = f"{APP_ID}.desktop"
SYSTEM_RUNTIME_LIB_PREFIXES = (
    "libcrypto.so",
    "libcurl.so",
    "libfontconfig.so",
    "libssl.so",
)
HOST_CA_BUNDLE_PATHS = (
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ca-certificates/extracted/tls-ca-bundle.pem",
    "/etc/ssl/cert.pem",
)
LAUNCHER_SCRIPT_TEMPLATE = """\
#!/bin/bash
{runtime_dir_init}
{library_path}
{path_export}
readonly HOST_CA_BUNDLE_PATHS=(
{ca_bundle_paths}
)

has_certificate_arg() {{
    for arg in "$@"; do
        case "$arg" in
            -C|--certificate|--certificate=*)
                return 0
                ;;
        esac
    done

    return 1
}}

find_host_ca_bundle() {{
    local cert_path
    local resolved_cert_path

    for cert_path in "${{HOST_CA_BUNDLE_PATHS[@]}}"; do
        resolved_cert_path="$(readlink -f "$cert_path" 2>/dev/null || true)"
        if [[ -z "$resolved_cert_path" ]]; then
            resolved_cert_path="$cert_path"
        fi

        if [[ -r "$resolved_cert_path" ]]; then
            printf '%s\\n' "$resolved_cert_path"
            return 0
        fi
    done

    return 1
}}

cert_args=()

if ! has_certificate_arg "$@"; then
    cert_path="$(find_host_ca_bundle || true)"
    if [[ -n "$cert_path" ]]; then
        cert_args=(--certificate "$cert_path")
    fi
fi

exec "{ladybird_path}" "${{cert_args[@]}}" "$@"
"""

def run(
    cmd: str,
    check: bool = True,
    env: dict[str, str] | None = None,
    capture: bool = False,
    wait: bool = True,
) -> tuple[int, str]:
    print(f"exec: {cmd}")

    if env:
        env = {**os.environ, **env}

    if wait:
        result = subprocess.run(
            cmd,
            shell=True,
            env=env,
            capture_output=capture,
            text=capture,
        )

        if check and result.returncode != 0:
            print(f"command failed with exit code {result.returncode}")
            sys.exit(result.returncode)

        return result.returncode, result.stdout

    _ = subprocess.Popen(
        cmd,
        shell=True,
        env=env,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=capture,
    )

    return 0, ""

def cmd_setup():
    clone_or_update()
    setup_vcpkg()

def clone_or_update():
    if not (LADYBIRD_DIR / ".git").exists():
        run(
            "git clone --branch master --single-branch --filter=blob:none --depth 1 "
            f"https://github.com/LadybirdBrowser/ladybird.git {shlex.quote(str(LADYBIRD_DIR))}"
        )
        return

    try:
        run(f"git -C {LADYBIRD_DIR} checkout master")
        run(f"git -C {LADYBIRD_DIR} pull --ff-only --depth 1 origin master")
    except SystemExit:
        print("failed to update master branch")
        sys.exit(1)

def setup_vcpkg():
    vcpkg_json = LADYBIRD_DIR / "vcpkg.json"

    with open(vcpkg_json) as f:
        data = json.load(f)
        git_rev = data.get("builtin-baseline", "")

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    current_rev = ""
    res, out = run(f"git -C {VCPKG_DIR} rev-parse HEAD", capture=True, check=False)

    if res == 0:
        current_rev = out.strip()

    if not VCPKG_DIR.exists():
        run(f"git -C {BUILD_DIR} clone https://github.com/microsoft/vcpkg.git")

    needs_bootstrap = not (VCPKG_DIR / "vcpkg").exists()

    if git_rev and current_rev != git_rev:
        run(f"git -C {VCPKG_DIR} fetch origin")
        run(f"git -C {VCPKG_DIR} checkout {git_rev}")
        needs_bootstrap = True

    if needs_bootstrap:
        run(f"chmod +x {VCPKG_DIR}/bootstrap-vcpkg.sh")
        run(
            f"{VCPKG_DIR}/bootstrap-vcpkg.sh -disableMetrics",
            env={"VCPKG_ROOT": str(VCPKG_DIR)},
        )

    os.environ["VCPKG_ROOT"] = str(VCPKG_DIR)
    os.environ.setdefault("XDG_CACHE_HOME", str(BUILD_CACHE_DIR))
    os.environ.setdefault("VCPKG_DEFAULT_BINARY_CACHE", str(BUILD_CACHE_DIR / "vcpkg-binary-cache"))

    if vcpkg_cache := os.environ.get("VCPKG_DEFAULT_BINARY_CACHE"):
        Path(vcpkg_cache).mkdir(parents=True, exist_ok=True)

def cmd_build(args):
    if not LADYBIRD_DIR.exists():
        print("error: ladybird directory not found. run 'setup' first.")
        sys.exit(1)

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    setup_vcpkg()

    if args.clean:
        clean_build()

    apply_patches()

    extra_cmake_args = args.cmake_args or os.environ.get("LADYBIRD_CMAKE_ARGS", "--preset Release")
    ninja = shutil.which("ninja") or shutil.which("ninja-build")

    if not ninja:
        print("error: ninja not found. install ninja-build.")
        sys.exit(1)

    cmake_cmd = (
        f"cmake -S . -B Build/release "
        f"-DCMAKE_MAKE_PROGRAM={ninja} "
        "-DENABLE_CI_BASELINE_CPU=ON " # ladybird_option(ENABLE_CI_BASELINE_CPU OFF CACHE BOOL "Use a baseline CPU target for improved ccache sharing")
    )

    if extra_cmake_args:
        cmake_cmd += " ".join(shlex.quote(arg) for arg in shlex.split(extra_cmake_args)) + " "

    run(f"cd {LADYBIRD_DIR} && {cmake_cmd}")

    jobs = args.jobs or os.cpu_count() or 4
    try:
        run(f"{ninja} -C {RELEASE_DIR} -j {jobs}")
    finally:
        revert_patches()

def apply_patches():
    if not PATCHES_DIR.exists():
        return

    for patch_file in sorted(PATCHES_DIR.glob("*.diff")):
        print(f"applying patch: {patch_file.name}")

        ret_check, _ = run(f"git -C {LADYBIRD_DIR} apply --check {patch_file}", check=False)

        if ret_check == 0:
            run(f"git -C {LADYBIRD_DIR} apply {patch_file}")
            continue

        ret_reverse, _ = run(f"git -C {LADYBIRD_DIR} apply --reverse --check {patch_file}", check=False)

        if ret_reverse == 0:
            print(f"patch {patch_file.name} already applied (skipping)")
        else:
            print(f"error: failed to apply patch {patch_file.name}")
            sys.exit(1)

def revert_patches():
    if not PATCHES_DIR.exists():
        return

    print("reverting patches...")
    for patch_file in sorted(PATCHES_DIR.glob("*.diff"), reverse=True):
        ret, _ = run(f"git -C {LADYBIRD_DIR} apply --reverse --check {patch_file}", check=False)
        if ret == 0:
            print(f"reverting patch: {patch_file.name}")
            run(f"git -C {LADYBIRD_DIR} apply --reverse {patch_file}")

def clean_build():
    print("cleaning build directory...")
    shutil.rmtree(RELEASE_DIR, ignore_errors=True)

def cmd_package(args):
    if not RELEASE_DIR.exists():
        print("error: build directory not found. run 'build' first.")
        sys.exit(1)

    install_to_staging()
    copy_shared_libs()
    cleanup_staging()
    create_launcher()

    if args.type == "appimage":
        create_appimage(args.name)
    elif args.type == "tarball":
        create_tarball(args.name)
    else:
        print(f"unknown package type: {args.type}")
        sys.exit(1)

def install_to_staging():
    shutil.rmtree(INSTALL_DIR, ignore_errors=True)
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)

    env = {"DESTDIR": str(INSTALL_DIR.absolute())}
    run(f"cmake --install {RELEASE_DIR}", env=env)

    usr_local = INSTALL_DIR / "usr" / "local"

    if usr_local.exists():
        for item in usr_local.iterdir():
            shutil.move(str(item), str(INSTALL_DIR / item.name))
        shutil.rmtree(INSTALL_DIR / "usr")

def copy_shared_libs():
    vcpkg_root = RELEASE_DIR / "vcpkg_installed"
    build_lib = RELEASE_DIR / "lib"
    dest_lib = INSTALL_DIR / "lib"
    dest_lib.mkdir(parents=True, exist_ok=True)

    lib_dirs = [build_lib]
    if vcpkg_root.exists():
        for item in vcpkg_root.iterdir():
            if item.is_dir() and (item / "lib").exists():
                lib_dirs.append(item / "lib")

    for lib_dir in lib_dirs:
        if lib_dir.exists():
            for so in lib_dir.glob("*.so*"):
                if should_use_system_runtime_lib(so.name):
                    print(f"skipped system runtime lib: {so.name}")
                    continue

                shutil.copy2(so, dest_lib)
                print(f"copied {so.name}")

def should_use_system_runtime_lib(lib_name: str) -> bool:
    return any(lib_name.startswith(prefix) for prefix in SYSTEM_RUNTIME_LIB_PREFIXES)

def cleanup_staging():
    for pattern in ["*.a", "*.cmake"]:
        for f in INSTALL_DIR.rglob(pattern):
            f.unlink()

def create_launcher():
    launcher = INSTALL_DIR / "ladybird"
    launcher.write_text(create_launcher_script(
        runtime_dir_init='SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"',
        library_path='export LD_LIBRARY_PATH="$SCRIPT_DIR/lib:$LD_LIBRARY_PATH"',
        path_export=None,
        ladybird_path="$SCRIPT_DIR/bin/Ladybird",
    ))
    launcher.chmod(0o755)

def create_launcher_script(
    runtime_dir_init: str,
    library_path: str,
    path_export: str | None,
    ladybird_path: str,
) -> str:
    ca_bundle_paths = "\n".join(f"    {shlex.quote(path)}" for path in HOST_CA_BUNDLE_PATHS)
    return LAUNCHER_SCRIPT_TEMPLATE.format(
        runtime_dir_init=runtime_dir_init,
        library_path=library_path,
        path_export=path_export or "",
        ca_bundle_paths=ca_bundle_paths,
        ladybird_path=ladybird_path,
    )

def create_appimage(name: str | None = None):
    appdir = OUTPUT_DIR / "AppDir"
    output_name = ensure_suffix(name or "Ladybird-x86_64.AppImage", ".AppImage")

    if not APPIMAGE_TOOL.exists():
        run(f"curl -L {shlex.quote(APPIMAGE_TOOL_URL)} -o {shlex.quote(str(APPIMAGE_TOOL))}")
        run(f"chmod +x {APPIMAGE_TOOL}")

    create_appdir(appdir)

    env = {
        "ARCH": "x86_64",
        "APPIMAGE_EXTRACT_AND_RUN": "1",
    }
    run(f"{APPIMAGE_TOOL} {appdir} {OUTPUT_DIR / output_name}", env=env)

def ensure_suffix(name: str, suffix: str) -> str:
    if name.endswith(suffix):
        return name

    return f"{name}{suffix}"

def create_appdir(appdir: Path):
    shutil.rmtree(appdir, ignore_errors=True)
    copy_appdir_payload(appdir)
    create_appdir_launcher(appdir)
    create_appdir_desktop_file(appdir)
    create_appdir_icon_links(appdir)
    create_appstream_compat_link(appdir)

def copy_appdir_payload(appdir: Path):
    usr_dir = appdir / "usr"
    usr_dir.mkdir(parents=True)

    shutil.copytree(INSTALL_DIR / "bin", usr_dir / "bin")
    shutil.copytree(INSTALL_DIR / "lib", usr_dir / "lib")

    if (INSTALL_DIR / "share").exists():
        shutil.copytree(INSTALL_DIR / "share", usr_dir / "share")
    else:
        (usr_dir / "share").mkdir()

    if (INSTALL_DIR / "libexec").exists():
        shutil.copytree(INSTALL_DIR / "libexec", usr_dir / "libexec")

def create_appdir_launcher(appdir: Path):
    apprun = appdir / "AppRun"
    apprun.write_text(create_launcher_script(
        runtime_dir_init='HERE="$(dirname "$(readlink -f "${0}")")"',
        library_path='export LD_LIBRARY_PATH="$HERE/usr/lib:$LD_LIBRARY_PATH"',
        path_export='export PATH="$HERE/usr/bin:$PATH"',
        ladybird_path="$HERE/usr/bin/Ladybird",
    ))
    apprun.chmod(0o755)

def create_appdir_desktop_file(appdir: Path):
    applications_dir = appdir / "usr" / "share" / "applications"
    applications_dir.mkdir(parents=True, exist_ok=True)

    desktop_file = applications_dir / APP_DESKTOP_NAME
    if desktop_file.exists():
        desktop_file.write_text(normalize_desktop_file(desktop_file.read_text()))
    else:
        desktop_file.write_text(normalize_desktop_file("""[Desktop Entry]
Name=Ladybird
Exec=Ladybird %u
Icon=org.ladybird.Ladybird
Type=Application
Categories=Network;WebBrowser;
Terminal=false
StartupNotify=true
"""))

    root_desktop = appdir / APP_DESKTOP_NAME
    if root_desktop.exists() or root_desktop.is_symlink():
        root_desktop.unlink()
    root_desktop.symlink_to(Path("usr") / "share" / "applications" / APP_DESKTOP_NAME)

def normalize_desktop_file(contents: str) -> str:
    replacements = {
        "Exec": "Ladybird --force-new-process %U",
        "Icon": APP_ICON_NAME,
    }
    seen_keys = set()
    normalized_lines = []
    in_desktop_entry = False

    for line in contents.splitlines():
        if line.startswith("[") and line.endswith("]"):
            in_desktop_entry = line == "[Desktop Entry]"
            normalized_lines.append(line)
            continue

        key = line.split("=", 1)[0]
        if in_desktop_entry and key in replacements:
            normalized_lines.append(f"{key}={replacements[key]}")
            seen_keys.add(key)
            continue

        normalized_lines.append(line)

    insert_at = len(normalized_lines)
    for index, line in enumerate(normalized_lines[1:], start=1):
        if line.startswith("[") and line.endswith("]"):
            insert_at = index
            break

    missing_lines = [
        f"{key}={value}"
        for key, value in replacements.items()
        if key not in seen_keys
    ]
    normalized_lines[insert_at:insert_at] = missing_lines

    return "\n".join(normalized_lines) + "\n"

def create_appdir_icon_links(appdir: Path):
    icon_path = find_app_icon(appdir)
    root_icon = appdir / f"{APP_ICON_NAME}{icon_path.suffix}"
    dir_icon = appdir / ".DirIcon"

    for link in (root_icon, dir_icon):
        if link.exists() or link.is_symlink():
            link.unlink()

    root_icon.symlink_to(icon_path.relative_to(appdir))
    dir_icon.symlink_to(root_icon.name)

def find_app_icon(appdir: Path) -> Path:
    icon_candidates = (
        appdir / "usr" / "share" / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg",
        appdir / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps" / f"{APP_ID}.png",
        appdir / "usr" / "share" / "icons" / "hicolor" / "128x128" / "apps" / f"{APP_ID}.png",
    )

    for icon_path in icon_candidates:
        if icon_path.exists():
            return icon_path

    fallback_icon = appdir / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps" / f"{APP_ID}.png"
    fallback_icon.parent.mkdir(parents=True, exist_ok=True)

    icon_src = LADYBIRD_DIR / "UI" / "Icons" / "ladybird.png"
    if icon_src.exists():
        shutil.copy2(icon_src, fallback_icon)
    else:
        fallback_icon.touch()

    return fallback_icon

def create_appstream_compat_link(appdir: Path):
    metainfo_dir = appdir / "usr" / "share" / "metainfo"
    metainfo_file = metainfo_dir / f"{APP_ID}.metainfo.xml"
    appdata_file = metainfo_dir / f"{APP_ID}.appdata.xml"

    if not metainfo_file.exists():
        return

    if appdata_file.exists() or appdata_file.is_symlink():
        return

    appdata_file.symlink_to(metainfo_file.name)

def create_tarball(name: str | None = None):
    output_name = ensure_suffix(name or "ladybird-x86_64", ".tar.gz")
    output_path = OUTPUT_DIR / output_name

    with tarfile.open(output_path, "w:gz") as tar:
        tar.add(INSTALL_DIR, arcname="ladybird")

    print(f"created tarball: {output_path}")

def cmd_all(args):
    cmd_setup()
    cmd_build(args)
    cmd_package(args)

def main():
    parser = argparse.ArgumentParser(description="ladybird build script")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # setup
    subparsers.add_parser("setup", help="clone/update ladybird source")

    # build
    build_parser = subparsers.add_parser("build", help="build ladybird")
    build_parser.add_argument("--jobs", "-j", type=int, help="parallel jobs")
    build_parser.add_argument("--clean", action="store_true", help="clean before build")
    build_parser.add_argument("--cmake-args", help="extra args passed to cmake configure")

    # package
    pkg_parser = subparsers.add_parser("package", help="package ladybird")
    pkg_parser.add_argument("--type", "-t", default="appimage", choices=["appimage", "tarball"], help="package type")
    pkg_parser.add_argument("--name", "-n", help="output filename")

    # all
    all_parser = subparsers.add_parser("all", help="setup + build + package")
    all_parser.add_argument("--jobs", "-j", type=int, help="parallel jobs")
    all_parser.add_argument("--clean", action="store_true", help="clean before build")
    all_parser.add_argument("--cmake-args", help="extra args passed to cmake configure")
    all_parser.add_argument("--type", "-t", default="appimage", choices=["appimage", "tarball"], help="package type")
    all_parser.add_argument("--name", "-n", help="output filename")

    args = parser.parse_args()

    if args.command == "setup":
        cmd_setup()
    elif args.command == "build":
        cmd_build(args)
    elif args.command == "package":
        cmd_package(args)
    elif args.command == "all":
        cmd_all(args)

if __name__ == "__main__":
    main()
