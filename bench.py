"""
bench.py -- ollama-bench MVP
Measures latency, RAM/VRAM usage and consistency for Ollama models.

Usage:
    python bench.py
    python bench.py --models phi3 deepseek-coder:6.7b-instruct-q4_K_M
    python bench.py --models phi3 --prompts bench_prompts.json
    python bench.py --list-prompts

Output:
    results/<YYYYMMDD_HHMMSS>_bench.json
"""

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime

# -------------------------------------------------
# CONFIG
# -------------------------------------------------
OLLAMA_BASE_URL   = "http://localhost:11434"
DEFAULT_PROMPTS   = os.path.join(os.path.dirname(__file__), "bench_prompts.json")
RESULTS_DIR       = os.path.join(os.path.dirname(__file__), "results")
DEFAULT_MODELS    = ["phi3", "deepseek-coder:6.7b-instruct-q4_K_M", "llama3.1:8b", "mistral:7b"]


# -------------------------------------------------
# OLLAMA
# -------------------------------------------------

def ollama_generate(model: str, system: str, prompt: str) -> dict:
    """
    Call Ollama /api/generate (stream=false).
    Returns the full response dict including timing fields:
      eval_count, eval_duration, prompt_eval_duration, total_duration
    """
    full_prompt = f"{system.strip()}\n\n{prompt.strip()}" if system.strip() else prompt.strip()
    payload = json.dumps({
        "model":  model,
        "prompt": full_prompt,
        "stream": False,
        "options": {"temperature": 0.2, "top_p": 0.9, "num_predict": 512},
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{OLLAMA_BASE_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=None) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ollama_version() -> str:
    try:
        req = urllib.request.Request(f"{OLLAMA_BASE_URL}/api/version", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read()).get("version", "unknown")
    except Exception:
        return "unknown"


def ollama_is_online() -> bool:
    try:
        req = urllib.request.Request(f"{OLLAMA_BASE_URL}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


# -------------------------------------------------
# SYSTEM METRICS
# -------------------------------------------------

def get_ram_used_mb() -> float | None:
    """Returns used RAM in MB using wmic (Windows) or /proc/meminfo (Linux)."""
    try:
        if platform.system() == "Windows":
            result = subprocess.run(
                ["wmic", "OS", "get", "FreePhysicalMemory,TotalVisibleMemorySize", "/Value"],
                capture_output=True, text=True, timeout=5,
            )
            values = {}
            for line in result.stdout.splitlines():
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    if v.strip().isdigit():
                        values[k.strip()] = int(v.strip())
            total = values.get("TotalVisibleMemorySize", 0)
            free  = values.get("FreePhysicalMemory", 0)
            if total:
                return round((total - free) / 1024, 1)
        else:
            with open("/proc/meminfo") as f:
                lines = f.readlines()
            mem = {}
            for line in lines:
                parts = line.split()
                mem[parts[0].rstrip(":")] = int(parts[1])
            total    = mem.get("MemTotal", 0)
            free     = mem.get("MemFree", 0)
            buffers  = mem.get("Buffers", 0)
            cached   = mem.get("Cached", 0)
            used_kb  = total - free - buffers - cached
            return round(used_kb / 1024, 1)
    except Exception:
        return None


def get_vram_used_mb() -> float | None:
    """Returns used VRAM in MB via nvidia-smi. Returns None if unavailable."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            val = result.stdout.strip().splitlines()[0].strip()
            return float(val)
    except Exception:
        pass
    return None


def get_system_info() -> dict:
    """Collect static system information once at startup."""
    info = {
        "os":       platform.system(),
        "os_ver":   platform.version(),
        "cpu":      platform.processor() or platform.machine(),
        "python":   platform.python_version(),
        "ram_total_mb": None,
        "vram_total_mb": None,
    }
    # RAM total
    try:
        if platform.system() == "Windows":
            result = subprocess.run(
                ["wmic", "OS", "get", "TotalVisibleMemorySize", "/Value"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                if "TotalVisibleMemorySize=" in line:
                    val = line.split("=")[1].strip()
                    if val.isdigit():
                        info["ram_total_mb"] = round(int(val) / 1024, 1)
        else:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        info["ram_total_mb"] = round(int(line.split()[1]) / 1024, 1)
    except Exception:
        pass

    # VRAM total
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            val = result.stdout.strip().splitlines()[0].strip()
            info["vram_total_mb"] = float(val)
    except Exception:
        pass

    return info


# -------------------------------------------------
# CONSISTENCY
# -------------------------------------------------

def consistency_score(outputs: list[str]) -> float:
    """
    Returns a score from 0.0 to 1.0.
    1.0 = all outputs identical.
    Lower = more variation across runs.
    Formula: most_common_count / total_runs
    """
    if len(outputs) <= 1:
        return 1.0
    counts = Counter(outputs)
    most_common = counts.most_common(1)[0][1]
    return round(most_common / len(outputs), 4)


# -------------------------------------------------
# BENCHMARK CORE
# -------------------------------------------------

def run_prompt(model: str, prompt_def: dict) -> dict:
    """
    Run a single prompt definition against a model N times.
    Returns a result dict with per-run data and a summary.
    """
    n_runs    = prompt_def.get("consistency_runs", 3)
    system    = prompt_def.get("system", "")
    prompt    = prompt_def["prompt"]
    prompt_id = prompt_def["id"]

    runs    = []
    outputs = []

    for i in range(n_runs):
        label = f"    run {i + 1}/{n_runs}"
        print(label, end="", flush=True)

        ram_before  = get_ram_used_mb()
        vram_before = get_vram_used_mb()

        try:
            resp = ollama_generate(model, system, prompt)
        except Exception as e:
            print(f"  ERROR: {e}")
            runs.append({"run": i + 1, "error": str(e)})
            continue

        ram_after  = get_ram_used_mb()
        vram_after = get_vram_used_mb()

        # Timing (Ollama returns nanoseconds)
        eval_count            = resp.get("eval_count", 0)
        eval_duration_ns      = resp.get("eval_duration", 0)
        prompt_eval_dur_ns    = resp.get("prompt_eval_duration", 0)
        total_duration_ns     = resp.get("total_duration", 0)

        tokens_per_second = (
            round(eval_count / (eval_duration_ns / 1e9), 2)
            if eval_duration_ns > 0 else None
        )
        time_to_first_token_s = round(prompt_eval_dur_ns / 1e9, 4) if prompt_eval_dur_ns else None
        total_duration_s      = round(total_duration_ns  / 1e9, 4) if total_duration_ns  else None

        output_text = resp.get("response", "").strip()
        outputs.append(output_text)

        run_data = {
            "run":                    i + 1,
            "tokens_generated":       eval_count,
            "tokens_per_second":      tokens_per_second,
            "time_to_first_token_s":  time_to_first_token_s,
            "total_duration_s":       total_duration_s,
            "ram_used_mb_before":     ram_before,
            "ram_used_mb_after":      ram_after,
            "ram_delta_mb":           round(ram_after - ram_before, 1) if (ram_before and ram_after) else None,
            "vram_used_mb_before":    vram_before,
            "vram_used_mb_after":     vram_after,
            "vram_delta_mb":          round(vram_after - vram_before, 1) if (vram_before is not None and vram_after is not None) else None,
            "output_preview":         output_text[:120].replace("\n", " "),
        }
        runs.append(run_data)

        tps_str = f"{tokens_per_second:.1f} tok/s" if tokens_per_second else "n/a"
        ttft_str = f"ttft {time_to_first_token_s:.3f}s" if time_to_first_token_s else ""
        print(f"  {tps_str}  {ttft_str}")

    # Summary
    valid_runs = [r for r in runs if "error" not in r]
    summary = {}
    if valid_runs:
        def avg(key):
            vals = [r[key] for r in valid_runs if r.get(key) is not None]
            return round(sum(vals) / len(vals), 4) if vals else None

        def peak(key):
            vals = [r[key] for r in valid_runs if r.get(key) is not None]
            return max(vals) if vals else None

        summary = {
            "avg_tokens_per_second":      avg("tokens_per_second"),
            "avg_time_to_first_token_s":  avg("time_to_first_token_s"),
            "avg_total_duration_s":       avg("total_duration_s"),
            "avg_tokens_generated":       avg("tokens_generated"),
            "consistency_score":          consistency_score(outputs),
            "peak_ram_delta_mb":          peak("ram_delta_mb"),
            "peak_vram_delta_mb":         peak("vram_delta_mb"),
            "successful_runs":            len(valid_runs),
            "total_runs":                 n_runs,
        }

    return {
        "model":       model,
        "prompt_id":   prompt_id,
        "category":    prompt_def.get("category", ""),
        "description": prompt_def.get("description", ""),
        "runs":        runs,
        "summary":     summary,
    }


# -------------------------------------------------
# ENTRY POINT
# -------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="ollama-bench -- Latency, RAM/VRAM and consistency benchmark")
    parser.add_argument("--models",       nargs="+", default=DEFAULT_MODELS, help="Models to benchmark")
    parser.add_argument("--prompts",      default=DEFAULT_PROMPTS,           help="Path to bench_prompts.json")
    parser.add_argument("--prompt-ids",   nargs="+", default=[],             help="Run only specific prompt IDs")
    parser.add_argument("--list-prompts", action="store_true",               help="List available prompt IDs and exit")
    args = parser.parse_args()

    # Load prompts
    if not os.path.exists(args.prompts):
        print(f"[ERROR] Prompts file not found: {args.prompts}")
        sys.exit(1)

    with open(args.prompts, encoding="utf-8") as f:
        prompt_data = json.load(f)

    all_prompts = prompt_data.get("prompts", [])

    if args.list_prompts:
        print(f"\n  {'ID':<30} {'category':<10} {'runs':<6}  description")
        print("  " + "-" * 80)
        for p in all_prompts:
            print(f"  {p['id']:<30} {p.get('category',''):<10} {p.get('consistency_runs',3):<6}  {p.get('description','')}")
        print()
        return

    prompts = [p for p in all_prompts if not args.prompt_ids or p["id"] in args.prompt_ids]
    if not prompts:
        print("[ERROR] No matching prompts found.")
        sys.exit(1)

    # Check Ollama
    if not ollama_is_online():
        print("[ERROR] Ollama is offline. Start Ollama and retry.")
        sys.exit(1)

    version    = ollama_version()
    sys_info   = get_system_info()
    timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")

    print(f"\nollama-bench")
    print(f"  Ollama:  {version}")
    print(f"  OS:      {sys_info['os']} {sys_info['os_ver'][:40]}")
    print(f"  RAM:     {sys_info['ram_total_mb']} MB total")
    print(f"  VRAM:    {sys_info['vram_total_mb']} MB total" if sys_info["vram_total_mb"] else "  VRAM:    not detected")
    print(f"  Models:  {', '.join(args.models)}")
    print(f"  Prompts: {len(prompts)}")
    print()

    results = []

    for model in args.models:
        print(f"[model] {model}")
        for p in prompts:
            print(f"  [{p['id']}] {p.get('description','')}")
            result = run_prompt(model, p)
            results.append(result)
            s = result["summary"]
            if s:
                print(f"  summary: {s.get('avg_tokens_per_second')} tok/s avg  |  "
                      f"ttft {s.get('avg_time_to_first_token_s')}s  |  "
                      f"consistency {s.get('consistency_score')}")
        print()

    # Save results
    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = os.path.join(RESULTS_DIR, f"{timestamp}_bench.json")

    output = {
        "timestamp":     datetime.now().isoformat(),
        "ollama_version": version,
        "system":        sys_info,
        "models":        args.models,
        "prompt_file":   os.path.basename(args.prompts),
        "results":       results,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Results saved: {out_path}")


if __name__ == "__main__":
    main()
