#!/usr/bin/env python3
"""
Run KLEE on all .bc files, save klee-stats to result/klee/<util>.txt,
then replay each .ktest with the gcov binary and merge line coverage into result/klee/merged-coverage.

Steps:
  1. For each .bc: run KLEE in docker (writes to cwd/klee-last), copy klee-last to result/klee/<util>/, klee-stats -> result/klee/<util>.txt
  2. For each util with .ktest files: run ktest-tool to get args, run obj-gcov/src/<util> with those args, run gcov, save .gcov.txt to result/klee/<util>_klee_coverage/
  3. Run coverage_aggregate on all *_klee_coverage dirs -> result/klee/merged-coverage and result/klee/merged_summary.txt

Requires: docker (klee-coreutils image); for replay, obj-gcov (step1-build-gcov.sh) or it will be
built automatically when running inside the container. lcov/gcovr for merge.

When running this script inside Docker (native klee/ktest-tool), obj-gcov is built in the container
if missing or if host-built binaries do not run there, so the full pipeline can run in one container.
When using docker from the host, set WORKSPACE_HOST to the host path mounted as the project
(e.g. docker run -v "$(pwd):/workspace" -e WORKSPACE_HOST="$(pwd)" ... python3 run_klee_and_replay.py).
"""

import argparse
import ast
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

WORKSPACE_ROOT = Path(__file__).parent.resolve()
# When script runs inside Docker, inner "docker run -v" is resolved on the host. Use WORKSPACE_HOST
# so the inner container gets the same dir (e.g. docker run -e WORKSPACE_HOST="$(pwd)" ...).
WORKSPACE_FOR_DOCKER_V = os.environ.get("WORKSPACE_HOST", str(WORKSPACE_ROOT))
BC_DIR = WORKSPACE_ROOT / "coreutils/coreutils-8.32/obj-llvm/src"

# If klee and ktest-tool are in PATH (e.g. we're inside klee-coreutils image), run them directly
# instead of via docker (docker CLI is not in the image).
USE_NATIVE_KLEE = shutil.which("klee") is not None and shutil.which("ktest-tool") is not None
COREUTILS_SRC = WORKSPACE_ROOT / "coreutils/coreutils-8.32"
OBJ_GCOV_DIR = WORKSPACE_ROOT / "coreutils/coreutils-8.32/obj-gcov/src"
OBJ_GCOV_TOP = WORKSPACE_ROOT / "coreutils/coreutils-8.32/obj-gcov"
KLEE_RESULT_DIR = WORKSPACE_ROOT / "result" / "klee"
STEP1_BUILD_GCOV = WORKSPACE_ROOT / "step1-build-gcov.sh"
KLEE_OUTPUT_DIR = KLEE_RESULT_DIR  # KLEE --output-dir (per-util subdirs)
DOCKER_IMAGE = "klee-coreutils"

# Same as run_symbolic_executor: [ -> lbracket for dirs and gcov base
UTIL_TO_REPORT_NAME: dict[str, str] = {"[": "lbracket"}
UTIL_TO_GCOV_BASE: dict[str, str] = {"[": "lbracket"}

KLEE_SYM_ARGS = "--sym-args 0 2 4"
KLEE_MAX_TIME = 300


def get_bc_files(workspace_root: Path, limit: Optional[int] = None) -> List[Path]:
    """Discover .bc files in obj-llvm/src; exclude *.o.bc and hidden. Optionally limit count."""
    if not BC_DIR.is_dir():
        return []
    files = []
    for p in BC_DIR.iterdir():
        if not p.is_file() or p.name.startswith("."):
            continue
        if p.suffix == ".bc" and not p.name.endswith(".o.bc"):
            files.append(p)
    files.sort(key=lambda x: x.name)
    if limit is not None and limit > 0:
        files = files[:limit]
    return files


def report_name(util: str) -> str:
    return UTIL_TO_REPORT_NAME.get(util, util)


def run_klee(util_name: str, output_subdir: Path, timeout: int = KLEE_MAX_TIME) -> bool:
    """Run KLEE (writes to cwd/klee-last); copy klee-last to output_subdir; klee-stats -> result/klee/<util>.txt."""
    output_subdir.mkdir(parents=True, exist_ok=True)
    stats_file = KLEE_RESULT_DIR / f"{report_name(util_name)}.txt"
    # Pass .bc path via env so shell does not expand special chars (e.g. [ in "[.bc")
    bc_path = f"./{util_name}.bc"
    shell_cmd = (
        f"klee --libc=uclibc --posix-runtime --max-time={timeout} "
        f"\"$KLEE_BC\" {KLEE_SYM_ARGS} 2>/dev/null; klee-stats klee-last"
    )
    try:
        with open(stats_file, "w", encoding="utf-8") as f:
            if USE_NATIVE_KLEE:
                env = os.environ.copy()
                env["KLEE_BC"] = bc_path
                r = subprocess.run(
                    ["bash", "-c", shell_cmd],
                    cwd=str(BC_DIR),
                    stdout=f,
                    stderr=subprocess.STDOUT,
                    timeout=timeout + 60,
                    env=env,
                )
            else:
                cmd = [
                    "docker", "run", "--rm",
                    "-v", f"{WORKSPACE_FOR_DOCKER_V}:/workspace",
                    "-w", "/workspace/coreutils/coreutils-8.32/obj-llvm/src",
                    "-e", f"KLEE_BC={bc_path}",
                    DOCKER_IMAGE,
                    "bash", "-c", shell_cmd,
                ]
                r = subprocess.run(cmd, cwd=str(WORKSPACE_ROOT), stdout=f, stderr=subprocess.STDOUT, timeout=timeout + 60)
        # Copy KLEE output from obj-llvm/src/klee-last to result/klee/<util>/ so replay finds .ktest
        klee_last = BC_DIR / "klee-last"
        if klee_last.exists():
            src = klee_last.resolve() if klee_last.is_symlink() else klee_last
            if src.is_dir():
                for f in src.iterdir():
                    dest = output_subdir / f.name
                    if dest.exists():
                        if dest.is_dir():
                            shutil.rmtree(dest, ignore_errors=True)
                        else:
                            dest.unlink(missing_ok=True)
                    shutil.copytree(f, dest, symlinks=True) if f.is_dir() else shutil.copy2(f, dest)
        return r.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        with open(stats_file, "a", encoding="utf-8") as f:
            f.write(f"\nError: {e}\n")
        return False


def ktest_tool_get_args(ktest_path: Path) -> Optional[List[str]]:
    """Run ktest-tool on ktest_path; parse 'args : [...]' and return list of argv strings."""
    try:
        if USE_NATIVE_KLEE:
            out = subprocess.run(
                ["ktest-tool", str(ktest_path)],
                cwd=str(WORKSPACE_ROOT),
                capture_output=True,
                text=True,
                timeout=30,
            )
        else:
            rel = ktest_path.relative_to(WORKSPACE_ROOT).as_posix()
            cmd = [
                "docker", "run", "--rm",
                "-v", f"{WORKSPACE_FOR_DOCKER_V}:/workspace",
                "-w", "/workspace",
                DOCKER_IMAGE,
                "ktest-tool", rel,
            ]
            out = subprocess.run(
                cmd,
                cwd=str(WORKSPACE_ROOT),
                capture_output=True,
                text=True,
                timeout=30,
            )
        if out.returncode != 0:
            return None
        # e.g. "args : ['./base64.bc', 'x', 'y']" or "args: [...]"
        m = re.search(r"args\s*:\s*(\[[^\]]*\]|\S+)", out.stdout)
        if not m:
            return None
        list_str = m.group(1).strip()
        if list_str.startswith("["):
            return ast.literal_eval(list_str)
        return None
    except Exception:
        return None


def build_obj_gcov_in_container() -> bool:
    """Build obj-gcov (gcov-instrumented coreutils) in the current environment. Returns True on success."""
    if not COREUTILS_SRC.is_dir() or not (COREUTILS_SRC / "configure").exists():
        sys.stderr.write("coreutils source or configure not found; cannot build obj-gcov.\n")
        return False
    if not STEP1_BUILD_GCOV.exists():
        sys.stderr.write("step1-build-gcov.sh not found.\n")
        return False
    try:
        env = os.environ.copy()
        env["FORCE_UNSAFE_CONFIGURE"] = "1"  # allow configure when running as root (e.g. in Docker)
        r = subprocess.run(
            ["bash", str(STEP1_BUILD_GCOV), str(COREUTILS_SRC)],
            cwd=str(WORKSPACE_ROOT),
            timeout=600,
            env=env,
        )
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        sys.stderr.write("step1-build-gcov.sh timed out.\n")
        return False
    except Exception as e:
        sys.stderr.write(f"step1-build-gcov.sh failed: {e}\n")
        return False


def _find_recent_gcda_files(obj_gcov_src: Path, within_seconds: float = 2.0) -> List[Path]:
    """Return .gcda files under obj_gcov_src modified in the last within_seconds."""
    now = time.time()
    out = []
    for p in obj_gcov_src.rglob("*.gcda"):
        try:
            if now - p.stat().st_mtime <= within_seconds:
                out.append(p)
        except OSError:
            pass
    return out


def _run_gcov_for_gcda(gcda_path: Path, obj_gcov_top: Path, obj_gcov_src: Path) -> None:
    """Run gcov for one .gcda (base = path relative to obj_gcov_src without .gcda)."""
    try:
        base = gcda_path.relative_to(obj_gcov_src).with_suffix("")
        base_str = str(base).replace("\\", "/")
        subprocess.run(
            ["gcov", "-o", "src", base_str],
            cwd=str(obj_gcov_top),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception:
        pass


def _probe_gcov_binary(gcov_bin: Path) -> Optional[str]:
    """Run gcov binary with --version. Return None if it runs, else an error message."""
    try:
        r = subprocess.run(
            [str(gcov_bin), "--version"],
            cwd=str(OBJ_GCOV_DIR),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode == 0:
            return None
        return (r.stderr or r.stdout or f"exit code {r.returncode}").strip() or f"exit code {r.returncode}"
    except FileNotFoundError as e:
        return str(e)
    except subprocess.TimeoutExpired:
        return "timeout"
    except Exception as e:
        return str(e)


def replay_ktests(
    util_name: str,
    ktest_paths: List[Path],
    coverage_dir: Path,
    timeout_per_run: float = 30.0,
) -> int:
    """
    Replay each .ktest with the gcov binary: ktest-tool -> args -> run binary -> gcov -> save .gcov.txt.
    Returns number of tests replayed successfully.
    """
    gcov_bin = OBJ_GCOV_DIR / util_name
    if not gcov_bin.exists() or not gcov_bin.is_file():
        return 0
    err = _probe_gcov_binary(gcov_bin)
    if err is not None:
        sys.stderr.write(
            f"  Warning: gcov binary {gcov_bin.name} did not run: {err}\n"
            "  Replay needs binaries that run in this environment. When using Docker with a\n"
            "  host-mounted workspace, host-built obj-gcov binaries may not run in the container.\n"
            "  Either build obj-gcov inside the container, or run replay on the host:\n"
            "    python3 run_klee_and_replay.py --skip-klee\n"
        )
        return 0
    gcda_base = UTIL_TO_GCOV_BASE.get(util_name, util_name)
    cwd = str(OBJ_GCOV_DIR)
    count = 0
    args_failures = 0
    for i, kpath in enumerate(ktest_paths):
        args_list = ktest_tool_get_args(kpath)
        if not args_list:
            args_failures += 1
            continue
        # Replace first arg (program) with gcov binary path
        run_args = [str(gcov_bin)] + list(args_list[1:])
        # Clear previous .gcda for this util so we only get this run's coverage
        fixed_gcda = OBJ_GCOV_DIR / f"{gcda_base}.gcda"
        if fixed_gcda.exists():
            fixed_gcda.unlink(missing_ok=True)
        # Also clear .gcda in subdirs (e.g. b2sum writes to blake2/*.gcda)
        for old in OBJ_GCOV_DIR.rglob("*.gcda"):
            try:
                old.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            subprocess.run(
                run_args,
                cwd=cwd,
                capture_output=True,
                timeout=timeout_per_run,
            )
        except (subprocess.TimeoutExpired, Exception):
            pass
        # Find .gcda files written by this run (recent mtime); run gcov and copy .gcov
        recent_gcda = _find_recent_gcda_files(OBJ_GCOV_DIR, within_seconds=2.0)
        gcov_processed = False
        for gcda_path in recent_gcda:
            _run_gcov_for_gcda(gcda_path, OBJ_GCOV_TOP, OBJ_GCOV_DIR)
            gcov_processed = True
            try:
                gcda_path.unlink(missing_ok=True)
            except OSError:
                pass
        if gcov_processed:
            for gcov_path in OBJ_GCOV_TOP.rglob("*.gcov"):
                if not gcov_path.stem.endswith(".c"):
                    continue
                try:
                    rel = gcov_path.relative_to(OBJ_GCOV_TOP)
                    dest_name = f"klee_{i+1:06d}_{rel.as_posix().replace('/', '_')}.txt"
                    dest = coverage_dir / dest_name
                    dest.write_text(gcov_path.read_text(encoding="utf-8", errors="replace"))
                    count += 1
                except Exception:
                    pass
    if args_failures and count == 0 and args_failures == len(ktest_paths):
        sys.stderr.write(
            f"  Warning: ktest-tool returned no args for all {len(ktest_paths)} .ktest files.\n"
        )
    return count


def main() -> None:
    global DOCKER_IMAGE
    parser = argparse.ArgumentParser(
        description="Run KLEE on all .bc, replay .ktest with gcov binary, merge line coverage."
    )
    parser.add_argument(
        "--results-dir",
        type=str,
        default=None,
        help=f"Base result directory (default: result/klee)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=2,
        help="Limit number of .bc files to process (default: 2 for testing; use --limit 0 for all)",
    )
    parser.add_argument(
        "--skip-klee",
        action="store_true",
        help="Skip KLEE runs; only replay existing .ktest and merge",
    )
    parser.add_argument(
        "--skip-replay",
        action="store_true",
        help="Skip replay and merge; only run KLEE and save klee-stats",
    )
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="Skip coverage merge (do not run coverage_aggregate)",
    )
    parser.add_argument(
        "--docker-image",
        type=str,
        default=DOCKER_IMAGE,
        help=f"Docker image for KLEE (default: {DOCKER_IMAGE})",
    )
    parser.add_argument(
        "--max-replay",
        type=int,
        default=200,
        help="Max number of .ktest files to replay per util (default: 200); replay can be very slow otherwise",
    )
    args = parser.parse_args()
    DOCKER_IMAGE = args.docker_image

    results_dir = WORKSPACE_ROOT / (args.results_dir or "result/klee")
    results_dir.mkdir(parents=True, exist_ok=True)

    if not BC_DIR.is_dir():
        print("Error: BC dir not found. Run setup-coreutils.sh and step2-build-llvm.sh first.", file=sys.stderr)
        sys.exit(1)

    bc_files = get_bc_files(WORKSPACE_ROOT, limit=args.limit)
    if not bc_files:
        print("Error: No .bc files found.", file=sys.stderr)
        sys.exit(1)

    # --- Step 1: Run KLEE for each .bc ---
    if not args.skip_klee:
        if USE_NATIVE_KLEE:
            print("Using native klee/ktest-tool (running inside container).")
        print(f"Running KLEE on {len(bc_files)} .bc file(s). Output -> {results_dir}/")
        for bc_path in bc_files:
            util = bc_path.stem
            rname = report_name(util)
            out_subdir = results_dir / rname
            print(f"  === {rname}.bc ===")
            ok = run_klee(util, out_subdir)
            stats_file = results_dir / f"{rname}.txt"
            print(f"    -> {stats_file}" + ("" if ok else " (warnings/errors in output)"))
    else:
        print("Skipping KLEE (--skip-klee). Using existing result/klee/<util>/ .ktest files.")

    # --- Step 2: Replay .ktest with gcov and save .gcov.txt ---
    if not args.skip_replay:
        if USE_NATIVE_KLEE and (not OBJ_GCOV_DIR.is_dir() or not (OBJ_GCOV_DIR / "echo").exists()):
            print("Building obj-gcov in container (required for replay)...")
            if build_obj_gcov_in_container():
                print("obj-gcov build complete.")
            else:
                print("obj-gcov build failed; skipping replay.", file=sys.stderr)
        elif USE_NATIVE_KLEE and OBJ_GCOV_DIR.is_dir():
            # Probe one gcov binary; if host-built and incompatible, rebuild in container
            probe_util = None
            for d in sorted(results_dir.iterdir()):
                if not d.is_dir() or d.name.endswith("_klee_coverage") or d.name == "merged-coverage":
                    continue
                ktests = list(d.rglob("*.ktest"))[:1]
                if not ktests:
                    continue
                rev_report = {v: k for k, v in UTIL_TO_REPORT_NAME.items()}
                probe_util = rev_report.get(d.name, d.name)
                break
            if probe_util and (OBJ_GCOV_DIR / probe_util).exists():
                err = _probe_gcov_binary(OBJ_GCOV_DIR / probe_util)
                if err is not None:
                    print("gcov binaries from host may not run in container; building obj-gcov in container...")
                    if build_obj_gcov_in_container():
                        print("obj-gcov build complete.")
                    else:
                        print("obj-gcov build failed; replay may produce 0 coverage.", file=sys.stderr)
        if not OBJ_GCOV_DIR.is_dir():
            if not USE_NATIVE_KLEE:
                print("obj-gcov not found; skipping replay. Build with: ./step1-build-gcov.sh coreutils/coreutils-8.32", file=sys.stderr)
        else:
            # Find all result/klee/<util>/ dirs that look like KLEE output (contain .ktest)
            replayed = 0
            for d in sorted(results_dir.iterdir()):
                if not d.is_dir():
                    continue
                # Skip *_klee_coverage and other non-KLEE-output dirs
                if d.name.endswith("_klee_coverage") or d.name == "merged-coverage":
                    continue
                all_ktests = sorted(d.rglob("*.ktest"))
                ktests = all_ktests[: args.max_replay]
                if not ktests:
                    continue
                if len(all_ktests) > args.max_replay:
                    print(f"  (capping replay to first {args.max_replay} of {len(all_ktests)} .ktest for {d.name})")
                # d.name is report name (e.g. lbracket or base64); we need bc name for gcov binary
                # result/klee/base64 -> util base64; result/klee/lbracket -> util [
                rev_report = {v: k for k, v in UTIL_TO_REPORT_NAME.items()}
                util_name = rev_report.get(d.name, d.name)
                gcov_bin = OBJ_GCOV_DIR / util_name
                if not gcov_bin.exists():
                    continue
                coverage_dir = results_dir / f"{d.name}_klee_coverage"
                coverage_dir.mkdir(parents=True, exist_ok=True)
                n = replay_ktests(util_name, ktests, coverage_dir)
                replayed += n
                print(f"  Replayed {len(ktests)} .ktest for {d.name} -> {n} .gcov.txt in {coverage_dir.name}/")
            if replayed:
                print(f"  Total .gcov.txt written: {replayed}")
    else:
        print("Skipping replay (--skip-replay).")

    # --- Step 3: Merge coverage ---
    if not args.no_merge and not args.skip_replay:
        coverage_dirs = sorted(d for d in results_dir.iterdir() if d.is_dir() and d.name.endswith("_klee_coverage"))
        if coverage_dirs:
            out_merge = results_dir / "merged-coverage"
            cmd = [
                sys.executable,
                str(WORKSPACE_ROOT / "coverage_aggregate.py"),
                *[str(d) for d in coverage_dirs],
                "-o", str(out_merge),
            ]
            print("Running coverage merge...")
            r = subprocess.run(cmd, cwd=str(WORKSPACE_ROOT))
            if r.returncode != 0:
                print("Warning: coverage_aggregate exited with non-zero status.", file=sys.stderr)
            else:
                print(f"Merged coverage -> {out_merge}; summary -> {results_dir}/merged_summary.txt")
        else:
            print("No *_klee_coverage dirs found; skip merge.")

    print("Done.")


if __name__ == "__main__":
    main()
