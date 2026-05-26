## ladybird nightly builds (linux)
> [!NOTE]
> all nightly builds are built targeting x86_64-v2 for compatibility.

### installation
download the latest night build [here](https://github.com/mezleca/ladybird-bin/releases/latest)

> [!WARNING]
> appimage / tarball doenst include qt6 by default, so make sure to install either qt6-base-dev (ubuntu) or whatever the name is on arch based distros

### local build
```bash
export LADYBIRD_CMAKE_ARGS="--preset Release -DBUILD_TESTING=OFF -DCMAKE_C_COMPILER=usr/bin/clang -DCMAKE_CXX_COMPILER=/usr/bin/clang++"

./ladybird.py setup
./ladybird.py build

./ladybird.py package --type appimage
./ladybird.py package --type tarball
```
