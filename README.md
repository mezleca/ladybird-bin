## ladybird nightly builds (linux)
> [!NOTE]
> all nightly builds are built targeting x86_64-v2 for compatibility.

### local build
```bash
export LADYBIRD_CMAKE_ARGS="--preset Release -DBUILD_TESTING=OFF -DCMAKE_C_COMPILER=usr/bin/clang -DCMAKE_CXX_COMPILER=/usr/bin/clang++"

./ladybird.py setup
./ladybird.py build

./ladybird.py package --type appimage
./ladybird.py package --type tarball
```
