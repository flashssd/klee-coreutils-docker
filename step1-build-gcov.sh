#!/bin/bash
# Step 1: Build coreutils with gcov
# Based on: https://klee-se.org/tutorials/testing-coreutils/
#
# Usage: Run from coreutils source directory, or pass path as first argument
#   step1-build-gcov.sh [coreutils-source-dir]
#
# This builds a gcov-instrumented version for coverage analysis when replaying
# KLEE-generated test cases.

set -e

COREUTILS_SRC="${1:-.}"
COREUTILS_SRC="$(cd "$COREUTILS_SRC" && pwd)"

if [[ ! -f "$COREUTILS_SRC/configure" ]]; then
    echo "Error: configure not found in $COREUTILS_SRC"
    echo "Please run from coreutils source directory or pass the path as argument."
    exit 1
fi

echo "Building coreutils with gcov support in $COREUTILS_SRC"
cd "$COREUTILS_SRC"

# Create build directory
rm -rf obj-gcov
mkdir -p obj-gcov
cd obj-gcov

# Configure with gcov flags
# --disable-nls reduces extra C library initialization we're not interested in testing
../configure --disable-nls CFLAGS="-g -fprofile-arcs -ftest-coverage"

# Build
make -j$(nproc)

# For older coreutils versions, arch and hostname need separate build
# (Can be skipped for recent versions)
make -C src arch hostname 2>/dev/null || true

echo ""
echo "Build complete. Executables are in: $COREUTILS_SRC/obj-gcov/src/"
echo "Example: $COREUTILS_SRC/obj-gcov/src/echo --version"
