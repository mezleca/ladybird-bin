## ladybird nightly builds (linux)
> [!NOTE]
> all nightly builds are built targeting x86_64-v2 for compatibility.

### installation
download the latest night build [here](https://github.com/mezleca/ladybird-bin/releases/latest)

### system dependencies
- openssl
- curl
- fontconfig
- fuse2 (appimage)

### how to build locally

install all of the dependencies listed [here](https://github.com/LadybirdBrowser/ladybird/blob/master/Documentation/BuildInstructionsLadybird.md)

```bash
# set cmake args (if not present "--preset Release" will be used)
export LADYBIRD_CMAKE_ARGS="--preset Release -DBUILD_TESTING=OFF -DCMAKE_C_COMPILER=usr/bin/clang -DCMAKE_CXX_COMPILER=/usr/bin/clang++"

./ladybird.py setup
./ladybird.py build

./ladybird.py package --type appimage
./ladybird.py package --type tarball
```
