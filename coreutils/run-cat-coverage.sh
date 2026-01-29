#!/bin/bash

cd /
cd /workspace/coreutils/coreutils-8.32/obj-gcov/src

if [ ! -f "./cat" ]; then
    echo "Error: cat binary not found in $(pwd)"
    exit 1
fi

mkdir -p /workspace/results/klee-result
chmod 777 /workspace/results/klee-result
echo "Results will be saved to: /workspace/results/klee-result/"
echo ""

KTEST_ARGS_FILE="/workspace/coreutils/coreutils-8.32/obj-llvm/src/klee-last/ktest-arguments.txt"

if [ ! -f "${KTEST_ARGS_FILE}" ]; then
    KTEST_ARGS_FILE="/workspace/ktest-arguments.txt"
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
            output_file="/workspace/results/klee-result/cat_test$(printf "%06d" $test_num)_arg_${arg_safe}.gcov.txt"
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

echo "Coverage files saved to /workspace/results/klee-result/"
