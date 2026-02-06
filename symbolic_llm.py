"""
LLM-related logic for the symbolic executor: build prompt, call OpenAI, parse response into inputs.
Used by run_symbolic_executor.py; no file I/O or coverage logic here.
"""

import ast
import json
from typing import List

from prompt import prompt_symbolic_executor
from openai_client import OpenAIClient


def parse_response_list(response: str) -> List[str]:
    """
    Parse LLM response into a list of inputs (strings for utility arguments).
    Handles JSON list, Python literal list, and strips markdown.
    """
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


def get_inputs_for_program(
    program_content: str,
    client: OpenAIClient,
    *,
    max_tokens: int = 1024,
    temperature: float = 0.3,
    timeout: float = 300.0,
) -> List[str]:
    """
    Send program source + symbolic-executor prompt to the LLM; return parsed list of inputs.
    """
    full_prompt = program_content + "\n\n" + prompt_symbolic_executor
    response = client.chat(
        full_prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
    )
    return parse_response_list((response or "").strip())


if __name__ == "__main__":
    import argparse
    import sys
    from pathlib import Path

    _script_dir = Path(__file__).parent.resolve()
    _src_dir = _script_dir / "coreutils/coreutils-8.32/src"
    _out_dir = _script_dir / "result" / "llm"

    parser = argparse.ArgumentParser(
        description="Generate LLM input lists for coreutils .c sources; save to result/llm/<stem>_inputs.json"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=2,
        help="Max number of .c files to process (default: 2); use 0 for all",
    )
    args = parser.parse_args()

    if not _src_dir.is_dir():
        print(f"Source dir not found: {_src_dir}", file=sys.stderr)
        sys.exit(1)

    c_files = sorted(_src_dir.rglob("*.c"))
    if args.limit and args.limit > 0:
        c_files = c_files[: args.limit]
    if not c_files:
        print("No .c files found.", file=sys.stderr)
        sys.exit(1)

    try:
        client = OpenAIClient()
    except Exception as e:
        print(f"OpenAI client error: {e}", file=sys.stderr)
        sys.exit(1)

    _out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Processing {len(c_files)} .c file(s). Output -> {_out_dir}/")

    for path in c_files:
        program_name = path.stem
        rel = path.relative_to(_src_dir)
        print(f"=== {rel} ===")
        try:
            program_content = path.read_text(encoding="utf-8", errors="replace")
            inputs_list = get_inputs_for_program(program_content, client)
        except Exception as e:
            print(f"  Error: {e}", file=sys.stderr)
            continue
        out_path = _out_dir / f"{program_name}_inputs.json"
        out_path.write_text(json.dumps(inputs_list, indent=2), encoding="utf-8")
        print(f"  Parsed {len(inputs_list)} input(s) -> {out_path.name}")

    print("Done.")
