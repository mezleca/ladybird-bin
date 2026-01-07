#!/usr/bin/env python3

import os
import sys
import argparse
import subprocess
import shutil
import json
import re
from pathlib import Path
from typing import Optional

ROOT_DIR = Path.cwd()
LADYBIRD_DIR = ROOT_DIR / "ladybird"
BUILD_DIR = LADYBIRD_DIR / "Build"
RELEASE_DIR = BUILD_DIR / "release"
OUTPUT_DIR = ROOT_DIR / "output"
INSTALL_DIR = OUTPUT_DIR / "ladybird"

def run(cmd: list[str], cwd: Optional[Path] = None, env: Optional[dict] = None) -> subprocess.CompletedProcess:
    merged_env = {**os.environ, **(env or {})}
    print(f"[run] {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=cwd, env=merged_env, check=True)

def run_capture(cmd: list[str], cwd: Optional[Path] = None) -> str:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout.strip()

class Setup:
    @staticmethod
    def clone_or_update():        
        if not (LADYBIRD_DIR / ".git").exists():
            shutil.rmtree(LADYBIRD_DIR, ignore_errors=True)
            run(["git", "clone", "https://github.com/LadybirdBrowser/ladybird.git"])
        else:
            try:
                run(["git", "checkout", "master"], cwd=LADYBIRD_DIR)
                run(["git", "pull", "origin", "master"], cwd=LADYBIRD_DIR)
            except subprocess.CalledProcessError:
                print("failed to update master branch")
                sys.exit(1)
                
        
        print("initializing submodules...")
        run(["git", "submodule", "update", "--init", "--recursive"], cwd=LADYBIRD_DIR)

    @staticmethod
    def setup_vcpkg():
        vcpkg_dir = BUILD_DIR / "vcpkg"
        vcpkg_json = LADYBIRD_DIR / "vcpkg.json"
        
        with open(vcpkg_json) as f:
            data = json.load(f)
            git_rev = data.get("builtin-baseline", "")
        
        if not vcpkg_dir.exists():
            BUILD_DIR.mkdir(parents=True, exist_ok=True)
            run(["git", "clone", "https://github.com/microsoft/vcpkg.git"], cwd=BUILD_DIR)
        
        try:
            current_rev = run_capture(["git", "rev-parse", "--short", "HEAD"], cwd=vcpkg_dir)
        except subprocess.CalledProcessError:
            current_rev = ""
        
        if git_rev and current_rev != git_rev[:len(current_rev)]:
            run(["git", "fetch", "origin"], cwd=vcpkg_dir)
            run(["git", "checkout", git_rev], cwd=vcpkg_dir)
            run(["./bootstrap-vcpkg.sh", "-disableMetrics"], cwd=vcpkg_dir)
        elif not (vcpkg_dir / "vcpkg").exists():
            run(["./bootstrap-vcpkg.sh", "-disableMetrics"], cwd=vcpkg_dir)
        
        os.environ["VCPKG_ROOT"] = str(vcpkg_dir)
        
        # ensure binary cache directory exists if set
        vcpkg_cache = os.environ.get("VCPKG_DEFAULT_BINARY_CACHE")
        if vcpkg_cache:
            Path(vcpkg_cache).mkdir(parents=True, exist_ok=True)
        
        triplet_file = vcpkg_dir / "triplets" / "x64-linux-compat.cmake"
        triplet_file.write_text("""set(VCPKG_TARGET_ARCHITECTURE x64)
set(VCPKG_CRT_LINKAGE dynamic)
set(VCPKG_LIBRARY_LINKAGE dynamic)
set(VCPKG_CMAKE_SYSTEM_NAME Linux)
set(VCPKG_CXX_FLAGS "-march=x86-64-v2")
set(VCPKG_C_FLAGS "-march=x86-64-v2")
""")

    @staticmethod
    def patch_cmake():
        cmake_file = LADYBIRD_DIR / "Meta" / "CMake" / "common_compile_options.cmake"
        if not cmake_file.exists():
            return
        
        print("patching cmake options...")
        content = cmake_file.read_text()
        content = re.sub(r"-march=x86-64-v3", "-march=x86-64-v2", content)
        content = re.sub(r"-march=native", "-march=x86-64-v2", content)
        cmake_file.write_text(content)

class Build:
    @staticmethod
    def clean():
        print("cleaning build directory...")
        shutil.rmtree(BUILD_DIR, ignore_errors=True)

    @staticmethod
    def run_build():
        cc = os.environ.get("CC", "clang")
        cxx = os.environ.get("CXX", "clang++")
        
        print(f"using compiler: {cc} / {cxx}")
        
        for compiler in [cc, cxx]:
            if not shutil.which(compiler):
                print(f"compiler not found: {compiler}")
                sys.exit(1)
        
        ninja = shutil.which("ninja") or shutil.which("ninja-build")
        if not ninja:
            print("error: ninja not found. install ninja-build.")
            sys.exit(1)
        
        run([
            "cmake", "--preset", "Release",
            f"-DCMAKE_CXX_COMPILER={cxx}",
            f"-DCMAKE_C_COMPILER={cc}",
            f"-DCMAKE_MAKE_PROGRAM={ninja}",
            "-DVCPKG_TARGET_TRIPLET=x64-linux-compat",
            "-DCMAKE_CXX_FLAGS=-march=x86-64-v2",
            "-DCMAKE_C_FLAGS=-march=x86-64-v2",
        ], cwd=LADYBIRD_DIR)
        
        nproc = os.cpu_count() or 4
        run([ninja, "-C", str(RELEASE_DIR), "-j", str(nproc)])

class Package:
    @staticmethod
    def install_to_staging():
        shutil.rmtree(INSTALL_DIR, ignore_errors=True)
        INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        
        env = {"DESTDIR": str(INSTALL_DIR.absolute())}
        run(["cmake", "--install", str(RELEASE_DIR)], env=env)
        
        usr_local = INSTALL_DIR / "usr" / "local"
        if usr_local.exists():
            for item in usr_local.iterdir():
                shutil.move(str(item), str(INSTALL_DIR / item.name))
            shutil.rmtree(INSTALL_DIR / "usr")

    @staticmethod
    def copy_shared_libs():
        vcpkg_lib = RELEASE_DIR / "vcpkg_installed" / "x64-linux-compat" / "lib"
        build_lib = RELEASE_DIR / "lib"
        dest_lib = INSTALL_DIR / "lib"
        dest_lib.mkdir(parents=True, exist_ok=True)
        
        for lib_dir in [vcpkg_lib, build_lib]:
            if lib_dir.exists():
                for so in lib_dir.glob("*.so*"):
                    shutil.copy2(so, dest_lib)
                    print(f"copied {so.name}")

    @staticmethod
    def cleanup():
        for pattern in ["*.a", "*.cmake"]:
            for f in INSTALL_DIR.rglob(pattern):
                f.unlink()

    @staticmethod
    def create_launcher():
        launcher = INSTALL_DIR / "ladybird"
        launcher.write_text("""#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export LD_LIBRARY_PATH="$SCRIPT_DIR/lib:$LD_LIBRARY_PATH"
exec "$SCRIPT_DIR/bin/Ladybird" "$@"
""")
        launcher.chmod(0o755)

    @staticmethod
    def create_appimage(appimage_name: Optional[str] = None):
        appdir = OUTPUT_DIR / "AppDir"
        appimage_tool = ROOT_DIR / "appimagetool-x86_64.AppImage"
        appimage_url = "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
        output_name = appimage_name or "Ladybird-x86_64.AppImage"
        
        if not appimage_tool.exists():
            run(["wget", "-O", str(appimage_tool), appimage_url])
            appimage_tool.chmod(0o755)
        
        shutil.rmtree(appdir, ignore_errors=True)
        (appdir / "usr").mkdir(parents=True)
        
        shutil.copytree(INSTALL_DIR / "bin", appdir / "usr" / "bin")
        shutil.copytree(INSTALL_DIR / "lib", appdir / "usr" / "lib")
        
        if (INSTALL_DIR / "share").exists():
            shutil.copytree(INSTALL_DIR / "share", appdir / "usr" / "share")
        else:
            (appdir / "usr" / "share").mkdir()
        
        if (INSTALL_DIR / "libexec").exists():
            shutil.copytree(INSTALL_DIR / "libexec", appdir / "usr" / "libexec")
        
        apprun = appdir / "AppRun"
        apprun.write_text("""#!/bin/bash
HERE="$(dirname "$(readlink -f "${0}")")"
export LD_LIBRARY_PATH="$HERE/usr/lib:$LD_LIBRARY_PATH"
export PATH="$HERE/usr/bin:$PATH"
exec "$HERE/usr/bin/Ladybird" "$@"
""")
        apprun.chmod(0o755)
        
        desktop = appdir / "Ladybird.desktop"
        desktop.write_text("""[Desktop Entry]
Name=Ladybird
Exec=Ladybird
Icon=ladybird
Type=Application
Categories=Network;WebBrowser;
Terminal=false
""")
        
        icon_src = LADYBIRD_DIR / "UI" / "Icons" / "ladybird.png"
        if icon_src.exists():
            shutil.copy2(icon_src, appdir / "ladybird.png")
            shutil.copy2(icon_src, appdir / ".DirIcon")
        else:
            (appdir / "ladybird.png").touch()
        
        env = {"ARCH": "x86_64"}
        run([str(appimage_tool), str(appdir), str(OUTPUT_DIR / output_name)], env=env)

def main():
    parser = argparse.ArgumentParser(description="Ladybird build script")
    parser.add_argument("--setup", action="store_true", help="clone/update ladybird source")
    parser.add_argument("--build", action="store_true", help="build ladybird")
    parser.add_argument("--package", action="store_true", help="package ladybird (staging + appimage)")
    parser.add_argument("--clean-build", action="store_true", help="clean build directory before building")
    parser.add_argument("--no-build", action="store_true", help="skip build after setup")
    parser.add_argument("--appimage-name", type=str, help="custom appimage output name")
    
    args = parser.parse_args()
    
    if not any([args.setup, args.build, args.package, args.clean_build]):
        parser.print_help()
        print("\nno action specified. use --setup, --build, --package, or --clean-build.")
        return
    
    if args.setup:
        Setup.clone_or_update()
        if args.no_build:
            print("setup complete, skipping build as requested.")
            return
    
    if args.clean_build:
        Build.clean()
    
    if args.build:
        if not LADYBIRD_DIR.exists():
            print("error: ladybird directory not found. run with --setup first.")
            sys.exit(1)
        
        BUILD_DIR.mkdir(parents=True, exist_ok=True)
        Setup.patch_cmake()
        Setup.setup_vcpkg()
        Build.run_build()
    
    if args.package or args.build:
        Package.install_to_staging()
        Package.copy_shared_libs()
        Package.cleanup()
        Package.create_launcher()
        Package.create_appimage(args.appimage_name)

if __name__ == "__main__":
    main()
