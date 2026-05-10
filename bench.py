"""
bench.py -- ollama-bench
Measures throughput, RAM/VRAM usage and consistency for OpenAI-compatible LLM servers.

Compatible with llama-swap, llama-server, and any server that serves /v1/chat/completions.

Usage:
    python bench.py
    python bench.py --models phi3 deepseek-coder:6.7b-instruct-q4_K_M
    python bench.py --models phi3 --prompts bench_prompts.json
    python bench.py --list-prompts

Output:
    results/<YYYYMMDD_HHMMSS>_bench.json

Note on timing:
    Wall-clock time is measured client-side per request.
    time_to_first_token_s is not available without streaming and is always null.
    tokens_per_second is derived from usage.completion_tokens / wall-clock time.
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
LLM_BASE_URL    = "http://localhost:8080"   # llama-swap / llama-server
DEFAULT_PROMPTS = os.path.join(os.path.dirname(__file__), "bench_prompts.json")
RESULTS_DIR     = os.path.join(os.path.dirname(__file__), "results")
DEFAULT_MODELS  = ["phi3", "deepseek-coder:6.7b-instruct-q4_K_M", "llama3.1:8b", "mistral:7b"]


# -------------------------------------------------
# LLM — OpenAI-compatible /v1/chat/completions
# -------------------------------------------------

def llm_generate(model: str, system: str, prompt: str) -> dict:
    """
    Call /v1/chat/completions (stream=false).
    Returns the full response dict. Timing is measured by the caller.
    """
    messages = []
    if system.strip():
        messages.append({"role": "system", "content": system.strip()})
    messages.append({"role": "user", "content": prompt.strip()})

    payload = json.dumps({
        "model":       model,
        "messages":    messages,
        "stream":      False,
        "max_tokens":  512,
        "temperature": 0.2,
        "top_p":       0.9,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{LLM_BASE_URL}/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=None) as resp:
        return json.loads(resp.read().decode("utf-8"))


def llm_server_info() -> str:
    """Return a short description of the running server via /v1/models."""
    try:
        req = urllib.request.Request(f"{LLM_BASE_URL}/v1/models", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            models = [m.get("id", "") for m in data.get("data", [])]
            return f"{len(models)} model(s): {', '.join(models[:4])}"
    except Exception:
        return "unknown"


def llm_is_online() -> bool:
    try:
        req = urllib.request.Request(f"{LLM_BASE_URL}/v1/models", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


# -------------------------------------------------
# SYSTEM METRICS
# -------------------------------------------------

def _win_memory_status() -> tuple[float, float] | None:
    """
    Returns (total_mb, avail_mb) via Win32 GlobalMemoryStatusEx.
    Works on Windows 10/11 regardless of wmic availability.
    """
    try:
        import ctypes
        import ctypes.wintypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength",                ctypes.wintypes.DWORD),
                ("dwMemoryLoad",            ctypes.wintypes.DWORD),
                ("ullTotalPhys",            ctypes.c_uint64),
                ("ullAvailPhys",            ctypes.c_uint64),
                ("ullTotalPageFile",        ctypes.c_uint64),
                ("ullAvailPageFile",        ctypes.c_uint64),
                ("ullTotalVirtual",         ctypes.c_uint64),
                ("ullAvailVirtual",         ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(stat)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        total_mb = round(stat.ullTotalPhys / 1024 / 1024, 1)
        avail_mb = round(stat.ullAvailPhys / 1024 / 1024, 1)
        return total_mb, avail_mb
    except Exception:
        return None


def get_ram_used_mb() -> float | None:
    """Returns used RAM in MB. Uses Win32 API on Windows, /proc/meminfo on Linux."""
    try:
        if platform.system() == "Windows":
            mem = _win_memory_status()
            if mem:
                total_mb, avail_mb = mem
                return round(total_mb - avail_mb, 1)
        else:
            with open("/proc/meminfo") as f:
                lines = f.readlines()
            info = {}
            for line in lines:
                parts = line.split()
                info[parts[0].rstrip(":")] = int(parts[1])
            total   = info.get("MemTotal", 0)
            free    = info.get("MemFree", 0)
            buffers = info.get("Buffers", 0)
            cached  = info.get("Cached", 0)
            return round((total - free - buffers - cached) / 1024, 1)
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
        "os":            platform.system(),
        "os_ver":        platform.version(),
        "cpu":           platform.processor() or platform.machine(),
        "python":        platform.python_version(),
        "ram_total_mb":  None,
        "vram_total_mb": None,
    }
    try:
        if platform.system() == "Windows":
            mem = _win_memory_status()
            if mem:
                info["ram_total_mb"] = mem[0]
        else:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        info["ram_total_mb"] = round(int(line.split()[1]) / 1024, 1)
    except Exception:
        pass

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

    Timing notes:
    - total_duration_s: wall-clock time per request (client-side)
    - tokens_per_second: usage.completion_tokens / total_duration_s
    - time_to_first_token_s: always null (requires streaming)
    """
    n_runs    = prompt_def.get("consistency_runs", 3)
    system    = prompt_def.get("system", "")
    prompt    = prompt_def["prompt"]
    prompt_id = prompt_def["id"]

    runs    = []
    outputs = []

    for i in range(n_runs):
        print(f"    run {i + 1}/{n_runs}", end="", flush=True)

        ram_before  = get_ram_used_mb()
        vram_before = get_vram_used_mb()
        t0 = time.time()

        try:
            resp = llm_generate(model, system, prompt)
        except Exception as e:
            print(f"  ERROR: {e}")
            runs.append({"run": i + 1, "error": str(e)})
            continue

        total_duration_s = round(time.time() - t0, 4)
        ram_after  = get_ram_used_mb()
        vram_after = get_vram_used_mb()

        usage             = resp.get("usage", {})
        completion_tokens = usage.get("completion_tokens")
        tokens_per_second = (
            round(completion_tokens / total_duration_s, 2)
            if (completion_tokens and total_duration_s > 0) else None
        )

        choices     = resp.get("choices", [])
        output_text = choices[0]["message"]["content"].strip() if choices else ""
        outputs.append(output_text)

        run_data = {
            "run":                    i + 1,
            "tokens_generated":       completion_tokens,
            "tokens_per_second":      tokens_per_second,
            "time_to_first_token_s":  None,
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

        tps_str  = f"{tokens_per_second:.1f} tok/s" if tokens_per_second else "n/a tok/s"
        time_str = f"{total_duration_s:.2f}s"
        print(f"  {tps_str}  {time_str}")

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
            "avg_time_to_first_token_s":  None,
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
# COMPARE
# -------------------------------------------------

def compare_results(path_a: str, path_b: str) -> None:
    """
    Print a side-by-side comparison of two bench result JSON files.

    For each (model, prompt_id) pair present in both files, shows:
      - avg tokens/s       (higher is better)
      - avg total duration (lower is better)
      - consistency score  (higher is better)
    with percentage deltas and a checkmark/cross improvement indicator.
    """
    for path in (path_a, path_b):
        if not os.path.exists(path):
            print(f"[ERROR] File not found: {path}")
            sys.exit(1)

    with open(path_a, encoding="utf-8") as f:
        data_a = json.load(f)
    with open(path_b, encoding="utf-8") as f:
        data_b = json.load(f)

    def build_index(data):
        idx = {}
        for r in data.get("results", []):
            key = (r["model"], r["prompt_id"])
            idx[key] = r.get("summary", {})
        return idx

    idx_a = build_index(data_a)
    idx_b = build_index(data_b)

    common_keys = sorted(k for k in idx_a if k in idx_b)
    if not common_keys:
        print("[compare] No matching (model, prompt_id) pairs found in both files.")
        return

    print("\nollama-bench --compare")
    print(f"  A: {os.path.basename(path_a):<50}  {data_a.get('timestamp','')[:19]}")
    print(f"  B: {os.path.basename(path_b):<50}  {data_b.get('timestamp','')[:19]}")
    srv_a = data_a.get("llm_server", data_a.get("ollama_version", ""))
    srv_b = data_b.get("llm_server", data_b.get("ollama_version", ""))
    if srv_a != srv_b:
        print(f"  Server: A={srv_a}  B={srv_b}")
    print()

    def fmt(val, decimals=2):
        return f"{val:>7.{decimals}f}" if val is not None else "      -"

    def delta(a, b, higher_is_better=True):
        if a is None or b is None or a == 0:
            return "       -"
        pct = (b - a) / abs(a) * 100
        improved = (pct > 0) == higher_is_better
        tag  = "+" if improved else "x"
        sign = "+" if pct > 0 else ""
        return f"{tag} {sign}{pct:.1f}%"

    models = []
    for model, _ in common_keys:
        if model not in models:
            models.append(model)

    col = 28

    for model in models:
        print(f"  model: {model}")
        header = (
            f"  {'prompt_id':<{col}}"
            f"  {'tok/s A':>8}  {'tok/s B':>8}  {'D tok/s':>10}"
            f"  {'dur A':>7}  {'dur B':>7}  {'D dur':>10}"
            f"  {'cons A':>7}  {'cons B':>7}  {'D cons':>10}"
        )
        print(header)
        print("  " + "-" * (len(header) - 2))

        for (m, pid) in common_keys:
            if m != model:
                continue
            sa = idx_a[(m, pid)]
            sb = idx_b[(m, pid)]

            tps_a  = sa.get("avg_tokens_per_second")
            tps_b  = sb.get("avg_tokens_per_second")
            dur_a  = sa.get("avg_total_duration_s")
            dur_b  = sb.get("avg_total_duration_s")
            cons_a = sa.get("consistency_score")
            cons_b = sb.get("consistency_score")

            print(
                f"  {pid:<{col}}"
                f"  {fmt(tps_a):>8}  {fmt(tps_b):>8}  {delta(tps_a, tps_b, higher_is_better=True):>10}"
                f"  {fmt(dur_a,3):>7}  {fmt(dur_b,3):>7}  {delta(dur_a, dur_b, higher_is_better=False):>10}"
                f"  {fmt(cons_a):>7}  {fmt(cons_b):>7}  {delta(cons_a, cons_b, higher_is_better=True):>10}"
            )
        print()

    print("  Summary (tok/s):")
    deltas = []
    for (model, pid) in common_keys:
        a = idx_a[(model, pid)].get("avg_tokens_per_second")
        b = idx_b[(model, pid)].get("avg_tokens_per_second")
        if a and b and a > 0:
            deltas.append(((b - a) / abs(a) * 100, model, pid))

    if deltas:
        deltas.sort(key=lambda x: x[0], reverse=True)
        pct, m, p = deltas[0]
        print(f"  Most improved:  {m} / {p:<30}  {pct:+.1f}%")
        pct, m, p = deltas[-1]
        print(f"  Most regressed: {m} / {p:<30}  {pct:+.1f}%")
    print()


# -------------------------------------------------
# ENTRY POINT
# -------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ollama-bench -- Throughput, RAM/VRAM and consistency benchmark for OpenAI-compatible LLM servers"
    )
    parser.add_argument("--models",       nargs="+", default=DEFAULT_MODELS, help="Models to benchmark")
    parser.add_argument("--prompts",      default=DEFAULT_PROMPTS,           help="Path to bench_prompts.json")
    parser.add_argument("--prompt-ids",   nargs="+", default=[],             help="Run only specific prompt IDs")
    parser.add_argument("--list-prompts", action="store_true",               help="List available prompt IDs and exit")
    parser.add_argument("--compare",      nargs=2, metavar="FILE",           help="Compare two result JSON files and exit")
    args = parser.parse_args()

    if args.compare:
        compare_results(args.compare[0], args.compare[1])
        return

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

    if not llm_is_online():
        print(f"[ERROR] LLM server is offline. Start llama-swap or llama-server on {LLM_BASE_URL} and retry.")
        sys.exit(1)

    server_info = llm_server_info()
    sys_info    = get_system_info()
    timestamp   = datetime.now().strftime("%Y%m%d_%H%M%S")

    print(f"\nollama-bench")
    print(f"  Server:  {LLM_BASE_URL}  ({server_info})")
    print(f"  OS:      {sys_info['os']} {sys_info['os_ver'][:40]}")
    print(f"  RAM:     {sys_info['ram_total_mb']} MB total")
    if sys_info["vram_total_mb"]:
        print(f"  VRAM:    {sys_info['vram_total_mb']} MB total")
    else:
        print(f"  VRAM:    not detected")
    print(f"  Models:  {', '.join(args.models)}")
    print(f"  Prompts: {len(prompts)}")
    print(f"  Note:    time_to_first_token not measured (non-streaming mode)")
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
                tps  = s.get("avg_tokens_per_second")
                dur  = s.get("avg_total_duration_s")
                cons = s.get("consistency_score")
                print(f"  summary: {tps} tok/s avg  |  {dur}s avg  |  consistency {cons}")
        print()

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = os.path.join(RESULTS_DIR, f"{timestamp}_bench.json")

    output = {
        "timestamp":    datetime.now().isoformat(),
        "llm_server":   server_info,
        "llm_base_url": LLM_BASE_URL,
        "system":       sys_info,
        "models":       args.models,
        "prompt_file":  os.path.basename(args.prompts),
        "results":      results,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Results saved: {out_path}")


if __name__ == "__main__":
    main()
