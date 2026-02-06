#!/usr/bin/env python3
"""
Run targeted coverage: read inputs from JSON file, run coverage for the utility,
and aggregate/compare with previous coverage.
"""

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Optional

from run_symbolic_executor import run_coverage_for_util, UTIL_TO_REPORT_NAME, get_programs_from_result_llm
from coverage_aggregate import (
    aggregate_directories,
    discover_coverage_dirs,
    get_merged_line_coverage_pct,
)


WORKSPACE_ROOT = Path(__file__).parent.resolve()
DEFAULT_RESULT_DIR = WORKSPACE_ROOT / "result" / "llm"
DEFAULT_MERGED_COVERAGE = WORKSPACE_ROOT / "result" / "llm" / "merged-coverage"

# Map source file stem to executable name (for special cases like lbracket.c -> "[")
REPORT_NAME_TO_UTIL = {v: k for k, v in UTIL_TO_REPORT_NAME.items()}


def resolve_program_path(program: str, workspace_root: Path) -> Path:
    """Resolve program to an absolute path. Tries coreutils src, then coreutils-6.11."""
    p = Path(program)
    if p.is_absolute() and p.exists():
        return p
    if p.exists():
        return p.resolve()
    for base in ("coreutils/coreutils-8.32/src", "coreutils/coreutils-6.11/src"):
        candidate = workspace_root / base / program
        if candidate.exists():
            return candidate
    for base in ("coreutils/coreutils-8.32/src", "coreutils/coreutils-6.11/src"):
        candidate = workspace_root / base / p.name
        if candidate.exists():
            return candidate
    return (workspace_root / program).resolve()


def source_stem_to_util(stem: str) -> str:
    """Derive executable/util name from source file stem (e.g. lbracket -> '[', md5sum -> md5sum)."""
    return REPORT_NAME_TO_UTIL.get(stem, stem)


def infer_previous_results_dir(cumulative_path: Path, workspace_root: Path) -> Optional[Path]:
    """
    If cumulative is result/llm/merged-coverage/.../cumulative.gcov.txt, return result/llm.
    Otherwise return None.
    """
    try:
        cumulative_path = cumulative_path.resolve()
        workspace_root = workspace_root.resolve()
        rel = cumulative_path.relative_to(workspace_root)
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) >= 2 and parts[0] == "result" and parts[1] == "llm":
        return workspace_root / "result" / "llm"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run targeted inputs for coverage and compare results"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run targeted coverage for all programs that have result/llm/*_inputs.json and *_targeted_inputs.json.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="When using --all, max number of programs to process (default: 0 = all).",
    )
    parser.add_argument(
        "--inputs",
        type=str,
        default=None,
        help="Path to JSON file containing list of inputs. Default: result/llm/{util}_targeted_inputs.json.",
    )
    parser.add_argument(
        "--function-name",
        type=str,
        default=None,
        help="Function/source name (e.g. md5sum). Used to infer program/paths.",
    )
    parser.add_argument(
        "--program",
        type=str,
        default=None,
        help="Program source file (e.g. md5sum.c). Default: inferred from --function-name.",
    )
    parser.add_argument(
        "--cumulative",
        type=str,
        default=None,
        help="Path to previous cumulative report (used only to infer --previous-results-dir if not provided).",
    )
    parser.add_argument(
        "--previous-results-dir",
        type=str,
        default=None,
        help="Parent directory containing *_symbolic_coverage subdirs to compare against (default: result/llm).",
    )
    parser.add_argument(
        "--results-dir",
        type=str,
        default=None,
        help="Parent directory for new targeted coverage (default: result/llm; new run goes to result/llm/targeted_uncovered_{util}_manual)",
    )
    parser.add_argument(
        "--output-merged",
        type=str,
        default=None,
        help="Directory for merged coverage after adding targeted run (default: result/llm/merged-coverage-after-targeted)",
    )
    parser.add_argument(
        "--model-name",
        type=str,
        default="manual",
        help="Suffix for output directory (targeted_uncovered_{util}_{suffix}). Default: manual.",
    )
    args = parser.parse_args()

    workspace_root = WORKSPACE_ROOT
    results_parent = (workspace_root / args.results_dir) if args.results_dir else DEFAULT_RESULT_DIR
    model_safe = re.sub(r"[^a-zA-Z0-9._-]", "_", args.model_name)
    output_merged = (workspace_root / args.output_merged) if args.output_merged else (DEFAULT_RESULT_DIR / "merged-coverage-after-targeted")

    # Resolve previous results dir (for "Before") once
    if args.previous_results_dir:
        previous_parent = workspace_root / args.previous_results_dir
    elif args.cumulative and not args.all:
        cumulative_path = (workspace_root / args.cumulative) if not Path(args.cumulative).is_absolute() else Path(args.cumulative)
        prev = infer_previous_results_dir(cumulative_path, workspace_root)
        previous_parent = prev if (prev and prev.exists()) else DEFAULT_RESULT_DIR
    else:
        previous_parent = DEFAULT_RESULT_DIR

    previous_dirs = discover_coverage_dirs(previous_parent)
    if not previous_dirs and not args.all:
        print(f"Warning: no *_symbolic_coverage dirs under {previous_parent}. Before coverage will be empty.", file=sys.stderr)

    # --- Batch mode: programs that have *_inputs.json and *_targeted_inputs.json ---
    if args.all:
        programs = [
            (r, u) for r, u in get_programs_from_result_llm(DEFAULT_RESULT_DIR)
            if not r.endswith("_targeted")
        ]
        if args.limit and args.limit > 0:
            programs = programs[: args.limit]
        to_run = []
        for report_name, util_name in programs:
            inputs_path = DEFAULT_RESULT_DIR / f"{report_name}_targeted_inputs.json"
            if not inputs_path.exists():
                continue
            try:
                with open(inputs_path, "r", encoding="utf-8") as f:
                    inputs_list = json.load(f)
                if not isinstance(inputs_list, list):
                    continue
            except Exception:
                continue
            to_run.append((report_name, util_name, inputs_list))
        if not to_run:
            print("No programs with both *_inputs.json and *_targeted_inputs.json found.", file=sys.stderr)
            sys.exit(1)
        new_dirs = []
        for report_name, util_name, inputs_list in to_run:
            print(f"Running targeted coverage: {report_name} ({len(inputs_list)} inputs)...", file=sys.stderr)
            new_parent = results_parent / f"targeted_uncovered_{report_name}_{model_safe}"
            new_parent.mkdir(parents=True, exist_ok=True)
            ok = run_coverage_for_util(workspace_root, util_name, inputs_list, new_parent)
            if ok:
                cov_dir = new_parent / f"{report_name}_symbolic_coverage"
                if cov_dir.exists():
                    new_dirs.append(cov_dir)
        if not new_dirs:
            print("No coverage dirs produced.", file=sys.stderr)
            sys.exit(1)
        after_dirs = list(previous_dirs) + new_dirs
        print("Calculating Before/After coverage...", file=sys.stderr)
        with tempfile.TemporaryDirectory(prefix="agg_before_") as tmp_before:
            before_pct = get_merged_line_coverage_pct(previous_dirs, workspace_root, Path(tmp_before))
        after_pct = get_merged_line_coverage_pct(after_dirs, workspace_root, output_merged)
        aggregate_ok = aggregate_directories(
            after_dirs, workspace_root, output_merged, use_lcov=True, use_gcovr=True
        )
        print("\n" + "=" * 60)
        print("Coverage comparison (all targeted runs)")
        print("=" * 60)
        if before_pct is not None:
            print(f"Before (previous only): {before_pct:.1f}% line coverage")
        else:
            print("Before: could not compute")
        if after_pct is not None:
            print(f"After (previous + targeted): {after_pct:.1f}% line coverage")
        else:
            print("After: could not compute")
        if before_pct is not None and after_pct is not None:
            delta = after_pct - before_pct
            if delta > 0:
                print(f"Improvement: +{delta:.1f}%")
            elif delta < 0:
                print(f"Change: {delta:.1f}%")
            else:
                print("No change in line coverage.")
        print(f"Merged output: {output_merged}")
        sys.exit(0 if aggregate_ok else 1)

    # --- Single-program mode ---
    if args.program:
        program_path = resolve_program_path(args.program, workspace_root)
    elif args.function_name:
        program_path = resolve_program_path(f"{args.function_name}.c", workspace_root)
    else:
        print("Error: provide --all, or --program, or --function-name.", file=sys.stderr)
        sys.exit(1)

    util_name = source_stem_to_util(program_path.stem)
    report_name = UTIL_TO_REPORT_NAME.get(util_name, util_name)

    if args.inputs:
        inputs_path = Path(args.inputs)
    else:
        inputs_path = DEFAULT_RESULT_DIR / f"{report_name}_targeted_inputs.json"
        print(f"Using inputs from: {inputs_path}", file=sys.stderr)

    if not inputs_path.exists():
        print(f"Error: inputs file not found: {inputs_path}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(inputs_path, "r", encoding="utf-8") as f:
            inputs_list = json.load(f)
        if not isinstance(inputs_list, list):
            raise ValueError("JSON content must be a list of strings")
    except Exception as e:
        print(f"Error reading inputs JSON: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Program: {program_path} (util: {util_name})")
    print(f"Running {len(inputs_list)} inputs...")

    new_parent = results_parent / f"targeted_uncovered_{report_name}_{model_safe}"
    new_parent.mkdir(parents=True, exist_ok=True)

    ok = run_coverage_for_util(workspace_root, util_name, inputs_list, new_parent)
    if not ok:
        sys.exit(1)

    new_coverage_dir = new_parent / f"{report_name}_symbolic_coverage"
    if not new_coverage_dir.exists():
        print(f"Warning: expected coverage dir not found: {new_coverage_dir}", file=sys.stderr)
    print(f"New coverage reports saved to: {new_coverage_dir}")

    print("Calculating 'Before' coverage...", file=sys.stderr)
    with tempfile.TemporaryDirectory(prefix="agg_before_") as tmp_before:
        before_pct = get_merged_line_coverage_pct(
            previous_dirs,
            workspace_root,
            Path(tmp_before),
        )

    print("Calculating 'After' coverage...", file=sys.stderr)
    after_dirs = list(previous_dirs)
    if new_coverage_dir.exists():
        after_dirs.append(new_coverage_dir)
    after_pct = get_merged_line_coverage_pct(
        after_dirs,
        workspace_root,
        output_merged,
    )

    aggregate_ok = aggregate_directories(
        after_dirs,
        workspace_root,
        output_merged,
        use_lcov=True,
        use_gcovr=True,
    )

    print("\n" + "=" * 60)
    print("Coverage comparison")
    print("=" * 60)
    if before_pct is not None:
        print(f"Before (previous only): {before_pct:.1f}% line coverage")
    else:
        print("Before: could not compute")
    if after_pct is not None:
        print(f"After (previous + new):  {after_pct:.1f}% line coverage")
    else:
        print("After: could not compute")
    if before_pct is not None and after_pct is not None:
        delta = after_pct - before_pct
        if delta > 0:
            print(f"Improvement: +{delta:.1f}%")
        elif delta < 0:
            print(f"Change: {delta:.1f}%")
        else:
            print("No change in line coverage.")
    print(f"Merged output and cumulative: {output_merged}")
    sys.exit(0 if aggregate_ok else 1)


if __name__ == "__main__":
    main()
