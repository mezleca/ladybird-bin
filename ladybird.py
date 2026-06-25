#!/usr/bin/env python3

import os
import re
import sys
import json
import shlex
import shutil
import tarfile
import argparse
import subprocess

from pathlib import Path

ROOT_DIR        = Path(__file__).resolve().parent
IS_CI           = os.environ.get("GITHUB_ACTIONS") == "true"
LADYBIRD_DIR    = ROOT_DIR / "ladybird"
BUILD_DIR       = LADYBIRD_DIR / "Build"
RELEASE_DIR     = BUILD_DIR / "release"
BUILD_CACHE_DIR = BUILD_DIR / "caches"
VCPKG_DIR       = BUILD_DIR / "vcpkg"
OUTPUT_DIR      = ROOT_DIR / "output"
INSTALL_DIR     = OUTPUT_DIR / "ladybird"
PATCHES_DIR     = ROOT_DIR / "patches"
RESOURCES_DIR   = ROOT_DIR / "resources"
APPIMAGE_TOOL   = ROOT_DIR / "appimagetool-x86_64.AppImage"
APPIMAGE_TOOL_URL = "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"

APP_ID           = "org.ladybird.Ladybird"
APP_ICON_NAME    = APP_ID
APP_DESKTOP_NAME = f"{APP_ID}.desktop"

# libs that must come from the host
SYSTEM_LIBS = re.compile(
    r"^(linux-vdso|ld-linux|libc\.so|libm\.so|libdl\.so|libpthread\.so"
    r"|librt\.so|libresolv\.so|libnss|libutil\.so|libgcc_s\.so"
    r"|libstdc\+\+\.so|libgomp\.so|libcrypto\.so|libssl\.so"
    r"|libfontconfig\.so|libcurl\.so)"
)

QT_PLUGIN_DIRS = [
    Path("/usr/lib/qt6/plugins"),
    Path("/usr/lib/x86_64-linux-gnu/qt6/plugins"),
]

QT_PLUGINS = [
    "platforms",
    "xcbglintegrations",
    "wayland-shell-integration",
    "wayland-decoration-client",
    "wayland-graphics-integration-client",
    "imageformats",
    "iconengines",
    "tls",
]

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
        result = subprocess.run(cmd, shell=True, env=env, capture_output=capture, text=capture)
        if check and result.returncode != 0:
            print(f"command failed with exit code {result.returncode}")
            sys.exit(result.returncode)
        return result.returncode, result.stdout

    subprocess.Popen(
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
        git_rev = json.load(f).get("builtin-baseline", "")

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    res, out = run(f"git -C {VCPKG_DIR} rev-parse HEAD", capture=True, check=False)
    current_rev = out.strip() if res == 0 else ""

    if not VCPKG_DIR.exists():
        run(f"git -C {BUILD_DIR} clone https://github.com/microsoft/vcpkg.git")

    needs_bootstrap = not (VCPKG_DIR / "vcpkg").exists()

    if git_rev and current_rev != git_rev:
        run(f"git -C {VCPKG_DIR} fetch origin")
        run(f"git -C {VCPKG_DIR} checkout {git_rev}")
        needs_bootstrap = True

    if needs_bootstrap:
        run(f"chmod +x {VCPKG_DIR}/bootstrap-vcpkg.sh")
        run(f"{VCPKG_DIR}/bootstrap-vcpkg.sh -disableMetrics", env={"VCPKG_ROOT": str(VCPKG_DIR)})

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

    extra_cmake_args = args.cmake_args or os.environ.get("LADYBIRD_CMAKE_ARGS", '--preset="Release"')
    ninja = shutil.which("ninja") or shutil.which("ninja-build")

    if not ninja:
        print("error: ninja not found. install ninja-build.")
        sys.exit(1)

    base_cmd = [
        "cmake", "-Wno-dev", "-G", "Ninja",
        "-B", "Build/release", "-S", ".",
        "-DBUILD_TESTING=OFF",
        "-DENABLE_CI_BASELINE_CPU=ON",
        '-DCMAKE_C_FLAGS="-O3 -march=x86-64-v2 -ffunction-sections -fdata-sections"',
        '-DCMAKE_CXX_FLAGS="-O3 -march=x86-64-v2 -ffunction-sections -fdata-sections"',
        '-DCMAKE_EXE_LINKER_FLAGS="-Wl,--gc-sections -Wl,-O1"',
        '-DCMAKE_SHARED_LINKER_FLAGS="-Wl,--gc-sections -Wl,-O1"',
    ]

    if IS_CI:
        base_cmd += [
            "-DCMAKE_C_COMPILER_LAUNCHER=sccache",
            "-DCMAKE_CXX_COMPILER_LAUNCHER=sccache",
        ]

    cmake_cmd = " ".join(base_cmd)
    if extra_cmake_args:
        cmake_cmd += " " + extra_cmake_args

    print(f"cmake_cmd: {cmake_cmd}")
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

    run(f"cmake --install {RELEASE_DIR}", env={"DESTDIR": str(INSTALL_DIR.absolute())})

    usr_local = INSTALL_DIR / "usr" / "local"
    if usr_local.exists():
        for item in usr_local.iterdir():
            shutil.move(str(item), str(INSTALL_DIR / item.name))
        shutil.rmtree(INSTALL_DIR / "usr")

def collect_deps(binary: Path, visited: set[str]) -> set[Path]:
    env = os.environ.copy()
    build_lib  = str(RELEASE_DIR / "lib")
    vcpkg_lib  = str(RELEASE_DIR / "vcpkg_installed" / "x64-linux-dynamic" / "lib")
    env["LD_LIBRARY_PATH"] = f"{build_lib}:{vcpkg_lib}:{env.get('LD_LIBRARY_PATH', '')}"

    deps: set[Path] = set()

    _, out = run(f"ldd {binary}", capture=True, check=False, env=env)

    for line in out.splitlines():
        parts = line.strip().split()

        if "=>" not in parts or len(parts) < 3:
            continue

        soname   = parts[0]
        resolved = parts[2]

        if resolved == "not" or not resolved.startswith("/"):
            continue

        if SYSTEM_LIBS.match(soname):
            continue

        if resolved in visited:
            continue

        visited.add(resolved)
        path = Path(resolved)
        deps.add(path)
        deps.update(collect_deps(path, visited))

    return deps

def copy_shared_libs():
    dest_lib = INSTALL_DIR / "lib"
    dest_lib.mkdir(parents=True, exist_ok=True)

    vcpkg_lib = RELEASE_DIR / "vcpkg_installed" / "x64-linux-dynamic" / "lib"
    build_lib  = RELEASE_DIR / "lib"

    scan_dirs = [INSTALL_DIR / "bin", INSTALL_DIR / "libexec", build_lib, vcpkg_lib]
    binaries: list[Path] = [
        p for d in scan_dirs if d.exists()
        for p in d.iterdir() if p.is_file() and not p.is_symlink()
    ]

    visited: set[str] = set()
    all_deps: set[Path] = set()

    for b in binaries:
        all_deps.update(collect_deps(b, visited))

    for dep in all_deps:
        dest = dest_lib / dep.name
        if not dest.exists():
            shutil.copy2(dep, dest)
            print(f"copied dep: {dep.name}")

    copy_qt6_plugins(INSTALL_DIR)

def copy_qt6_plugins(install_root: Path):
    plugins_dest = install_root / "plugins"

    for qt_plugins in QT_PLUGIN_DIRS:
        if not qt_plugins.exists():
            continue

        for name in QT_PLUGINS:
            src = qt_plugins / name
            if not src.exists():
                continue
            dest = plugins_dest / name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest)
            print(f"copied Qt plugin: {name}")

        break

def cleanup_staging():
    for pattern in ["*.a", "*.cmake"]:
        for f in INSTALL_DIR.rglob(pattern):
            f.unlink()

def create_launcher():
    launcher = INSTALL_DIR / "ladybird"
    shutil.copy2(RESOURCES_DIR / "launcher.sh", launcher)
    launcher.chmod(0o755)

def create_appimage(name: str | None = None):
    appdir      = OUTPUT_DIR / "AppDir"
    output_name = ensure_suffix(name or "Ladybird-x86_64.AppImage", ".AppImage")

    if not APPIMAGE_TOOL.exists():
        run(f"curl -L {shlex.quote(APPIMAGE_TOOL_URL)} -o {shlex.quote(str(APPIMAGE_TOOL))}")
        run(f"chmod +x {APPIMAGE_TOOL}")

    create_appdir(appdir)
    run(f"{APPIMAGE_TOOL} {appdir} {OUTPUT_DIR / output_name}", env={"ARCH": "x86_64", "APPIMAGE_EXTRACT_AND_RUN": "1"})

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

    plugins_src = INSTALL_DIR / "plugins"
    if plugins_src.exists():
        shutil.copytree(plugins_src, usr_dir / "plugins")

    share_src = INSTALL_DIR / "share"
    if share_src.exists():
        shutil.copytree(share_src, usr_dir / "share")
    else:
        (usr_dir / "share").mkdir()

    libexec_src = INSTALL_DIR / "libexec"
    if libexec_src.exists():
        shutil.copytree(libexec_src, usr_dir / "libexec")

def create_appdir_launcher(appdir: Path):
    apprun = appdir / "AppRun"
    shutil.copy2(RESOURCES_DIR / "apprun.sh", apprun)
    apprun.chmod(0o755)

def create_appdir_desktop_file(appdir: Path):
    applications_dir = appdir / "usr" / "share" / "applications"
    applications_dir.mkdir(parents=True, exist_ok=True)

    desktop_file = applications_dir / APP_DESKTOP_NAME
    content = desktop_file.read_text() if desktop_file.exists() else (
        "[Desktop Entry]\n"
        "Name=Ladybird\n"
        "Exec=Ladybird %u\n"
        f"Icon={APP_ID}\n"
        "Type=Application\n"
        "Categories=Network;WebBrowser;\n"
        "Terminal=false\n"
        "StartupNotify=true\n"
    )
    desktop_file.write_text(normalize_desktop_file(content))

    root_desktop = appdir / APP_DESKTOP_NAME

    if root_desktop.exists() or root_desktop.is_symlink():
        root_desktop.unlink()

    root_desktop.symlink_to(Path("usr") / "share" / "applications" / APP_DESKTOP_NAME)

def normalize_desktop_file(contents: str) -> str:
    replacements = {
        "Exec": "Ladybird --force-new-process %U",
        "Icon": APP_ICON_NAME,
    }

    seen_keys      = set()
    normalized     = []
    in_entry       = False

    for line in contents.splitlines():
        if line.startswith("[") and line.endswith("]"):
            in_entry = line == "[Desktop Entry]"
            normalized.append(line)
            continue

        key = line.split("=", 1)[0]
        if in_entry and key in replacements:
            normalized.append(f"{key}={replacements[key]}")
            seen_keys.add(key)
            continue

        normalized.append(line)

    insert_at = len(normalized)

    for i, line in enumerate(normalized[1:], start=1):
        if line.startswith("[") and line.endswith("]"):
            insert_at = i
            break

    missing = [f"{k}={v}" for k, v in replacements.items() if k not in seen_keys]
    normalized[insert_at:insert_at] = missing

    return "\n".join(normalized) + "\n"

def create_appdir_icon_links(appdir: Path):
    icon_path = find_app_icon(appdir)
    root_icon = appdir / f"{APP_ICON_NAME}{icon_path.suffix}"
    dir_icon  = appdir / ".DirIcon"

    for link in (root_icon, dir_icon):
        if link.exists() or link.is_symlink():
            link.unlink()

    root_icon.symlink_to(icon_path.relative_to(appdir))
    dir_icon.symlink_to(root_icon.name)

def find_app_icon(appdir: Path) -> Path:
    candidates = (
        appdir / "usr/share/icons/hicolor/scalable/apps" / f"{APP_ID}.svg",
        appdir / "usr/share/icons/hicolor/256x256/apps"  / f"{APP_ID}.png",
        appdir / "usr/share/icons/hicolor/128x128/apps"  / f"{APP_ID}.png",
    )

    for icon in candidates:
        if icon.exists():
            return icon

    fallback = appdir / "usr/share/icons/hicolor/256x256/apps" / f"{APP_ID}.png"
    fallback.parent.mkdir(parents=True, exist_ok=True)

    icon_src = LADYBIRD_DIR / "UI" / "Icons" / "ladybird.png"

    if icon_src.exists():
        shutil.copy2(icon_src, fallback)
    else:
        fallback.touch()

    return fallback

def create_appstream_compat_link(appdir: Path):
    metainfo_dir  = appdir / "usr/share/metainfo"
    metainfo_file = metainfo_dir / f"{APP_ID}.metainfo.xml"
    appdata_file  = metainfo_dir / f"{APP_ID}.appdata.xml"

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

def ensure_suffix(name: str, suffix: str) -> str:
    return name if name.endswith(suffix) else f"{name}{suffix}"

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
    build_parser.add_argument("--jobs", "-j", type=int)
    build_parser.add_argument("--clean", action="store_true")
    build_parser.add_argument("--cmake-args")

    # package
    pkg_parser = subparsers.add_parser("package", help="package ladybird")
    pkg_parser.add_argument("--type", "-t", default="appimage", choices=["appimage", "tarball"])
    pkg_parser.add_argument("--name", "-n")

    # all
    all_parser = subparsers.add_parser("all", help="setup + build + package")
    all_parser.add_argument("--jobs", "-j", type=int)
    all_parser.add_argument("--clean", action="store_true")
    all_parser.add_argument("--cmake-args")
    all_parser.add_argument("--type", "-t", default="appimage", choices=["appimage", "tarball"])
    all_parser.add_argument("--name", "-n")

    args = parser.parse_args()

    {
        "setup":   lambda: cmd_setup(),
        "build":   lambda: cmd_build(args),
        "package": lambda: cmd_package(args),
        "all":     lambda: cmd_all(args),
    }[args.command]()

if __name__ == "__main__":
    main()
