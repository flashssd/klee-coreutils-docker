#!/usr/bin/env python3
"""
Generate targeted inputs using LLM: read cumulative coverage + program source,
call LLM with prompt_target_uncovered, and output list of inputs (JSON) to stdout or file.
"""

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Optional

from prompt import prompt_target_uncovered
from openai_client import OpenAIClient
from run_symbolic_executor import UTIL_TO_REPORT_NAME, get_programs_from_result_llm


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
    # Allow program as bare name e.g. md5sum.c
    for base in ("coreutils/coreutils-8.32/src", "coreutils/coreutils-6.11/src"):
        candidate = workspace_root / base / p.name
        if candidate.exists():
            return candidate
    return (workspace_root / program).resolve()


def source_stem_to_util(stem: str) -> str:
    """Derive executable/util name from source file stem (e.g. lbracket -> '[', md5sum -> md5sum)."""
    return REPORT_NAME_TO_UTIL.get(stem, stem)


def read_program(program_path: Path) -> str:
    """Read program source."""
    return program_path.read_text(encoding="utf-8", errors="replace")


def parse_response_list(response: str) -> list:
    """Parse LLM response into a list of inputs (strings for utility arguments)."""
    s = (response or "").strip()
    if s.startswith("```"):
        lines = s.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        s = "\n".join(lines)
    s = s.strip()
    try:
        out = json.loads(s)
        if isinstance(out, list):
            return [str(x) for x in out]
    except json.JSONDecodeError:
        pass
    try:
        out = ast.literal_eval(s)
        if isinstance(out, list):
            return [str(x) for x in out]
    except (ValueError, SyntaxError):
        pass
    if s:
        return [s]
    return []


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate inputs targeting uncovered lines (LLM only)"
    )
    parser.add_argument(
        "--cumulative",
        type=str,
        default=None,
        help="Path to cumulative coverage report (e.g. result/llm/merged-coverage/src_md5sum.c/cumulative.gcov.txt). If omitted, uses --function-name.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Process all programs that have result/llm/*_inputs.json (from symbolic_llm.py). Ignores --cumulative/--function-name.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="When using --all, max number of programs to process (default: 0 = all).",
    )
    parser.add_argument(
        "--function-name",
        type=str,
        default=None,
        help="Function/source name to build cumulative path (e.g. md5sum). Used only when not --all.",
    )
    parser.add_argument(
        "--program",
        type=str,
        default=None,
        help="Program source file (e.g. md5sum.c). Default: inferred from --cumulative or --function-name.",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        help="OpenAI API key (or set OPENAI_API_KEY)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="gpt-4o-mini",
        help="OpenAI model (default: gpt-4o-mini)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=4096,
        help="Max completion tokens (default: 4096)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="File to write generated inputs (JSON format). Default: result/llm/{util}_targeted_inputs.json.",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print inputs to stdout instead of saving to a file.",
    )
    args = parser.parse_args()

    workspace_root = WORKSPACE_ROOT
    results_dir = DEFAULT_RESULT_DIR

    # --- Batch mode: discover programs from result/llm/*_inputs.json (from symbolic_llm.py) ---
    if args.all:
        programs = [
            (r, u) for r, u in get_programs_from_result_llm(results_dir)
            if not r.endswith("_targeted")  # only base *_inputs.json, not *_targeted_inputs.json
        ]
        if not programs:
            print("No *_inputs.json found in result/llm. Run symbolic_llm.py first.", file=sys.stderr)
            sys.exit(1)
        if args.limit and args.limit > 0:
            programs = programs[: args.limit]
        try:
            client = OpenAIClient(api_key=args.api_key, model=args.model)
        except Exception as e:
            print(f"OpenAI client error: {e}", file=sys.stderr)
            sys.exit(1)
        results_dir.mkdir(parents=True, exist_ok=True)
        for report_name, util_name in programs:
            cumulative_path = DEFAULT_MERGED_COVERAGE / f"src_{report_name}.c" / "cumulative.gcov.txt"
            if not cumulative_path.exists():
                print(f"Skipping {report_name} (no cumulative: {cumulative_path.relative_to(workspace_root)})", file=sys.stderr)
                continue
            program_path = resolve_program_path(f"{report_name}.c", workspace_root)
            if not program_path.exists():
                print(f"Skipping {report_name} (program not found: {program_path})", file=sys.stderr)
                continue
            print(f"=== {report_name} ===", file=sys.stderr)
            cumulative_content = cumulative_path.read_text(encoding="utf-8", errors="replace")
            program_content = read_program(program_path)
            full_prompt = program_content + "\n\n" + cumulative_content + "\n\n" + prompt_target_uncovered
            try:
                response = client.chat(
                    full_prompt,
                    max_tokens=args.max_tokens,
                    temperature=0.3,
                    timeout=300.0,
                )
            except Exception as e:
                print(f"  OpenAI error: {e}", file=sys.stderr)
                continue
            response = (response or "").strip()
            inputs_list = parse_response_list(response)
            if not inputs_list:
                print(f"  No parseable inputs.", file=sys.stderr)
                continue
            out_path = results_dir / f"{report_name}_targeted_inputs.json"
            out_path.write_text(json.dumps(inputs_list, indent=2), encoding="utf-8")
            print(f"  Saved {len(inputs_list)} inputs -> {out_path.name}", file=sys.stderr)
        print("Done.", file=sys.stderr)
        return

    # --- Single-program mode ---
    # Resolve cumulative path
    if args.cumulative:
        cumulative_path = (workspace_root / args.cumulative) if not Path(args.cumulative).is_absolute() else Path(args.cumulative)
    elif args.function_name:
        func = args.function_name.strip()
        if not func:
            print("Error: --function-name must be non-empty.", file=sys.stderr)
            sys.exit(1)
        cumulative_path = DEFAULT_MERGED_COVERAGE / f"src_{func}.c" / "cumulative.gcov.txt"
    else:
        print("Error: provide --all, or --cumulative, or --function-name.", file=sys.stderr)
        sys.exit(1)

    if not cumulative_path.exists():
        print(f"Error: cumulative report not found: {cumulative_path}", file=sys.stderr)
        sys.exit(1)

    cumulative_content = cumulative_path.read_text(encoding="utf-8", errors="replace")

    # Resolve program path
    if args.program:
        program_path = resolve_program_path(args.program, workspace_root)
    elif args.function_name:
        program_path = resolve_program_path(f"{args.function_name}.c", workspace_root)
    else:
        # Infer from cumulative path: result/llm/merged-coverage/src_md5sum.c -> md5sum.c
        parent_name = cumulative_path.parent.name
        if parent_name.startswith("src_") and parent_name.endswith(".c"):
            stem = parent_name[4:-2]
            program_path = resolve_program_path(f"{stem}.c", workspace_root)
        else:
            print("Error: cannot infer program; pass --program or --function-name.", file=sys.stderr)
            sys.exit(1)

    if not program_path.exists():
        print(f"Error: program not found: {program_path}", file=sys.stderr)
        sys.exit(1)

    util_name = source_stem_to_util(program_path.stem)
    program_content = read_program(program_path)
    full_prompt = program_content + "\n\n" + cumulative_content + "\n\n" + prompt_target_uncovered

    print(f"Program: {program_path} (util: {util_name})", file=sys.stderr)
    print(f"Cumulative report: {cumulative_path}", file=sys.stderr)
    print("Calling OpenAI...", file=sys.stderr)
    try:
        client = OpenAIClient(api_key=args.api_key, model=args.model)
        response = client.chat(
            full_prompt,
            max_tokens=args.max_tokens,
            temperature=0.3,
            timeout=300.0,
        )
    except Exception as e:
        print(f"OpenAI API error: {e}", file=sys.stderr)
        sys.exit(1)

    response = (response or "").strip()
    inputs_list = parse_response_list(response)
    
    if not response:
        print("Error: LLM returned an empty response.", file=sys.stderr)
        sys.exit(1)
    if len(inputs_list) == 0:
        print("Error: LLM returned no parseable input list.", file=sys.stderr)
        sys.exit(1)
        
    print(f"Generated {len(inputs_list)} inputs.", file=sys.stderr)
    
    # Output
    output_str = json.dumps(inputs_list, indent=2)
    
    if args.stdout:
        print(output_str)
        return

    if args.output:
        out_path = Path(args.output)
    else:
        # Default: result/llm/{util}_targeted_inputs.json
        out_path = DEFAULT_RESULT_DIR / f"{util_name}_targeted_inputs.json"
    
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(output_str)
    print(f"Inputs saved to: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
