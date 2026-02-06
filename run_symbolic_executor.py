#!/usr/bin/env python3
"""
Discover programs from result/llm/*_inputs.json; for each, run the gcov binary
with those inputs and write result/llm/<util>_symbolic.txt and
result/llm/<util>_symbolic_coverage/. No LLM calls; use symbolic_llm.py to
generate the input JSON files.
"""

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

WORKSPACE_ROOT = Path(__file__).parent.resolve()
OBJ_GCOV_DIR = WORKSPACE_ROOT / "coreutils/coreutils-8.32/obj-gcov/src"
OBJ_GCOV_TOP = WORKSPACE_ROOT / "coreutils/coreutils-8.32/obj-gcov"
DEFAULT_RESULT_DIR = WORKSPACE_ROOT / "result" / "llm"

# Report name (from _inputs.json stem) -> binary name in obj-gcov/src (e.g. lbracket -> "[")
REPORT_NAME_TO_UTIL: dict[str, str] = {
    "lbracket": "[",
}
# gcov .gcda/.gcno base name (executable "[" builds lbracket.gcda/lbracket.gcno)
UTIL_TO_GCOV_BASE: dict[str, str] = {
    "[": "lbracket",
}
UTIL_TO_REPORT_NAME: dict[str, str] = {v: k for k, v in REPORT_NAME_TO_UTIL.items()}


def get_programs_from_result_llm(results_dir: Path) -> List[Tuple[str, str]]:
    """
    Discover programs from result/llm/*_inputs.json.
    Returns sorted list of (report_name, util_name) where util_name is the binary name to run.
    """
    if not results_dir.is_dir():
        return []
    out = []
    for p in results_dir.glob("*_inputs.json"):
        report_name = p.stem.removesuffix("_inputs")
        util_name = REPORT_NAME_TO_UTIL.get(report_name, report_name)
        out.append((report_name, util_name))
    out.sort(key=lambda x: x[0])
    return out


def load_inputs_from_json(results_dir: Path, report_name: str) -> List[str]:
    """Load input list from result/llm/<report_name>_inputs.json. Return [] if missing or invalid."""
    path = results_dir / f"{report_name}_inputs.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(x) for x in data]
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _arg_safe_for_filename(arg: str, max_len: int = 80) -> str:
    """Sanitize an argument string for use in a filename (alphanumeric, underscore)."""
    s = re.sub(r"[^a-zA-Z0-9._-]", "_", arg)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:max_len] if s else "empty"


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


def run_coverage_for_util(
    workspace_root: Path,
    util_name: str,
    inputs_list: list,
    results_dir: Path,
    timeout_per_run: float = 30.0,
) -> bool:
    """
    Run the utility with each generated input and save reports.
    Prefers gcov binary (obj-gcov/src/<util>); otherwise runs system binary
    and saves stdout. Writes result/<util>_symbolic.txt. When gcov is available,
    also runs gcov per input and saves reports to result/<util>_symbolic_coverage/.
    """
    if not inputs_list:
        return True
    results_dir.mkdir(parents=True, exist_ok=True)
    gcov_bin = OBJ_GCOV_DIR / util_name
    use_gcov = OBJ_GCOV_DIR.is_dir() and gcov_bin.exists() and os.access(gcov_bin, os.X_OK)
    cwd = str(OBJ_GCOV_DIR if use_gcov else workspace_root)
    binary = str(gcov_bin) if use_gcov else util_name
    report_name = UTIL_TO_REPORT_NAME.get(util_name, util_name)  # for coverage_dir / out_file naming
    gcda_base = UTIL_TO_GCOV_BASE.get(util_name, util_name)
    report_lines = []

    coverage_dir: Optional[Path] = None
    if use_gcov:
        coverage_dir = results_dir / f"{report_name}_symbolic_coverage"
        coverage_dir.mkdir(parents=True, exist_ok=True)

    for i, inp in enumerate(inputs_list):
        inp_str = str(inp).strip()
        test_num = i + 1

        if use_gcov:
            gcda_path = OBJ_GCOV_DIR / f"{gcda_base}.gcda"
            if gcda_path.exists():
                gcda_path.unlink()

        try:
            args = shlex.split(inp_str) if inp_str else []
            cmd = [binary] + args
            result = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout_per_run,
            )
            report_lines.append(f"=== input {test_num}: {inp_str!r} -> {cmd!r} ===")
            report_lines.append(result.stdout or "")
            if result.stderr:
                report_lines.append("stderr: " + result.stderr)
        except subprocess.TimeoutExpired:
            report_lines.append(f"=== input {test_num}: {inp_str!r} === (timeout)")
        except Exception as e:
            report_lines.append(f"=== input {test_num}: {inp_str!r} === error: {e}")

        if use_gcov and coverage_dir is not None:
            arg_safe = _arg_safe_for_filename(inp_str)
            gcov_processed = False
            if util_name in UTIL_TO_GCOV_BASE:
                gcda_path = OBJ_GCOV_DIR / f"{gcda_base}.gcda"
                if gcda_path.exists():
                    try:
                        _run_gcov_for_gcda(gcda_path, OBJ_GCOV_TOP, OBJ_GCOV_DIR)
                        gcov_processed = True
                    finally:
                        try:
                            gcda_path.unlink()
                        except OSError:
                            pass
            else:
                recent = _find_recent_gcda_files(OBJ_GCOV_DIR, within_seconds=2.0)
                for gcda_path in recent:
                    _run_gcov_for_gcda(gcda_path, OBJ_GCOV_TOP, OBJ_GCOV_DIR)
                    try:
                        gcda_path.unlink()
                    except OSError:
                        pass
                    gcov_processed = True
            if gcov_processed:
                for gcov_path in OBJ_GCOV_TOP.rglob("*.gcov"):
                    if not gcov_path.stem.endswith(".c"):
                        continue  # only save coverage for .c sources, not .h etc.
                    try:
                        rel = gcov_path.relative_to(OBJ_GCOV_TOP)
                        dest_name = f"test{test_num:06d}_arg_{arg_safe}_{rel.as_posix().replace('/', '_')}.txt"
                        dest = coverage_dir / dest_name
                        dest.write_text(
                            gcov_path.read_text(encoding="utf-8", errors="replace")
                        )
                    except Exception:
                        pass

    out_file = results_dir / f"{report_name}_symbolic.txt"
    out_file.write_text("\n".join(report_lines), encoding="utf-8")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Run gcov binary with inputs from result/llm/<util>_inputs.json and save coverage"
    )
    parser.add_argument(
        "--results-dir",
        type=str,
        default=None,
        help=f"Directory for reports and input JSON (default: {DEFAULT_RESULT_DIR})",
    )
    parser.add_argument(
        "--aggregate",
        action="store_true",
        help="After generating coverage, run coverage_aggregate to merge .gcov (requires lcov/gcovr)",
    )
    args = parser.parse_args()

    workspace_root = WORKSPACE_ROOT
    results_dir = Path(args.results_dir) if args.results_dir else DEFAULT_RESULT_DIR

    programs = get_programs_from_result_llm(results_dir)
    if not programs:
        print(f"No *_inputs.json found in {results_dir}. Run symbolic_llm.py to generate them.", file=sys.stderr)
        raise SystemExit(1)

    print(f"Found {len(programs)} program(s) from result/llm/*_inputs.json. Results -> {results_dir}")
    if not OBJ_GCOV_DIR.is_dir():
        print("Note: obj-gcov not found; no per-input gcov reports (result/llm/*_symbolic_coverage/).")
        print("      Build with: ./step1-build-gcov.sh coreutils/coreutils-8.32")

    for report_name, util_name in programs:
        inputs_list = load_inputs_from_json(results_dir, report_name)
        if not inputs_list:
            print(f"Skipping {report_name} (empty or invalid {report_name}_inputs.json)")
            continue
        gcov_bin = OBJ_GCOV_DIR / util_name
        if not gcov_bin.exists() or not gcov_bin.is_file():
            print(f"Skipping {report_name} (gcov binary not found: {util_name})")
            continue
        print(f"=== {report_name} ===")
        print(f"  Loaded {len(inputs_list)} inputs from {report_name}_inputs.json")
        ok = run_coverage_for_util(workspace_root, util_name, inputs_list, results_dir)
        if ok:
            print(f"  -> {results_dir / (report_name + '_symbolic.txt')}")
            coverage_dir = results_dir / f"{report_name}_symbolic_coverage"
            if coverage_dir.exists():
                print(f"  -> {coverage_dir}/ (gcov per input)")

    if args.aggregate:
        # Run coverage_aggregate to merge result/llm/*_symbolic_coverage
        r = subprocess.run(
            [sys.executable, str(workspace_root / "coverage_aggregate.py")],
            cwd=str(workspace_root),
        )
        if r.returncode != 0:
            print("Warning: coverage_aggregate exited with non-zero status.", file=sys.stderr)

    print("Done.")


if __name__ == "__main__":
    main()
