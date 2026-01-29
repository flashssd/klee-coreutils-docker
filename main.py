#!/usr/bin/env python3
"""
Main script to:
1. Read each coverage file from results/klee-result
2. Send coverage + prompt to OpenRouter LLM
3. Use LLM response as input to run-cat-coverage.sh
4. Compare generated coverage with original
"""

import os
import sys
import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import Tuple, Optional
import re

from prompt import prompt as base_prompt
from openrouter_client import OpenRouterClient


def clean_llm_response(response: str) -> str:
    """
    Strip markdown code fences and extra text from LLM response.
    Returns the cleaned argument to pass to cat (first line of content, or empty string).
    """
    if not response or not response.strip():
        return ""
    s = response.strip()
    # Remove markdown code block fences (``` at start/end)
    if s.startswith("```"):
        lines = s.split("\n")
        # Remove first line (``` or ```bash etc.)
        lines = lines[1:]
        # Remove trailing ``` if present
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        s = "\n".join(lines)
    s = s.strip()
    # For cat we pass a single argument; take first line if multi-line
    first_line = s.split("\n")[0].strip()
    return first_line


def read_coverage_file(filepath: Path) -> str:
    """Read a coverage file and return its content."""
    with open(filepath, 'r', encoding='utf-8') as f:
        return f.read()


def extract_coverage_lines(content: str) -> dict:
    """
    Extract coverage information from gcov file.
    Returns a dict mapping line numbers to their coverage status:
    - None for non-executable lines (-)
    - Execution count for covered lines (int)
    - '#####' for uncovered executable lines
    """
    coverage = {}
    for line in content.split('\n'):
        # Match gcov format: "        -:   50:..." or "        5:   100:..." or "    #####:   200:..."
        match = re.match(r'^\s*([-0-9#]+):\s*(\d+):', line)
        if match:
            coverage_str = match.group(1).strip()
            line_num = int(match.group(2))
            
            if coverage_str == '-':
                coverage[line_num] = None  # Non-executable
            elif coverage_str == '#####':
                coverage[line_num] = '#####'  # Uncovered executable
            else:
                try:
                    coverage[line_num] = int(coverage_str)  # Execution count
                except ValueError:
                    coverage[line_num] = coverage_str
    return coverage


def compare_coverage(original: dict, generated: dict) -> Tuple[bool, dict]:
    """
    Compare two coverage dictionaries.
    Returns (match, stats) where stats contains comparison details.
    """
    all_lines = set(original.keys()) | set(generated.keys())
    matches = 0
    mismatches = 0
    missing_in_generated = 0
    extra_in_generated = 0
    
    mismatch_details = []
    
    for line_num in sorted(all_lines):
        orig_val = original.get(line_num)
        gen_val = generated.get(line_num)
        
        if line_num not in original:
            extra_in_generated += 1
            continue
        if line_num not in generated:
            missing_in_generated += 1
            mismatch_details.append(f"Line {line_num}: missing in generated (original: {orig_val})")
            continue
        
        if orig_val == gen_val:
            matches += 1
        else:
            mismatches += 1
            mismatch_details.append(f"Line {line_num}: original={orig_val}, generated={gen_val}")
    
    stats = {
        'matches': matches,
        'mismatches': mismatches,
        'missing_in_generated': missing_in_generated,
        'extra_in_generated': extra_in_generated,
        'mismatch_details': mismatch_details[:10]  # Limit to first 10
    }
    
    return (mismatches == 0 and missing_in_generated == 0, stats)


# Max length for arg_safe in filenames (must match run-cat-coverage.sh)
ARG_SAFE_MAX_LEN = 80


def run_coverage_script(test_arg: str, results_dir: Path, workspace_root: Optional[Path] = None) -> Optional[Path]:
    """
    Run run-cat-coverage.sh with a single test argument.
    Writes coverage output to results_dir (e.g. results/openrouter-result).
    Returns the path to the generated coverage file, or None if failed.
    """
    workspace_root = workspace_root or Path(__file__).parent
    # Create temporary ktest-arguments.txt with the test argument
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write(test_arg + '\n')
        temp_ktest_file = f.name
    
    try:
        # Run the coverage script; RESULTS_DIR must be absolute (script cds to obj-gcov/src)
        script_path = Path(__file__).parent / "run-cat-coverage.sh"
        results_dir_abs = results_dir.resolve()
        env = {
            **os.environ,
            'KTEST_ARGS_FILE': temp_ktest_file,
            'WORKSPACE_ROOT': str(workspace_root.resolve()),
            'RESULTS_DIR': str(results_dir_abs),
        }
        result = subprocess.run(
            ['bash', str(script_path)],
            env=env,
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode != 0:
            print(f"  ✗ Coverage script failed: {result.stderr}", file=sys.stderr)
            return None
        
        # Find the generated coverage file (script truncates arg_safe to ARG_SAFE_MAX_LEN)
        arg_safe = re.sub(r'[^a-zA-Z0-9._-]', '_', test_arg)
        arg_safe = re.sub(r'_+', '_', arg_safe)
        if not arg_safe:
            arg_safe = "empty"
        arg_safe = arg_safe[:ARG_SAFE_MAX_LEN]
        
        pattern = f"cat_test000001_arg_{arg_safe}.gcov.txt"
        generated_file = results_dir_abs / pattern
        
        if not generated_file.exists():
            # Try to find any file starting with cat_test000001 (e.g. if truncation differed)
            candidates = list(results_dir_abs.glob("cat_test000001_arg_*.gcov.txt"))
            if candidates:
                generated_file = candidates[0]
            else:
                print(f"  ✗ Generated coverage file not found (expected: {pattern})", file=sys.stderr)
                return None
        
        return generated_file
        
    finally:
        # Clean up temp file
        if os.path.exists(temp_ktest_file):
            os.unlink(temp_ktest_file)


def process_coverage_file(
    coverage_file: Path,
    client: OpenRouterClient,
    klee_results_dir: Path,
    llm_results_dir: Path,
    verbose: bool = False
) -> Tuple[bool, dict]:
    """
    Process a single coverage file:
    1. Read coverage file
    2. Send to LLM with prompt
    3. Run coverage script with LLM response
    4. Compare results
    
    Returns (success, stats)
    """
    print(f"\nProcessing: {coverage_file.name}")
    
    # Read original coverage
    print(f"  Reading coverage file ({coverage_file.stat().st_size} bytes)...")
    sys.stdout.flush()
    original_content = read_coverage_file(coverage_file)
    original_coverage = extract_coverage_lines(original_content)
    
    print(f"  ✓ Parsed {len(original_coverage)} coverage lines")
    if verbose:
        print(f"  Original coverage: {len(original_coverage)} lines")
    
    # Create prompt with coverage file content
    full_prompt = original_content + "\n\n" + base_prompt
    
    # Get LLM response
    print("  Sending to LLM (this may take a while)...")
    sys.stdout.flush()
    
    try:
        # Increase timeout for large prompts
        prompt_size = len(full_prompt)
        timeout = max(120.0, prompt_size / 100)  # At least 120s, more for larger prompts
        print(f"  Prompt size: {prompt_size} characters, timeout: {timeout:.1f}s")
        sys.stdout.flush()
        
        llm_response = client.chat(full_prompt, max_tokens=256, temperature=0.3, timeout=timeout)
        llm_response = llm_response.strip()
        
        # Strip markdown code fences (```...```) and take the actual input
        llm_response = clean_llm_response(llm_response)
        
        print(f"  ✓ Received LLM response: {repr(llm_response)}")
        if verbose:
            print(f"  Full LLM response: {repr(llm_response)}")
    except Exception as e:
        print(f"  ✗ LLM call failed: {e}", file=sys.stderr)
        return False, {'error': str(e)}
    
    # Run coverage script with LLM response as argument
    print(f"  Running coverage script with argument: {repr(llm_response)}")
    sys.stdout.flush()
    
    generated_file = run_coverage_script(llm_response, llm_results_dir)
    
    if generated_file is None:
        return False, {'error': 'Coverage script failed'}
    
    # Read generated coverage
    generated_content = read_coverage_file(generated_file)
    generated_coverage = extract_coverage_lines(generated_content)
    
    if verbose:
        print(f"  Generated coverage: {len(generated_coverage)} lines")
    
    # Compare coverages
    match, stats = compare_coverage(original_coverage, generated_coverage)
    
    if match:
        print(f"  ✓ Coverage matches!")
    else:
        print(f"  ✗ Coverage mismatch:")
        print(f"    Matches: {stats['matches']}, Mismatches: {stats['mismatches']}")
        if stats['missing_in_generated'] > 0:
            print(f"    Missing in generated: {stats['missing_in_generated']}")
        if stats['extra_in_generated'] > 0:
            print(f"    Extra in generated: {stats['extra_in_generated']}")
        if stats['mismatch_details']:
            print(f"    First mismatches:")
            for detail in stats['mismatch_details']:
                print(f"      {detail}")
    
    stats['llm_response'] = llm_response
    stats['generated_file'] = str(generated_file)
    stats['match'] = match
    
    return match, stats


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Process coverage files with LLM and compare results'
    )
    parser.add_argument(
        '--api-key',
        type=str,
        help='OpenRouter API key (or set OPENROUTER_API_KEY env var)'
    )
    parser.add_argument(
        '--model',
        type=str,
        default='qwen/qwen-2.5-72b-instruct',
        help='Model to use (default: qwen/qwen-2.5-72b-instruct)'
    )
    parser.add_argument(
        '--results-dir',
        type=str,
        default='results/klee-result',
        help='Directory containing coverage files (default: results/klee-result)'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Verbose output'
    )
    parser.add_argument(
        '--limit',
        type=int,
        help='Limit number of files to process'
    )
    parser.add_argument(
        '--llm-name',
        type=str,
        default='openrouter',
        help='Name of LLM (output saved to results/<llm-name>-result, default: openrouter)'
    )
    
    args = parser.parse_args()
    
    # Initialize OpenRouter client
    try:
        client = OpenRouterClient(api_key=args.api_key, model=args.model)
    except Exception as e:
        print(f"Error initializing OpenRouter client: {e}", file=sys.stderr)
        sys.exit(1)
    
    # Find all coverage files (KLEE originals)
    klee_results_dir = Path(args.results_dir)
    if not klee_results_dir.exists():
        print(f"Error: Results directory not found: {klee_results_dir}", file=sys.stderr)
        sys.exit(1)
    
    # LLM coverage output directory: results/<llm_name>-result
    llm_results_dir = klee_results_dir.parent / f"{args.llm_name}-result"
    llm_results_dir.mkdir(parents=True, exist_ok=True)
    print(f"LLM coverage output: {llm_results_dir}")
    
    coverage_files = sorted(klee_results_dir.glob("*.gcov.txt"))
    
    if not coverage_files:
        print(f"No coverage files found in {klee_results_dir}", file=sys.stderr)
        sys.exit(1)
    
    if args.limit:
        coverage_files = coverage_files[:args.limit]
    
    print(f"Found {len(coverage_files)} coverage files to process")
    
    # Process each file
    results = []
    for coverage_file in coverage_files:
        success, stats = process_coverage_file(
            coverage_file,
            client,
            klee_results_dir,
            llm_results_dir,
            verbose=args.verbose
        )
        results.append({
            'file': coverage_file.name,
            'success': success,
            'stats': stats
        })
    
    # Summary
    print("\n" + "="*60)
    print("Summary:")
    print("="*60)
    
    successful = sum(1 for r in results if r['success'])
    total = len(results)
    
    print(f"Total files processed: {total}")
    print(f"Successful matches: {successful}")
    print(f"Failed matches: {total - successful}")
    print(f"Success rate: {successful/total*100:.1f}%")
    
    if successful < total:
        print("\nFailed files:")
        for r in results:
            if not r['success']:
                print(f"  - {r['file']}: {r['stats'].get('error', 'Coverage mismatch')}")


if __name__ == "__main__":
    main()
