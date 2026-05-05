# Changelog

All notable changes to ollama-bench are documented here.

---

## [Unreleased]

---

## [1.0] — 2026-04-25

### Added
- `bench.py` — main runner with CLI flags: `--models`, `--prompt-ids`, `--list-prompts`, `--prompts`
- `bench_prompts.json` — prompt library with 7 prompts covering real ecosystem tasks:
  - `commit_msg_simple` — commit message from a small Python diff
  - `commit_msg_complex` — commit message from a multi-file diff
  - `code_python_function` — generate a small Python utility function
  - `code_solidity_view` — generate a Solidity view function
  - `readme_section` — generate a README features section
  - `script_hook_ptbr` — short-form video hook in pt-BR (bitcoinfacil channel)
  - `script_hook_en` — short-form video hook in English (PandaPoints channel)
- Metrics per run: tokens/s, time-to-first-token, total duration, consistency score,
  peak RAM delta (via `wmic` on Windows / `/proc/meminfo` on Linux), peak VRAM delta
  (via `nvidia-smi`, graceful fallback if unavailable)
- Results saved to `results/<YYYYMMDD_HHMMSS>_bench.json` — structured JSON with system
  info, Ollama version, model list and per-prompt summaries
- Default model list: `phi3`, `deepseek-coder:6.7b-instruct-q4_K_M`, `llama3.1:8b`,
  `mistral:7b` — mirrors `TASK_MODEL_MAP` from pandagent
- Python stdlib only — zero external dependencies
