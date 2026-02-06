# Dockerfile for KLEE Coreutils Testing Environment
# Based on: https://klee-se.org/tutorials/testing-coreutils/

FROM ubuntu:22.04

# Avoid interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies (lcov for coverage merge/summary)
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    git \
    python3 \
    python3-pip \
    wget \
    curl \
    libcap-dev \
    libsqlite3-dev \
    libtcmalloc-minimal4 \
    libgoogle-perftools-dev \
    zlib1g-dev \
    libncurses5-dev \
    libboost-all-dev \
    bison \
    flex \
    libelf-dev \
    libdwarf-dev \
    lsb-release \
    software-properties-common \
    gnupg \
    lcov \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /workspace

# Install WLLVM (whole-program-llvm)
RUN pip3 install --upgrade pip && \
    pip3 install wllvm

# Set environment variables for WLLVM
ENV LLVM_COMPILER=clang
ENV PATH="/usr/local/bin:${PATH}"

# Install LLVM and Clang (version compatible with KLEE)
# KLEE typically works with LLVM 10-14, we'll use LLVM 14
# Using explicit package installation for LLVM 14
RUN wget -O - https://apt.llvm.org/llvm-snapshot.gpg.key | apt-key add - && \
    echo "deb http://apt.llvm.org/jammy/ llvm-toolchain-jammy-14 main" >> /etc/apt/sources.list.d/llvm.list && \
    apt-get update && \
    apt-get install -y \
        llvm-14 \
        clang-14 \
        clang++-14 \
        llvm-14-dev \
        llvm-14-tools \
    && rm -rf /var/lib/apt/lists/*

# Update alternatives to use LLVM 14
RUN update-alternatives --install /usr/bin/clang clang /usr/bin/clang-14 100 && \
    update-alternatives --install /usr/bin/clang++ clang++ /usr/bin/clang++-14 100 && \
    update-alternatives --install /usr/bin/llvm-config llvm-config /usr/bin/llvm-config-14 100 && \
    update-alternatives --install /usr/bin/opt opt /usr/bin/opt-14 100 && \
    update-alternatives --install /usr/bin/llvm-as llvm-as /usr/bin/llvm-as-14 100 && \
    update-alternatives --install /usr/bin/llvm-link llvm-link /usr/bin/llvm-link-14 100

# Install KLEE dependencies
RUN apt-get update && apt-get install -y \
    libboost-program-options-dev \
    libboost-filesystem-dev \
    libboost-system-dev \
    libboost-regex-dev \
    libboost-thread-dev \
    libboost-serialization-dev \
    libc6-dev \
    minisat \
    && rm -rf /var/lib/apt/lists/*

# Build STP solver (required by KLEE)
RUN git clone https://github.com/stp/stp.git /opt/stp && \
    cd /opt/stp && \
    git checkout tags/2.3.3 && \
    mkdir build && \
    cd build && \
    cmake .. && \
    make -j$(nproc) && \
    make install && \
    ldconfig

# Clone and build KLEE
RUN git clone https://github.com/klee/klee.git /opt/klee && \
    cd /opt/klee && \
    git checkout v3.1

# Build uclibc for KLEE
RUN git clone https://github.com/klee/klee-uclibc.git /opt/klee-uclibc && \
    cd /opt/klee-uclibc && \
    ./configure --make-llvm-lib && \
    make -j$(nproc)

# Install lit (required for KLEE tests), tabulate (for klee-stats), gcovr (coverage aggregate)
RUN pip3 install lit tabulate gcovr

# Build KLEE
RUN mkdir -p /opt/klee/build && \
    cd /opt/klee/build && \
    cmake \
        -DENABLE_SOLVER_STP=ON \
        -DENABLE_POSIX_RUNTIME=ON \
        -DENABLE_KLEE_UCLIBC=ON \
        -DENABLE_SYSTEM_TESTS=OFF \
        -DKLEE_UCLIBC_PATH=/opt/klee-uclibc \
        -DLLVM_CONFIG_BINARY=/usr/bin/llvm-config-14 \
        -DLLVMCC=/usr/bin/clang-14 \
        -DLLVMCXX=/usr/bin/clang++-14 \
        .. && \
    make -j$(nproc) && \
    make install

# Add KLEE to PATH
ENV PATH="/opt/klee/build/bin:${PATH}"

# Create directories for coreutils
RUN mkdir -p /workspace/coreutils

# Set up entrypoint script
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Copy setup scripts
COPY setup-coreutils.sh /usr/local/bin/setup-coreutils.sh
RUN chmod +x /usr/local/bin/setup-coreutils.sh
COPY step1-build-gcov.sh /usr/local/bin/step1-build-gcov.sh
RUN chmod +x /usr/local/bin/step1-build-gcov.sh
COPY step2-build-llvm.sh /usr/local/bin/step2-build-llvm.sh
RUN chmod +x /usr/local/bin/step2-build-llvm.sh

WORKDIR /workspace

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["/bin/bash"]
