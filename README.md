# KLEE Coreutils Testing Docker Environment

This Docker environment provides a complete setup for testing GNU Coreutils with KLEE, following the tutorial at [https://klee-se.org/tutorials/testing-coreutils/](https://klee-se.org/tutorials/testing-coreutils/).

## Prerequisites

- Docker
- Docker Compose (optional, but recommended)

## Quick Start - Step 1 (Build with gcov)

### Automated Approach (Recommended)

Simply run the automation script:

```bash
cd /mnt/raid/home/yangluning/klee-coreutils-docker
./run-step1.sh
```

This will:
1. Build the Docker image (30-60 minutes on first run)
2. Download coreutils 6.11
3. Build coreutils with gcov support
4. Verify the build

### Manual Approach

### 1. Build the Docker Image

```bash
docker-compose build
```

Or using Docker directly:

```bash
docker build -t klee-coreutils:latest .
```

### 2. Start the Container

```bash
docker-compose up -d
docker-compose exec klee-coreutils bash
```

Or using Docker directly:

```bash
docker run -it --rm \
  -v $(pwd)/coreutils:/workspace/coreutils \
  -v $(pwd)/results:/workspace/results \
  klee-coreutils:latest
```

### 3. Set Up Coreutils

Inside the container, run:

```bash
cd /workspace
bash setup-coreutils.sh [VERSION]
```

Where `VERSION` is the coreutils version (default: 6.11). For example:

```bash
bash setup-coreutils.sh 6.11
```

This will:
- Download and extract coreutils
- Build with gcov support (in `obj-gcov/`)
- Build with LLVM/WLLVM (in `obj-llvm/`)
- Extract bitcode files (`.bc`) for KLEE

### 4. Run KLEE

Example: Testing `echo` with symbolic arguments:

```bash
cd /workspace/coreutils-6.11/obj-llvm/src
klee --only-output-states-covering-new --optimize --libc=uclibc --posix-runtime ./echo.bc --sym-args 0 2 4
```

### 5. Replay Test Cases

To replay the generated test cases on the native binary:

```bash
cd /workspace/coreutils-6.11/obj-gcov/src
klee-replay ./echo ../obj-llvm/src/klee-last/*.ktest
```

### 6. Check Coverage

```bash
cd /workspace/coreutils-6.11/obj-gcov/src
rm -f *.gcda  # Clean old coverage data
klee-replay ./echo ../obj-llvm/src/klee-last/*.ktest
gcov echo
```

## Directory Structure

```
.
├── Dockerfile              # Main Docker image definition
├── docker-compose.yml      # Docker Compose configuration
├── entrypoint.sh          # Container entrypoint script
├── setup-coreutils.sh     # Script to build coreutils
├── README.md              # This file
├── coreutils/             # Mounted volume for coreutils source (created automatically)
└── results/               # Mounted volume for test results (created automatically)
```

## What's Included

- **KLEE** (v3.1) with:
  - uclibc support
  - POSIX runtime support
  - STP solver support

- **LLVM 14** and Clang

- **WLLVM** (whole-program-llvm) for building LLVM bitcode

- **Build tools**: gcc, gcov, make, cmake, etc.

## Example Workflow

```bash
# 1. Build and start container
docker-compose up -d

# 2. Enter container
docker-compose exec klee-coreutils bash

# 3. Set up coreutils
cd /workspace
bash setup-coreutils.sh 6.11

# 4. Run KLEE on echo
cd coreutils-6.11/obj-llvm/src
klee --only-output-states-covering-new --optimize \
  --libc=uclibc --posix-runtime \
  ./echo.bc --sym-args 0 2 4

# 5. Check results
ls -la klee-last/
ktest-tool klee-last/test000001.ktest

# 6. Replay on native binary
cd ../../obj-gcov/src
klee-replay ./echo ../../obj-llvm/src/klee-last/*.ktest

# 7. View coverage
gcov echo
cat echo.c.gcov
```

## Troubleshooting

### KLEE build fails
- Ensure you have enough memory (KLEE build requires several GB)
- Try building with fewer parallel jobs: `make -j2` instead of `make -j$(nproc)`

### Coreutils download fails
- The script tries multiple mirrors, but if all fail, manually download and extract coreutils
- Place it in the `coreutils/` directory

### Bitcode extraction fails
- Some binaries may not extract properly - this is normal for some coreutils programs
- Try running `extract-bc` manually on specific binaries

### Permission issues
- If you encounter permission issues with mounted volumes, adjust file permissions:
  ```bash
  sudo chown -R $USER:$USER coreutils/ results/
  ```

## References

- [KLEE Coreutils Tutorial](https://klee-se.org/tutorials/testing-coreutils/)
- [KLEE Documentation](https://klee.github.io/)
- [WLLVM Documentation](https://github.com/travitch/whole-program-llvm)

## License

This Docker setup is provided as-is for educational and research purposes.
