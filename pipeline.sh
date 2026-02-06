START_TROJAN=1 source ~/enable-proxy.sh

# docker run --rm -v "$(pwd):/workspace" -w /workspace klee-coreutils python3 run_klee_and_replay.py --limit 10

python3 symbolic_llm.py --limit 20

docker run --rm -v "$(pwd):/workspace" -w /workspace klee-coreutils python3 run_symbolic_executor.py

docker run --rm -v "$(pwd):/workspace" -w /workspace klee-coreutils python3 coverage_aggregate.py

python3 generate_targeted_inputs.py --all

docker run --rm -v "$(pwd):/workspace" -w /workspace klee-coreutils python3 run_targeted_coverage.py --all


