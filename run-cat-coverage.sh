#!/bin/bash

# Use WORKSPACE_ROOT for both host and Docker: set by main.py to project dir, or default /workspace in Docker
WORKSPACE_ROOT="${WORKSPACE_ROOT:-/workspace}"

OBJ_GCOV_DIR="${WORKSPACE_ROOT}/coreutils/coreutils-8.32/obj-gcov/src"
cd "${OBJ_GCOV_DIR}" || { echo "Error: cannot cd to ${OBJ_GCOV_DIR}"; exit 1; }

if [ ! -f "./cat" ]; then
    echo "Error: cat binary not found in $(pwd)"
    echo "Build with coverage: cd coreutils/coreutils-8.32 && CFLAGS='--coverage' ./configure && make"
    exit 1
fi

# Use RESULTS_DIR from env (e.g. results/openrouter-result) or default to klee-result
RESULTS_DIR="${RESULTS_DIR:-${WORKSPACE_ROOT}/results/klee-result}"
mkdir -p "${RESULTS_DIR}"
chmod 777 "${RESULTS_DIR}" 2>/dev/null || true
echo "Results will be saved to: ${RESULTS_DIR}/"
echo ""

KTEST_ARGS_FILE="${WORKSPACE_ROOT}/coreutils/coreutils-8.32/obj-llvm/src/klee-last/ktest-arguments.txt"

if [ ! -f "${KTEST_ARGS_FILE}" ]; then
    KTEST_ARGS_FILE="${WORKSPACE_ROOT}/ktest-arguments.txt"
fi

if [ ! -f "${KTEST_ARGS_FILE}" ]; then
    echo "Error: ktest-arguments.txt not found"
    exit 1
fi

test_num=1
while IFS= read -r arg || [ -n "$arg" ]; do
    if [ -z "$arg" ]; then
        arg=""
    fi
    
    echo "Processing test case ${test_num} with argument: '${arg}'"
    
    rm -f cat.gcda cat.c.gcov
    
    if [ -z "$arg" ]; then
        echo "test input" | timeout 2 ./cat > /dev/null 2>&1 || true
    else
        echo "test input" | timeout 2 ./cat "$arg" > /dev/null 2>&1 || true
    fi
    
    sleep 0.1
    
    if [ -f "cat.gcda" ]; then
        gcov cat > /dev/null 2>&1
        if [ -f "cat.c.gcov" ]; then
            arg_safe=$(echo "$arg" | sed 's/[^a-zA-Z0-9._-]/_/g' | sed 's/__*/_/g')
            if [ -z "$arg_safe" ]; then
                arg_safe="empty"
            fi
            # Truncate to avoid "File name too long" (max 80 chars for arg_safe)
            arg_safe="${arg_safe:0:80}"
            output_file="${RESULTS_DIR}/cat_test$(printf "%06d" $test_num)_arg_${arg_safe}.gcov.txt"
            cp cat.c.gcov "${output_file}"
            echo "  ✓ Saved: $(basename ${output_file})"
        else
            echo "  ✗ cat.c.gcov not generated"
        fi
    else
        echo "  ✗ cat.gcda not created (program may have failed or exited early)"
    fi
    
    test_num=$((test_num + 1))
done < "${KTEST_ARGS_FILE}"

echo "Coverage files saved to ${RESULTS_DIR}/"
