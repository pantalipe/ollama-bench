# ollama-bench

Local benchmark tool for Ollama models. Measures latency, RAM/VRAM usage and output consistency — designed to inform model routing decisions in `panda_client.py`.

## What it measures

- **Latency** — tokens/s and time-to-first-token (from Ollama's native timing fields)
- **RAM usage** — delta before/after each inference (via `wmic` on Windows, `/proc/meminfo` on Linux)
- **VRAM usage** — delta via `nvidia-smi` (graceful fallback if unavailable)
- **Consistency** — same prompt run N times, score = most_common_output / total_runs

## Usage

```bash
# Benchmark default models (phi3 + deepseek-coder)
python bench.py

# Benchmark specific models
python bench.py --models phi3 deepseek-coder:6.7b-instruct-q4_K_M

# Run only specific prompt IDs
python bench.py --prompt-ids commit_msg_simple code_python_function

# List available prompts
python bench.py --list-prompts

# Custom prompts file
python bench.py --prompts my_prompts.json
```

## Output

Results are saved to `results/<YYYYMMDD_HHMMSS>_bench.json`. Example structure:

```json
{
  "timestamp": "2025-04-23T14:00:00",
  "ollama_version": "0.3.x",
  "system": { "os": "Windows", "ram_total_mb": 8192, "vram_total_mb": 4096 },
  "models": ["phi3", "deepseek-coder:6.7b-instruct-q4_K_M"],
  "results": [
    {
      "model": "phi3",
      "prompt_id": "commit_msg_simple",
      "summary": {
        "avg_tokens_per_second": 18.4,
        "avg_time_to_first_token_s": 0.312,
        "avg_total_duration_s": 4.21,
        "consistency_score": 1.0,
        "peak_ram_delta_mb": 42.0,
        "peak_vram_delta_mb": null
      }
    }
  ]
}
```

## Prompt categories

Prompts in `bench_prompts.json` mirror real ecosystem tasks:

| ID | Category | Description |
|----|----------|-------------|
| `commit_msg_simple` | text | Commit message from a small Python diff |
| `commit_msg_complex` | text | Commit message from a multi-file diff |
| `code_python_function` | code | Generate a small Python utility function |
| `code_solidity_view` | code | Generate a Solidity view function |
| `readme_section` | text | Generate a README features section |
| `script_hook_ptbr` | text | Short-form video hook in pt-BR (bitcoinfacil channel) |
| `script_hook_en` | text | Short-form video hook in English (PandaPoints channel) |

## Adding prompts

Edit `bench_prompts.json` and add an entry:

```json
{
  "id": "my_prompt",
  "category": "code",
  "task": "code",
  "description": "What this prompt tests",
  "consistency_runs": 3,
  "system": "Optional system prompt",
  "prompt": "Your prompt here"
}
```

## Stack

- Python stdlib only (no pip installs)
- Ollama local API (`http://localhost:11434`)
- `nvidia-smi` for VRAM (optional)
- `wmic` for RAM on Windows

## Project structure

```
ollama-bench/
  bench.py              # runner
  bench_prompts.json    # prompt definitions
  results/              # JSON output files (gitignored)
  README.md
```
