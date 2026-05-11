# Changelog

All notable changes to ollama-bench are documented here.

---

## [Unreleased]

---

## [1.2] — 2026-05-10

### Changed
- Migrated from Ollama's native `/api/generate` to the OpenAI-compatible
  `/v1/chat/completions` endpoint — now works with llama-swap and llama-server
- `LLM_BASE_URL = "http://localhost:8080"` replaces `OLLAMA_BASE_URL = "http://localhost:11434"`
- `ollama_generate()` → `llm_generate()`: sends a `messages` array, parses
  `choices[0].message.content` from the response
- `ollama_is_online()` → `llm_is_online()`: checks `/v1/models` instead of `/api/tags`
- `ollama_version()` → `llm_server_info()`: returns model count and IDs from `/v1/models`
- Timing now measured client-side (`time.time()` before/after request) — Ollama's
  nanosecond timing fields (`eval_duration`, `total_duration`) are not available in
  the OpenAI API
- `tokens_per_second` derived from `usage.completion_tokens / wall_clock_s`
- `time_to_first_token_s` is always `null` — not available without streaming; field
  retained in output schema for backward compatibility with `--compare` and gitmanager
- Output JSON: `ollama_version` key replaced by `llm_server` + `llm_base_url`
- `compare_results()`: reads `llm_server` with fallback to legacy `ollama_version`
  key so existing result files still compare correctly
- RAM metrics on Windows: `wmic` replaced by `ctypes.windll.kernel32.GlobalMemoryStatusEx`
  (`wmic` is deprecated and removed in recent Windows 11 builds)

---

## [1.1] — 2026-05-06

### Added
- `--compare A B` flag — side-by-side comparison of two result JSON files;
  for each `(model, prompt_id)` pair present in both files shows tok/s, total duration
  and consistency score with percentage deltas and a +/x improvement indicator
- Overall summary block at the end of `--compare` output: most improved and
  most regressed `(model, prompt_id)` pair by tok/s

---

## [1.0] — 2026-04-25

### Added
- `bench.py` — main runner with CLI flags: `--models`, `--prompt-ids`,
  `--list-prompts`, `--prompts`
- `bench_prompts.json` — prompt library with 7 prompts covering real ecosystem tasks:
  `commit_msg_simple`, `commit_msg_complex`, `code_python_function`,
  `code_solidity_view`, `readme_section`, `script_hook_ptbr`, `script_hook_en`
- Metrics per run: tokens/s, time-to-first-token, total duration, consistency score,
  peak RAM delta, peak VRAM delta (nvidia-smi, graceful fallback)
- Results saved to `results/<YYYYMMDD_HHMMSS>_bench.json`
- Default model list mirrors `TASK_MODEL_MAP` from pandagent:
  `phi3`, `deepseek-coder:6.7b-instruct-q4_K_M`, `llama3.1:8b`, `mistral:7b`
- Python stdlib only — zero external dependencies
