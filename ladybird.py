#!/usr/bin/env python3

import os
import sys
import argparse
import subprocess
import shutil
import json

from pathlib import Path
from typing import Optional

ROOT_DIR = Path.cwd()
LADYBIRD_DIR = ROOT_DIR / "ladybird"
BUILD_DIR = LADYBIRD_DIR / "Build"
RELEASE_DIR = BUILD_DIR / "release"
OUTPUT_DIR = ROOT_DIR / "output"
INSTALL_DIR = OUTPUT_DIR / "ladybird"
PATCHES_DIR = ROOT_DIR / "patches"

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
    # clone if it doesn't exist
    if not (LADYBIRD_DIR / ".git").exists():
        run("git clone https://github.com/LadybirdBrowser/ladybird.git")
        return

    # otherwise update master
    try:
        run(f"git -C {LADYBIRD_DIR} checkout master")
        run(f"git -C {LADYBIRD_DIR} pull origin master")
    except SystemExit:
        print("failed to update master branch")
        sys.exit(1)

    print("initializing submodules...")
    run(f"git -C {LADYBIRD_DIR} submodule update --init --recursive")

def setup_vcpkg():
    vcpkg_dir = BUILD_DIR / "vcpkg"
    vcpkg_json = LADYBIRD_DIR / "vcpkg.json"

    with open(vcpkg_json) as f:
        data = json.load(f)
        git_rev = data.get("builtin-baseline", "")

    # clone vcpkg if missing
    if not vcpkg_dir.exists():
        BUILD_DIR.mkdir(parents=True, exist_ok=True)
        run(f"git -C {BUILD_DIR} clone https://github.com/microsoft/vcpkg.git")

    # check current revision
    current_rev = ""
    res, out = run(f"git -C {vcpkg_dir} rev-parse HEAD", capture=True, check=False)

    if res == 0:
        current_rev = out.strip()

    # update if revision mismatch or if never bootstrapped
    needs_bootstrap = not (vcpkg_dir / "vcpkg").exists()

    if git_rev and current_rev != git_rev:
        run(f"git -C {vcpkg_dir} fetch origin")
        run(f"git -C {vcpkg_dir} checkout {git_rev}")
        needs_bootstrap = True

    if needs_bootstrap:
        run(f"chmod +x {vcpkg_dir}/bootstrap-vcpkg.sh")
        run(f"{vcpkg_dir}/bootstrap-vcpkg.sh -disableMetrics", env={"VCPKG_ROOT": str(vcpkg_dir)})

    os.environ["VCPKG_ROOT"] = str(vcpkg_dir)

    # create cache dir if env var is set
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

    cc = args.cc or os.environ.get("CC", "clang")
    cxx = args.cxx or os.environ.get("CXX", "clang++")

    print(f"using compiler: {cc} / {cxx}")

    if not shutil.which(cc) or not shutil.which(cxx):
        print(f"error: compiler not found ({cc}/{cxx})")
        sys.exit(1)

    ninja = shutil.which("ninja") or shutil.which("ninja-build")

    if not ninja:
        print("error: ninja not found. install ninja-build.")
        sys.exit(1)

    cmake_cmd = (
        f"cmake -S . -B Build/release "
        f"--preset {args.preset} "
        f"-DCMAKE_CXX_COMPILER={cxx} "
        f"-DCMAKE_C_COMPILER={cc} "
        f"-DCMAKE_MAKE_PROGRAM={ninja} "
        "-DENABLE_CI_BASELINE_CPU=ON " # ladybird_option(ENABLE_CI_BASELINE_CPU OFF CACHE BOOL "Use a baseline CPU target for improved ccache sharing")
    )

    # run cmake configuration
    run(f"cd {LADYBIRD_DIR} && {cmake_cmd}")

    # build target
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

        # check if patch applies cleanly
        ret_check, _ = run(f"git -C {LADYBIRD_DIR} apply --check {patch_file}", check=False)

        if ret_check == 0:
            run(f"git -C {LADYBIRD_DIR} apply {patch_file}")
            continue

        # if check failed, maybe its already applied?
        # so try reverse check
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
        # check if applied
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
    # clean staging dir
    shutil.rmtree(INSTALL_DIR, ignore_errors=True)
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)

    # install to staging
    env = {"DESTDIR": str(INSTALL_DIR.absolute())}
    run(f"cmake --install {RELEASE_DIR}", env=env)

    # move /usr/local content to root of staging
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

    # find vcpkg lib dirs
    lib_dirs = [build_lib]
    if vcpkg_root.exists():
        for item in vcpkg_root.iterdir():
            if item.is_dir() and (item / "lib").exists():
                lib_dirs.append(item / "lib")

    # copy all shared libs to lib dir
    for lib_dir in lib_dirs:
        if lib_dir.exists():
            for so in lib_dir.glob("*.so*"):
                shutil.copy2(so, dest_lib)
                print(f"copied {so.name}")

def cleanup_staging():
    # remove static libs and cmake files
    for pattern in ["*.a", "*.cmake"]:
        for f in INSTALL_DIR.rglob(pattern):
            f.unlink()

def create_launcher():
    launcher = INSTALL_DIR / "ladybird"
    launcher.write_text("""#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export LD_LIBRARY_PATH="$SCRIPT_DIR/lib:$LD_LIBRARY_PATH"
exec "$SCRIPT_DIR/bin/Ladybird" "$@"
""")
    launcher.chmod(0o755)

def create_appimage(name: Optional[str] = None):
    appdir = OUTPUT_DIR / "AppDir"
    appimage_tool = ROOT_DIR / "appimagetool-x86_64.AppImage"
    appimage_url = "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
    output_name = name or "Ladybird-x86_64.AppImage"

    # ensure right suffix
    if not output_name.endswith(".AppImage"):
        output_name += ".AppImage"

    # download appimagetool if missing
    if not appimage_tool.exists():
        run(f"wget -O {appimage_tool} {appimage_url}")
        run(f"chmod +x {appimage_tool}")

    # prepare AppDir structure
    shutil.rmtree(appdir, ignore_errors=True)
    (appdir / "usr").mkdir(parents=True)

    # copy content
    shutil.copytree(INSTALL_DIR / "bin", appdir / "usr" / "bin")
    shutil.copytree(INSTALL_DIR / "lib", appdir / "usr" / "lib")

    if (INSTALL_DIR / "share").exists():
        shutil.copytree(INSTALL_DIR / "share", appdir / "usr" / "share")
    else:
        (appdir / "usr" / "share").mkdir()

    if (INSTALL_DIR / "libexec").exists():
        shutil.copytree(INSTALL_DIR / "libexec", appdir / "usr" / "libexec")

    # create AppRun
    apprun = appdir / "AppRun"
    apprun.write_text("""#!/bin/bash
HERE="$(dirname "$(readlink -f "${0}")")"
export LD_LIBRARY_PATH="$HERE/usr/lib:$LD_LIBRARY_PATH"
export PATH="$HERE/usr/bin:$PATH"
exec "$HERE/usr/bin/Ladybird" "$@"
""")
    apprun.chmod(0o755)

    # create desktop file
    desktop = appdir / "Ladybird.desktop"
    desktop.write_text("""[Desktop Entry]
Name=Ladybird
Exec=Ladybird
Icon=ladybird
Type=Application
Categories=Network;WebBrowser;
Terminal=false
""")

    # copy icon
    icon_src = LADYBIRD_DIR / "UI" / "Icons" / "ladybird.png"

    if icon_src.exists():
        shutil.copy2(icon_src, appdir / "ladybird.png")
        shutil.copy2(icon_src, appdir / ".DirIcon")
    else:
        (appdir / "ladybird.png").touch()

    # generate appimage
    env = {"ARCH": "x86_64"}
    run(f"{appimage_tool} {appdir} {OUTPUT_DIR / output_name}", env=env)

def create_tarball(name: Optional[str] = None):
    output_name = name or "ladybird-x86_64"

    # ensure right suffix to avoid conflict with directory
    if not output_name.endswith(".tar.gz"):
        output_name += ".tar.gz"

    output_path = OUTPUT_DIR / output_name

    import tarfile
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
    build_parser.add_argument("--cc", help="C compiler")
    build_parser.add_argument("--cxx", help="C++ compiler")
    build_parser.add_argument("--jobs", "-j", type=int, help="parallel jobs")
    build_parser.add_argument("--preset", default="Release", help="cmake preset")
    build_parser.add_argument("--clean", action="store_true", help="clean before build")

    # package
    pkg_parser = subparsers.add_parser("package", help="package ladybird")
    pkg_parser.add_argument("--type", "-t", default="appimage", choices=["appimage", "tarball"], help="package type")
    pkg_parser.add_argument("--name", "-n", help="output filename")

    # all
    all_parser = subparsers.add_parser("all", help="setup + build + package")
    all_parser.add_argument("--cc", help="C compiler")
    all_parser.add_argument("--cxx", help="C++ compiler")
    all_parser.add_argument("--jobs", "-j", type=int, help="parallel jobs")
    all_parser.add_argument("--preset", default="Release", help="cmake preset")
    all_parser.add_argument("--clean", action="store_true", help="clean before build")
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
