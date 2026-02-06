#!/bin/bash
# Step 2: Build coreutils with LLVM (WLLVM) and extract bitcode
# Based on: https://klee-se.org/tutorials/testing-coreutils/
#
# Usage: Run from coreutils source directory, or pass path as first argument
#   step2-build-llvm.sh [coreutils-source-dir]
#
# Prerequisites: WLLVM installed, LLVM_COMPILER=clang set
# This produces .bc files that KLEE can interpret.

set -e

# Ensure WLLVM environment is set
export LLVM_COMPILER="${LLVM_COMPILER:-clang}"

COREUTILS_SRC="${1:-.}"
COREUTILS_SRC="$(cd "$COREUTILS_SRC" && pwd)"

if [[ ! -f "$COREUTILS_SRC/configure" ]]; then
    echo "Error: configure not found in $COREUTILS_SRC"
    echo "Please run from coreutils source directory or pass the path as argument."
    exit 1
fi

if ! command -v wllvm &>/dev/null; then
    echo "Error: wllvm not found. Install with: pip install wllvm"
    exit 1
fi

if ! command -v extract-bc &>/dev/null; then
    echo "Error: extract-bc not found. Install WLLVM with: pip install wllvm"
    exit 1
fi

echo "Building coreutils with WLLVM/LLVM in $COREUTILS_SRC"
cd "$COREUTILS_SRC"

# Create build directory
rm -rf obj-llvm
mkdir -p obj-llvm
cd obj-llvm

# Configure with WLLVM and KLEE-friendly flags
# -O1 -Xclang -disable-llvm-passes: similar to -O0 but allows KLEE optimizations
# -D__NO_STRING_INLINES -D_FORTIFY_SOURCE=0 -U__OPTIMIZE__: avoid __fprintf_chk etc.
CC=wllvm ../configure --disable-nls \
    CFLAGS="-g -O1 -Xclang -disable-llvm-passes -D__NO_STRING_INLINES -D_FORTIFY_SOURCE=0 -U__OPTIMIZE__"

# Build
make -j$(nproc)

# For older coreutils versions, arch and hostname need separate build
make -C src arch hostname 2>/dev/null || true

# Extract LLVM bitcode from executables
# WLLVM stores bitcode locations in object files; extract-bc links them into .bc files
echo ""
echo "Extracting LLVM bitcode..."
cd src
find . -executable -type f | xargs -I '{}' extract-bc '{}'

echo ""
echo "Build complete. Bitcode files are in: $COREUTILS_SRC/obj-llvm/src/"
echo "Example: klee --libc=uclibc --posix-runtime $COREUTILS_SRC/obj-llvm/src/echo.bc --version"
