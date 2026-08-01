"""
bench_all.py -- sequential multi-model runner for ollama-bench
Runs bench.py once per model, waits between runs so RAM can free up,
then merges every individual result into a single combined JSON.

Usage:
    python bench_all.py
    python bench_all.py --models phi3 mistral:7b
    python bench_all.py --delay 20
    python bench_all.py --prompt-ids commit_msg_simple code_python_function
    python bench_all.py --list-prompts

Output:
    results/<YYYYMMDD_HHMMSS>_bench_all.json   (combined)
    results/<timestamp>_bench.json              (one per model, created by bench.py)

Why sequential?
    llama-swap unloads the previous model before loading the next one.
    A short delay between models lets RAM settle so the PC stays responsive.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
BENCH_PY      = Path(__file__).parent / "bench.py"
RESULTS_DIR   = Path(__file__).parent / "results"
DEFAULT_MODELS = [
    "phi3",
    "deepseek-coder:6.7b-instruct-q4_K_M",
    "llama3.1:8b",
    "mistral:7b",
    "qwen3:8b",
    "phi4-mini",
    "qwen3:4b",
]
DEFAULT_DELAY  = 15   # seconds between models


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def find_latest_result(before_ts: float) -> Path | None:
    """Return the newest .json in results/ created after before_ts."""
    candidates = []
    for p in RESULTS_DIR.glob("*_bench.json"):
        if p.stat().st_mtime > before_ts:
            candidates.append(p)
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def merge_results(per_model_files: list[dict]) -> dict:
    """Merge per-model result JSONs into one combined structure."""
    if not per_model_files:
        return {}

    first = per_model_files[0]["data"]
    combined = {
        "timestamp":    datetime.now().isoformat(),
        "runner":       "bench_all.py",
        "llm_server":   first.get("llm_server", ""),
        "llm_base_url": first.get("llm_base_url", ""),
        "system":       first.get("system", {}),
        "models":       [e["model"] for e in per_model_files],
        "prompt_file":  first.get("prompt_file", ""),
        "per_model_files": [e["file"] for e in per_model_files],
        "results":      [],
    }
    for entry in per_model_files:
        combined["results"].extend(entry["data"].get("results", []))

    return combined


def print_summary(combined: dict) -> None:
    """Print a compact leaderboard sorted by avg tok/s."""
    rows = []
    for r in combined.get("results", []):
        s = r.get("summary", {})
        tps = s.get("avg_tokens_per_second")
        dur = s.get("avg_total_duration_s")
        con = s.get("consistency_score")
        rows.append((r["model"], r["prompt_id"], tps, dur, con))

    if not rows:
        return

    print("\n" + "=" * 72)
    print("  COMBINED SUMMARY")
    print("=" * 72)
    print(f"  {'model':<38} {'prompt':<28} {'tok/s':>7}  {'dur':>6}  {'cons':>5}")
    print("  " + "-" * 70)

    rows.sort(key=lambda x: (x[0], x[2] or 0), reverse=False)
    for model, pid, tps, dur, con in rows:
        tps_s = f"{tps:>7.2f}" if tps is not None else "      -"
        dur_s = f"{dur:>6.2f}" if dur is not None else "     -"
        con_s = f"{con:>5.2f}" if con is not None else "    -"
        print(f"  {model:<38} {pid:<28} {tps_s}  {dur_s}  {con_s}")

    print()

    # Best tok/s per prompt
    from collections import defaultdict
    by_prompt: dict[str, list] = defaultdict(list)
    for model, pid, tps, dur, con in rows:
        if tps is not None:
            by_prompt[pid].append((tps, model))

    print("  FASTEST MODEL PER PROMPT")
    print("  " + "-" * 50)
    for pid, entries in sorted(by_prompt.items()):
        best_tps, best_model = max(entries, key=lambda x: x[0])
        print(f"  {pid:<32}  {best_model}  ({best_tps:.2f} tok/s)")
    print()


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="bench_all.py -- run ollama-bench one model at a time and merge results"
    )
    parser.add_argument("--models",      nargs="+", default=DEFAULT_MODELS, help="Models to benchmark")
    parser.add_argument("--delay",       type=int,  default=DEFAULT_DELAY,  help="Seconds to wait between models (default: 15)")
    parser.add_argument("--prompt-ids",  nargs="+", default=[],             help="Run only specific prompt IDs")
    parser.add_argument("--prompts",     default="",                        help="Custom bench_prompts.json path")
    parser.add_argument("--list-prompts",action="store_true",               help="List available prompts and exit")
    args = parser.parse_args()

    # Pass --list-prompts straight through to bench.py
    if args.list_prompts:
        subprocess.run([sys.executable, str(BENCH_PY), "--list-prompts"])
        return

    RESULTS_DIR.mkdir(exist_ok=True)

    total   = len(args.models)
    session = datetime.now().strftime("%Y%m%d_%H%M%S")
    per_model_files: list[dict] = []

    print(f"\nbench_all.py  —  {total} model(s), {args.delay}s delay between runs")
    print(f"  Models:  {', '.join(args.models)}")
    if args.prompt_ids:
        print(f"  Prompts: {', '.join(args.prompt_ids)}")
    print(f"  NOTE:    llama-swap must be running on :8081 before starting.")
    print(f"           Run 'panda llm start' if not already up.")
    print()

    for idx, model in enumerate(args.models, start=1):
        print(f"{'=' * 60}")
        print(f"  [{idx}/{total}]  {model}")
        print(f"{'=' * 60}")

        cmd = [sys.executable, str(BENCH_PY), "--models", model]
        if args.prompt_ids:
            cmd += ["--prompt-ids"] + args.prompt_ids
        if args.prompts:
            cmd += ["--prompts", args.prompts]

        ts_before = time.time()
        result = subprocess.run(cmd, text=True)

        if result.returncode != 0:
            print(f"[WARN] bench.py exited with code {result.returncode} for model '{model}'. Skipping.")
        else:
            result_file = find_latest_result(ts_before)
            if result_file:
                with open(result_file, encoding="utf-8") as f:
                    data = json.load(f)
                per_model_files.append({
                    "model": model,
                    "file":  str(result_file.name),
                    "data":  data,
                })
                print(f"  [saved] {result_file.name}")
            else:
                print(f"[WARN] Could not locate result file for '{model}'.")

        if idx < total:
            print(f"\n  Waiting {args.delay}s before next model...\n")
            time.sleep(args.delay)

    if not per_model_files:
        print("\n[ERROR] No results collected. Check that llama-swap is running.")
        sys.exit(1)

    # Merge and save combined
    combined     = merge_results(per_model_files)
    out_path     = RESULTS_DIR / f"{session}_bench_all.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(combined, f, indent=2, ensure_ascii=False)

    print_summary(combined)
    print(f"Combined results saved: {out_path}")


if __name__ == "__main__":
    main()
