#!/usr/bin/env python3
"""
Convert .gcov.txt reports to lcov .info and gcovr JSON, then merge and compute total coverage.
Works with results from run_symbolic_executor.py (result/llm/<util>_symbolic_coverage/*.gcov.txt).
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple


WORKSPACE_ROOT = Path(__file__).parent.resolve()
DEFAULT_RESULT_DIR = WORKSPACE_ROOT / "result" / "llm"
DEFAULT_COVERAGE_SUBDIR_GLOB = "*_symbolic_coverage"
DEFAULT_OUTPUT_DIR = WORKSPACE_ROOT / "result" / "llm" / "merged-coverage"


def parse_gcov_source_path(content: str) -> Optional[str]:
    """Extract source file path from gcov content (line like '    -:    0:Source:../src/cat.c')."""
    for line in content.split("\n"):
        if ":Source:" in line:
            idx = line.find("Source:")
            if idx != -1:
                return line[idx + 7 :].strip()
    return None


def parse_gcov_coverage(content: str) -> Dict[int, int]:
    """
    Parse gcov file. Returns dict mapping line_number -> execution_count.
    Non-executable lines (-) are skipped. ##### -> 0, number -> number.
    """
    coverage = {}
    for line in content.split("\n"):
        m = re.match(r"^\s*([-0-9#]+):\s*(\d+):", line)
        if not m:
            continue
        cov_str, line_num = m.group(1).strip(), int(m.group(2))
        if cov_str == "-":
            continue  # non-executable
        if cov_str == "#####":
            coverage[line_num] = 0
        else:
            try:
                coverage[line_num] = int(cov_str)
            except ValueError:
                coverage[line_num] = 0
    return coverage


def parse_gcov_lines(content: str) -> List[Tuple[str, int, str]]:
    """
    Parse gcov file line by line. Returns list of (count_str, line_num, rest) preserving order.
    count_str is the gcov prefix (-, #####, or number); rest is the part after "line_num:".
    """
    lines_out = []
    for line in content.split("\n"):
        m = re.match(r"^(\s*)([-0-9#]+)(\s*):\s*(\d+):(.*)$", line)
        if not m:
            lines_out.append(("-", 0, line))  # keep unparseable as-is
            continue
        _lead, count_str, _gap, line_num, rest = m.groups()
        lines_out.append((count_str.strip(), int(line_num), rest))
    return lines_out


def write_cumulative_gcov(
    gcov_paths: List[Path],
    output_path: Path,
) -> bool:
    """
    Merge all gcov files into a single cumulative report.
    A line is marked "+" if covered by any report (count > 0); otherwise "#####" if executable, "-" if not.
    Uses the first file as the template for line order and source text.
    """
    if not gcov_paths:
        return False
    merged_covered: set[int] = set()
    merged_executable: set[int] = set()
    template_lines: List[Tuple[str, int, str]] = []
    for p in gcov_paths:
        if not p.exists():
            continue
        content = p.read_text(encoding="utf-8", errors="replace")
        cov = parse_gcov_coverage(content)
        for line_num, count in cov.items():
            merged_executable.add(line_num)
            if count > 0:
                merged_covered.add(line_num)
        if not template_lines:
            template_lines = parse_gcov_lines(content)
    if not template_lines:
        return False
    out_lines = []
    for count_str, line_num, rest in template_lines:
        if count_str == "-":
            prefix = "        -"
        elif line_num in merged_covered:
            prefix = "        +"
        elif line_num in merged_executable:
            prefix = "    #####"
        else:
            prefix = "        -"
        out_lines.append(f"{prefix}: {line_num:>4}:{rest}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    return True


def gcov_to_lcov_info(content: str, source_path: Optional[str]) -> str:
    """Convert gcov text to lcov .info content. SF + DA lines + end_of_record."""
    path = source_path or parse_gcov_source_path(content) or "unknown.c"
    cov = parse_gcov_coverage(content)
    lines = [f"SF:{path}"]
    for ln in sorted(cov.keys()):
        lines.append(f"DA:{ln},{cov[ln]}")
    lines.append("end_of_record")
    return "\n".join(lines)


def normalize_gcovr_file_path(source_path: str, workspace_root: Path) -> str:
    """Convert gcov source path (e.g. ../src/cat.c from obj-gcov) to path relative to workspace_root."""
    if not source_path or source_path == "unknown.c":
        return source_path or "unknown.c"
    obj_gcov = workspace_root / "coreutils" / "coreutils-8.32" / "obj-gcov"
    try:
        resolved = (obj_gcov / source_path).resolve()
        return str(resolved.relative_to(workspace_root)).replace("\\", "/")
    except (ValueError, OSError):
        return source_path


def gcov_to_gcovr_json(
    content: str,
    source_path: Optional[str],
    workspace_root: Optional[Path] = None,
) -> Dict:
    """Convert gcov text to gcovr JSON tracefile (format_version 0.14)."""
    raw_path = source_path or parse_gcov_source_path(content) or "unknown.c"
    path = normalize_gcovr_file_path(raw_path, workspace_root) if workspace_root else raw_path
    cov = parse_gcov_coverage(content)
    line_entries = [
        {
            "line_number": ln,
            "function_name": "",
            "count": cov[ln],
            "branches": [],
        }
        for ln in sorted(cov.keys())
    ]
    return {
        "gcovr/format_version": "0.14",
        "files": [
            {
                "file": path,
                "lines": line_entries,
                "functions": [],
            }
        ],
    }


def convert_dir_to_tracefiles(
    gcov_dir: Path,
    out_dir: Path,
    base_name: str = "trace",
    workspace_root: Optional[Path] = None,
    only_c_sources: bool = True,
) -> Tuple[List[Path], List[Path]]:
    """
    Convert .gcov.txt in gcov_dir to .info and .json in out_dir.
    If only_c_sources is True (default), only convert files whose Source: path ends with .c.
    Returns (list of .info paths, list of .json paths).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    gcov_files = sorted(gcov_dir.glob("*.gcov.txt"))
    info_paths = []
    json_paths = []
    idx = 0
    for gcov_path in gcov_files:
        content = gcov_path.read_text(encoding="utf-8", errors="replace")
        source_path = parse_gcov_source_path(content)
        if only_c_sources and (not source_path or not source_path.endswith(".c")):
            continue
        info_content = gcov_to_lcov_info(content, source_path)
        info_path = out_dir / f"{base_name}_{idx:05d}.info"
        info_path.write_text(info_content, encoding="utf-8")
        info_paths.append(info_path)
        j = gcov_to_gcovr_json(content, source_path, workspace_root)
        json_path = out_dir / f"{base_name}_{idx:05d}.json"
        json_path.write_text(json.dumps(j), encoding="utf-8")
        json_paths.append(json_path)
        idx += 1
    return info_paths, json_paths


def merge_lcov(info_paths: List[Path], merged_path: Path) -> Optional[bool]:
    """Merge lcov .info files: lcov -a f1.info -a f2.info ... -o merged.info. Returns None if lcov not installed."""
    if not info_paths:
        return False
    args = []
    for p in info_paths:
        args.extend(["-a", str(p)])
    args.extend(["-o", str(merged_path)])
    try:
        r = subprocess.run(
            ["lcov", *args],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        return None  # lcov not installed
    if r.returncode != 0:
        sys.stderr.write(f"lcov merge failed: {r.stderr}\n")
        return False
    return True


def summary_lcov(merged_path: Path) -> bool:
    """Print lcov summary: lcov --summary merged.info."""
    r = subprocess.run(
        ["lcov", "--summary", str(merged_path)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if r.returncode != 0:
        sys.stderr.write(f"lcov summary failed: {r.stderr}\n")
        return False
    print(r.stdout)
    return True


def get_lcov_line_coverage_pct(merged_path: Path) -> Optional[float]:
    """Run lcov --summary on merged.info and return line coverage percentage, or None on failure."""
    r = subprocess.run(
        ["lcov", "--summary", str(merged_path)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if r.returncode != 0:
        return None
    m = re.search(r"lines\s*\.+:\s*([\d.]+)%", r.stdout)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def get_merged_line_coverage_pct(
    results_dirs: List[Path],
    workspace_root: Path,
    output_dir: Path,
) -> Optional[float]:
    """
    Convert results_dirs to tracefiles, merge with lcov, return line coverage percentage.
    Uses unique base names per dir so multiple dirs with the same name do not overwrite.
    """
    all_info = []
    for idx, res_dir in enumerate(results_dirs):
        if not res_dir.exists():
            continue
        res_dir = res_dir.resolve()
        base = f"{idx:04d}_{res_dir.name}".replace("-", "_")
        info_paths, _ = convert_dir_to_tracefiles(
            res_dir,
            output_dir,
            base_name=base,
            workspace_root=workspace_root,
        )
        all_info.extend(info_paths)
    if not all_info:
        return None
    merged_info = output_dir / "merged.info"
    if not merge_lcov(all_info, merged_info):
        return None
    return get_lcov_line_coverage_pct(merged_info)


def _strip_gcovr_missing_column(text: str) -> str:
    """Remove the 'Missing' column from gcovr text report (header and data lines)."""
    lines = text.split("\n")
    out = []
    for line in lines:
        stripped = line.strip()
        if "Missing" in line and stripped.startswith("File"):
            out.append(re.sub(r"\s+Missing\s*$", "", line).rstrip())
        elif re.search(r"\d+%\s+[\d,\s\-]+$", line):
            # Data/TOTAL line: drop trailing Missing column (digits, commas, ranges)
            out.append(re.sub(r"(\s+\d+%)\s+[\d,\s\-]+$", r"\1", line).rstrip())
        else:
            out.append(line)
    return "\n".join(out)


def merge_and_summary_gcovr(
    json_paths: List[Path],
    output_dir: Path,
    root: Optional[Path] = None,
    hide_missing: bool = True,
) -> bool:
    """Merge gcovr JSON tracefiles and print text summary. Skips if gcovr not installed."""
    if not json_paths:
        return False
    args = []
    for p in json_paths:
        args.extend(["--add-tracefile", str(p)])
    if root is not None:
        args.extend(["--root", str(root)])
    args.extend(["--txt", "-"])
    try:
        r = subprocess.run(
            ["gcovr", *args],
            cwd=str(output_dir),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        sys.stderr.write("gcovr not installed, skipping gcovr summary (pip install gcovr)\n")
        return True
    if r.returncode != 0:
        sys.stderr.write(f"gcovr failed: {r.stderr}\n")
        return False
    stdout = r.stdout
    if hide_missing:
        stdout = _strip_gcovr_missing_column(stdout)
    print(stdout)
    return True


def aggregate_directories(
    results_dirs: List[Path],
    workspace_root: Path,
    output_dir: Path,
    use_lcov: bool = True,
    use_gcovr: bool = True,
) -> bool:
    """
    Collect all .gcov.txt from results_dirs, convert to tracefiles, merge, print summary.
    """
    all_info = []
    all_json = []
    all_gcov_paths: List[Path] = []
    for idx, res_dir in enumerate(results_dirs):
        if not res_dir.exists():
            continue
        res_dir = res_dir.resolve()
        # Unique base so multiple dirs with same name (e.g. cat_symbolic_coverage and
        # targeted_uncovered_cat_manual/cat_symbolic_coverage) do not overwrite each other.
        base = f"{idx:04d}_{res_dir.name}".replace("-", "_")
        info_paths, json_paths = convert_dir_to_tracefiles(
            res_dir,
            output_dir,
            base_name=base,
            workspace_root=workspace_root,
            only_c_sources=True,
        )
        all_info.extend(info_paths)
        all_json.extend(json_paths)
        for p in sorted(res_dir.glob("*.gcov.txt")):
            try:
                src = parse_gcov_source_path(p.read_text(encoding="utf-8", errors="replace"))
                if src and src.endswith(".c"):
                    all_gcov_paths.append(p)
            except Exception:
                pass

    if not all_info and not all_json:
        print("No .gcov.txt files found.", file=sys.stderr)
        return False

    # Per-source: only .c files get their own directory and merged cumulative report
    by_source = group_gcov_paths_by_source(all_gcov_paths)
    for source_key, paths in sorted(by_source.items()):
        if not source_key.endswith(".c"):
            continue
        dir_name = _source_to_dirname(source_key)
        source_out_dir = output_dir / dir_name
        source_out_dir.mkdir(parents=True, exist_ok=True)
        cumulative_path = source_out_dir / "cumulative.gcov.txt"
        if write_cumulative_gcov(paths, cumulative_path):
            print(f"  {dir_name}/cumulative.gcov.txt ({len(paths)} runs)")

    # Global cumulative (all sources merged into one report) for backward compatibility
    global_cumulative_path = output_dir / "cumulative.gcov.txt"
    if write_cumulative_gcov(all_gcov_paths, global_cumulative_path):
        print(f"Global cumulative: {global_cumulative_path}")

    ok = True
    if use_lcov and all_info:
        merged_info = output_dir / "merged.info"
        lcov_result = merge_lcov(all_info, merged_info)
        if lcov_result is None:
            sys.stderr.write("lcov not installed, skipping lcov merge/summary (install lcov)\n")
        elif lcov_result:
            print("--- lcov merged summary ---")
            if not summary_lcov(merged_info):
                ok = False
        else:
            ok = False

    if use_gcovr and all_json:
        print("--- gcovr merged summary ---")
        if not merge_and_summary_gcovr(all_json, output_dir, root=workspace_root):
            ok = False

    return ok


def _source_to_dirname(source_path: str) -> str:
    """Turn gcov source path (e.g. ../src/cat.c) into a safe directory name (e.g. src_cat.c)."""
    if not source_path or source_path == "unknown.c":
        return "unknown"
    s = source_path.replace("\\", "/").strip()
    for prefix in ("../", "./"):
        if s.startswith(prefix):
            s = s[len(prefix) :]
            break
    return s.replace("/", "_").strip("_") or "unknown"


def group_gcov_paths_by_source(gcov_paths: List[Path]) -> Dict[str, List[Path]]:
    """Group .gcov.txt paths by source file (from Source: line in content). Returns dict source_key -> [paths]."""
    groups: Dict[str, List[Path]] = {}
    for p in gcov_paths:
        if not p.exists():
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
            source = parse_gcov_source_path(content) or "unknown.c"
            key = source.replace("\\", "/").strip()
            if key not in groups:
                groups[key] = []
            groups[key].append(p)
        except Exception:
            pass
    return groups


def discover_coverage_dirs(result_dir: Path, pattern: str = DEFAULT_COVERAGE_SUBDIR_GLOB) -> List[Path]:
    """Find subdirs of result_dir matching pattern (e.g. *_symbolic_coverage)."""
    if not result_dir.is_dir():
        return []
    return sorted(d for d in result_dir.glob(pattern) if d.is_dir())


class _Tee:
    """Write to both stdout and a file."""

    def __init__(self, stdout: object, file_handle: object) -> None:
        self._stdout = stdout
        self._file = file_handle

    def write(self, s: str) -> None:
        self._stdout.write(s)
        self._file.write(s)

    def flush(self) -> None:
        self._stdout.flush()
        self._file.flush()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert .gcov.txt to lcov/gcovr tracefiles, merge, and print total coverage."
    )
    parser.add_argument(
        "results_dirs",
        nargs="*",
        default=None,
        help="Directories containing .gcov.txt (default: auto-discover result/llm/*_symbolic_coverage)",
    )
    parser.add_argument(
        "--result-dir",
        type=str,
        default=None,
        help=f"Parent result directory to search for *_symbolic_coverage (default: result/llm)",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=str,
        default=None,
        help=f"Where to write tracefiles and merged output (default: result/llm/merged-coverage)",
    )
    parser.add_argument(
        "--no-lcov",
        action="store_true",
        help="Skip lcov merge/summary (only use gcovr)",
    )
    parser.add_argument(
        "--no-gcovr",
        action="store_true",
        help="Skip gcovr merge/summary (only use lcov)",
    )
    args = parser.parse_args()

    workspace_root = WORKSPACE_ROOT
    result_dir = workspace_root / args.result_dir if args.result_dir else DEFAULT_RESULT_DIR
    output_dir = workspace_root / args.output_dir if args.output_dir else DEFAULT_OUTPUT_DIR

    if args.results_dirs:
        results_dirs = [workspace_root / d for d in args.results_dirs]
    else:
        results_dirs = discover_coverage_dirs(result_dir)
        if not results_dirs:
            print(f"No *_symbolic_coverage dirs found under {result_dir}", file=sys.stderr)
            print("Run run_symbolic_executor.py first to generate result/llm/<util>_symbolic_coverage/", file=sys.stderr)
            sys.exit(1)

    summary_path = output_dir.parent / "merged_summary.txt"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    real_stdout = sys.stdout
    ok = False
    with open(summary_path, "w", encoding="utf-8") as summary_file:
        sys.stdout = _Tee(real_stdout, summary_file)
        try:
            print(f"Using coverage dirs: {[str(d.relative_to(workspace_root)) for d in results_dirs]}")
            ok = aggregate_directories(
                results_dirs,
                workspace_root,
                output_dir,
                use_lcov=not args.no_lcov,
                use_gcovr=not args.no_gcovr,
            )
            if ok:
                print(f"Tracefiles and merged output in: {output_dir}")
            print(f"Merged summary saved to: {summary_path}")
        finally:
            summary_file.flush()
            sys.stdout = real_stdout
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
