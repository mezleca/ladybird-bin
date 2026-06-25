## ladybird nightly builds (linux)
> [!NOTE]
> all nightly builds are built targeting x86_64-v2 for compatibility.

### installation
download the latest night build [here](https://github.com/mezleca/ladybird-bin/releases/latest)

> [!WARNING]
> appimage / tarball does not include qt6, curl, openssl, or fontconfig by default to prevent weird errors.<br>
> install the matching runtime packages for your distro before running it.

### local build
```bash
export LADYBIRD_CMAKE_ARGS="--preset Release -DBUILD_TESTING=OFF -DCMAKE_C_COMPILER=usr/bin/clang -DCMAKE_CXX_COMPILER=/usr/bin/clang++"

./ladybird.py setup
./ladybird.py build

./ladybird.py package --type appimage
./ladybird.py package --type tarball
```
