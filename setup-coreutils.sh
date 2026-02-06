#!/bin/bash
# Setup: Download and unpack GNU coreutils source
# Based on: https://klee-se.org/tutorials/testing-coreutils/
#
# Usage: setup-coreutils.sh [version] [destination-dir]
#   version          Coreutils version (default: 6.11)
#   destination-dir  Where to unpack (default: /workspace/coreutils)
#
# Example: setup-coreutils.sh 6.11 /workspace/coreutils

set -e

VERSION="${1:-6.11}"
DEST="${2:-/workspace/coreutils}"
GNU_MIRROR="${GNU_MIRROR:-https://ftp.gnu.org/gnu/coreutils}"
TARBALL="coreutils-${VERSION}.tar.gz"
URL="${GNU_MIRROR}/${TARBALL}"

echo "Setting up coreutils ${VERSION} in ${DEST}"

mkdir -p "$DEST"
cd "$DEST"

if [[ -d "coreutils-${VERSION}" ]]; then
    echo "coreutils-${VERSION} already exists in $DEST"
    echo "Source directory: $DEST/coreutils-${VERSION}"
    exit 0
fi

if [[ ! -f "$TARBALL" ]]; then
    echo "Downloading $URL ..."
    wget -q --show-progress "$URL" || {
        echo "Download failed. Trying alternate mirror..."
        wget -q --show-progress "https://mirrors.kernel.org/gnu/coreutils/${TARBALL}" || true
    }
fi

if [[ ! -f "$TARBALL" ]]; then
    echo "Error: Could not obtain $TARBALL"
    exit 1
fi

echo "Unpacking $TARBALL ..."
tar xzf "$TARBALL"

echo ""
echo "Setup complete. Source is in: $DEST/coreutils-${VERSION}"
echo ""
echo "Next steps:"
echo "  cd $DEST/coreutils-${VERSION}"
echo "  step1-build-gcov.sh    # Build with gcov for coverage"
echo "  step2-build-llvm.sh    # Build with WLLVM and extract bitcode for KLEE"
echo ""
echo "Note: Older coreutils versions may require patching on newer systems."
echo "See: https://klee-se.org/tutorials/testing-coreutils/"
