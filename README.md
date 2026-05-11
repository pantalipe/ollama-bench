# ollama-bench

Throughput, RAM/VRAM usage and consistency benchmark for OpenAI-compatible local LLM
servers. Designed to inform model routing decisions in `panda_client.py`.

## What it measures

- **Throughput** — tokens/s derived from `usage.completion_tokens` / wall-clock time
- **Total duration** — wall-clock time per request (client-side, includes network overhead)
- **RAM usage** — delta before/after each inference (Win32 API on Windows, `/proc/meminfo` on Linux)
- **VRAM usage** — delta via `nvidia-smi` (graceful fallback if unavailable)
- **Consistency** — same prompt run N times; score = most_common_output / total_runs

> `time_to_first_token` is not measured — it requires streaming mode, which this tool
> does not use. The field is always `null` in result files.

## Requirements

Python 3.10+. No external dependencies — stdlib only.

An OpenAI-compatible LLM server must be running on port 8080:

```bash
# llama-swap example
llama-swap --config llama-swap.yaml
```

## Usage

```bash
# Benchmark default models
python bench.py

# Benchmark specific models
python bench.py --models phi3 deepseek-coder:6.7b-instruct-q4_K_M

# Run only specific prompt IDs
python bench.py --prompt-ids commit_msg_simple code_python_function

# List available prompts
python bench.py --list-prompts

# Custom prompts file
python bench.py --prompts my_prompts.json

# Compare two result files
python bench.py --compare results/20260425_bench.json results/20260506_bench.json
```

## Output

Results are saved to `results/<YYYYMMDD_HHMMSS>_bench.json`. Example structure:

```json
{
  "timestamp": "2026-05-09T14:00:00",
  "llm_server": "4 model(s): phi3, deepseek-coder:6.7b-instruct-q4_K_M, ...",
  "llm_base_url": "http://localhost:8080",
  "system": { "os": "Windows", "ram_total_mb": 8156.9, "vram_total_mb": null },
  "models": ["phi3"],
  "results": [
    {
      "model": "phi3",
      "prompt_id": "commit_msg_simple",
      "summary": {
        "avg_tokens_per_second": 18.4,
        "avg_time_to_first_token_s": null,
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
- OpenAI-compatible API (`http://localhost:8080`) — llama-swap, llama-server
- `nvidia-smi` for VRAM (optional)
- Win32 `GlobalMemoryStatusEx` for RAM on Windows (no external tools required)

## Project structure

```
ollama-bench/
  bench.py              # runner
  bench_prompts.json    # prompt definitions
  results/              # JSON output files (gitignored)
  README.md
  CHANGELOG.md
```
