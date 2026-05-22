"""
bench_quality.py -- avaliação de qualidade de outputs do ollama-bench
Combina heurísticas automáticas por categoria com exportação de
judge sheet para avaliação manual por qualquer LLM.

Uso:
    python bench_quality.py                              # lê o bench_all mais recente
    python bench_quality.py --input results/X.json      # arquivo específico
    python bench_quality.py --export-judge               # exporta judge sheet (.md)
    python bench_quality.py --export-judge --judge-dir quality

Saída:
    Tabela de scores no terminal
    results/<timestamp>_quality.json     (scores + heurísticas)
    quality/<timestamp>_judge.md         (sheet para colar em qualquer LLM)
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"
QUALITY_DIR = Path(__file__).parent / "quality"

# ---------------------------------------------------------------------------
# HEURÍSTICAS POR PROMPT_ID
# ---------------------------------------------------------------------------
# Cada check retorna (passed: bool, label: str, detail: str)

CONVENTIONAL_TYPES = r"(feat|fix|refactor|chore|docs|test|style|build|ci|perf|revert)"
CC_PATTERN         = re.compile(rf"^{CONVENTIONAL_TYPES}(\([a-z0-9_/#-]+\))?: .{{3,}}", re.I)
PREAMBLE_PATTERN   = re.compile(r"^(sure[,\s]|here is|here'?s|commit message:)", re.I)
FENCE_PATTERN      = re.compile(r"```")
STAGE_DIR_PATTERN  = re.compile(r"\[|\bNARRATOR\b|stage direction", re.I)
PT_WORDS           = re.compile(r"\b(você|seu|sua|para|como|que|por|mas|não|com|uma|isso)\b", re.I)
EN_WORDS           = re.compile(r"\b(you|your|the|and|for|that|with|this|how|why|what)\b", re.I)


def _check(passed: bool, label: str, ok_msg: str = "", fail_msg: str = "") -> dict:
    return {"passed": passed, "label": label,
            "detail": ok_msg if passed else fail_msg}


def heuristics_commit(text: str) -> list[dict]:
    first_line = text.strip().splitlines()[0] if text.strip() else ""
    return [
        _check(bool(CC_PATTERN.match(first_line)),
               "conventional commits",
               "formato correto", f"linha: {first_line[:60]!r}"),
        _check(not PREAMBLE_PATTERN.match(text.strip()),
               "sem preamble",
               "sem preamble", "começa com preamble"),
        _check(not FENCE_PATTERN.search(first_line),
               "sem markdown fence",
               "sem fences", "contém ```"),
        _check(len(first_line) <= 72,
               "linha <=72 chars",
               f"{len(first_line)} chars", f"{len(first_line)} chars (>72)"),
        _check("\n" not in text.strip()[:80] or len(text.strip().splitlines()) <= 2,
               "resposta concisa",
               "concisa", "resposta muito longa / multi-linha"),
    ]


def heuristics_code_python(text: str) -> list[dict]:
    clean = re.sub(r"```[a-z]*\n?", "", text).strip()
    has_def  = bool(re.search(r"\bdef\s+slugify\b", clean))
    has_sig  = bool(re.search(r"text:\s*str", clean))
    has_ret  = bool(re.search(r"return\b", clean))
    has_re   = bool(re.search(r"\bre\b|\bregex\b", clean))
    syntax_ok, syntax_msg = _py_syntax(clean)
    return [
        _check(has_def,    "def slugify presente",    "sim", "não encontrado"),
        _check(has_sig,    "type hint str",            "sim", "ausente"),
        _check(has_ret,    "return presente",          "sim", "ausente"),
        _check(has_re,     "usa regex (re)",           "sim", "não usa re"),
        _check(not PREAMBLE_PATTERN.match(text.strip()), "sem preamble", "ok", "tem preamble"),
        _check(syntax_ok,  "sintaxe Python válida",    "ok", syntax_msg),
    ]


def heuristics_code_solidity(text: str) -> list[dict]:
    clean = re.sub(r"```[a-z]*\n?", "", text).strip()
    has_fn   = bool(re.search(r"\bfunction\s+getPoolRatio\b", clean))
    has_view = bool(re.search(r"\bview\b", clean))
    has_ret  = bool(re.search(r"\breturns\s*\(\s*uint256\s*\)", clean))
    has_zero = bool(re.search(r"== 0|== 0x0|bnbReserve\s*==\s*0|return\s+0", clean))
    has_scale= bool(re.search(r"1e18|10\s*\*\*\s*18|1_?000_?000_?000_?000_?000_?000", clean))
    return [
        _check(has_fn,   "function getPoolRatio",  "sim", "não encontrado"),
        _check(has_view, "modificador view",        "sim", "ausente"),
        _check(has_ret,  "returns (uint256)",       "sim", "ausente/incorreto"),
        _check(has_zero, "divisão por zero tratada","sim", "não tratada"),
        _check(has_scale,"escala 1e18",             "sim", "ausente"),
        _check(not PREAMBLE_PATTERN.match(text.strip()), "sem preamble", "ok", "tem preamble"),
    ]


def heuristics_readme(text: str) -> list[dict]:
    has_h2     = bool(re.search(r"^##\s+Features", text.strip(), re.M))
    bullets    = re.findall(r"^[-*]\s+.+", text, re.M)
    has_bold   = bool(re.search(r"\*\*[^*]+\*\*", text))
    no_fence   = not FENCE_PATTERN.search(text)
    no_preamble= not PREAMBLE_PATTERN.match(text.strip())
    return [
        _check(has_h2,          "## Features presente", "sim", "ausente"),
        _check(len(bullets) >= 4,">=4 bullet points",   f"{len(bullets)} bullets", f"apenas {len(bullets)}"),
        _check(has_bold,        "usa bold (**)",         "sim", "sem bold"),
        _check(no_fence,        "sem markdown fence",    "ok", "contém ```"),
        _check(no_preamble,     "sem preamble",          "ok", "tem preamble"),
    ]


def heuristics_script_ptbr(text: str) -> list[dict]:
    words      = len(text.split())
    pt_matches = len(PT_WORDS.findall(text))
    no_stage   = not STAGE_DIR_PATTERN.search(text)
    has_hook   = bool(re.search(r'[?!"]|bitcoin|cripto|você', text, re.I))
    return [
        _check(pt_matches >= 3,  "texto em pt-BR",      f"{pt_matches} palavras PT", f"apenas {pt_matches} PT"),
        _check(15 <= words <= 80,"tamanho do hook 15-80",f"{words} palavras", f"{words} palavras (fora 15-80)"),
        _check(no_stage,         "sem stage directions","ok", "contém stage direction"),
        _check(has_hook,         "conteúdo de hook",    "ok", "não parece um hook"),
    ]


def heuristics_script_en(text: str) -> list[dict]:
    words      = len(text.split())
    en_matches = len(EN_WORDS.findall(text))
    pt_matches = len(PT_WORDS.findall(text))
    no_stage   = not STAGE_DIR_PATTERN.search(text)
    has_hook   = bool(re.search(r'[?!"]|crypto|reward|imagine|earn', text, re.I))
    return [
        _check(en_matches >= 3,  "texto em inglês",     f"{en_matches} EN words", f"apenas {en_matches}"),
        _check(pt_matches <= 1,  "sem pt-BR",           "ok", f"{pt_matches} palavras PT detectadas"),
        _check(15 <= words <= 80,"tamanho do hook 15-80",f"{words} palavras", f"{words} palavras (fora 15-80)"),
        _check(no_stage,         "sem stage directions","ok", "contém stage direction"),
        _check(has_hook,         "conteúdo de hook",    "ok", "não parece um hook"),
    ]


HEURISTIC_MAP = {
    "commit_msg_simple":   heuristics_commit,
    "commit_msg_complex":  heuristics_commit,
    "code_python_function":heuristics_code_python,
    "code_solidity_view":  heuristics_code_solidity,
    "readme_section":      heuristics_readme,
    "script_hook_ptbr":    heuristics_script_ptbr,
    "script_hook_en":      heuristics_script_en,
}

# ---------------------------------------------------------------------------
# SYNTAX CHECK HELPER
# ---------------------------------------------------------------------------

def _py_syntax(code: str) -> tuple[bool, str]:
    """Tenta compilar o código Python. Retorna (ok, mensagem)."""
    try:
        import py_compile, tempfile
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w",
                                         delete=False, encoding="utf-8") as f:
            f.write(code)
            tmp = f.name
        py_compile.compile(tmp, doraise=True)
        os.unlink(tmp)
        return True, "ok"
    except Exception as e:
        return False, str(e)[:80]


# ---------------------------------------------------------------------------
# SCORE DE UM RESULTADO
# ---------------------------------------------------------------------------

def score_result(result: dict) -> dict:
    """Dado um item de results[], retorna heurísticas + score."""
    pid      = result["prompt_id"]
    model    = result["model"]
    preview  = result.get("output_preview", "")
    runs     = result.get("runs", [])

    # Usa o output do run 2 (warm, mais representativo). Fallback: preview.
    warm_text = preview
    if len(runs) >= 2:
        warm_text = runs[1].get("output_preview", preview) or preview

    fn = HEURISTIC_MAP.get(pid)
    if not fn or not warm_text.strip():
        return {
            "model": model, "prompt_id": pid,
            "heuristic_score": None,
            "checks": [],
            "output_sample": warm_text[:200],
            "note": "sem heurística definida ou output vazio",
        }

    checks    = fn(warm_text)
    passed    = sum(1 for c in checks if c["passed"])
    total     = len(checks)
    h_score   = round(passed / total, 3) if total else None

    return {
        "model":            model,
        "prompt_id":        pid,
        "heuristic_score":  h_score,
        "checks_passed":    passed,
        "checks_total":     total,
        "checks":           checks,
        "output_sample":    warm_text[:300],
    }


# ---------------------------------------------------------------------------
# TERMINAL: tabela de scores
# ---------------------------------------------------------------------------

def print_table(scored: list[dict]) -> None:
    from collections import defaultdict
    by_prompt: dict[str, dict[str, float | None]] = defaultdict(dict)
    models_seen: list[str] = []

    for s in scored:
        m, p = s["model"], s["prompt_id"]
        by_prompt[p][m] = s["heuristic_score"]
        if m not in models_seen:
            models_seen.append(m)

    # Larguras
    col = 9
    pid_w = max(len(p) for p in by_prompt) + 2
    header = f"{'prompt':<{pid_w}}" + "".join(f"{m[:col]:>{col+1}}" for m in models_seen)
    print("\n" + "=" * len(header))
    print("  QUALITY — heuristic scores (0.0 – 1.0)")
    print("=" * len(header))
    print("  " + header)
    print("  " + "-" * (len(header)))

    for pid, scores in sorted(by_prompt.items()):
        row = f"  {pid:<{pid_w}}"
        for m in models_seen:
            v = scores.get(m)
            cell = f"{v:.2f}" if v is not None else "  -  "
            color = ""
            if v is not None:
                color = "\033[32m" if v >= 0.8 else ("\033[33m" if v >= 0.5 else "\033[31m")
            row += f"{color}{cell:>{col+1}}\033[0m"
        print(row)

    # Média por modelo
    print("  " + "-" * (len(header)))
    avgs = {}
    for m in models_seen:
        vals = [by_prompt[p].get(m) for p in by_prompt if by_prompt[p].get(m) is not None]
        avgs[m] = round(sum(vals) / len(vals), 3) if vals else None

    row = f"  {'MÉDIA':<{pid_w}}"
    for m in models_seen:
        v = avgs[m]
        cell = f"{v:.2f}" if v is not None else "  -  "
        row += f"\033[1m{cell:>{col+1}}\033[0m"
    print(row + "\n")


# ---------------------------------------------------------------------------
# EXPORTAR JUDGE SHEET
# ---------------------------------------------------------------------------

RUBRIC = """## Rubric de avaliação — instruções para o juiz

Você é um avaliador de outputs de modelos de linguagem locais (LLMs).
Para cada tarefa abaixo, você vai receber os outputs de vários modelos e deve
atribuir uma nota de **1 a 5** para cada um, seguindo os critérios específicos
da tarefa. Preencha a tabela de scores no final.

### Escala geral
| Nota | Significado |
|------|-------------|
| 5    | Perfeito — atende a todos os requisitos, zero ajuste necessário |
| 4    | Bom — atende quase tudo, ajuste mínimo necessário |
| 3    | Aceitável — funciona mas tem problemas menores de formato ou qualidade |
| 2    | Ruim — tem a ideia certa mas com erros ou formato errado |
| 1    | Inútil — errado, fora do assunto ou inutilizável |

### Critérios por tarefa
- **commit_msg**: Conventional Commits (`type(scope): desc`), uma linha, <72 chars, sem preamble, sem markdown
- **code_python**: Código Python válido, `def slugify` correto, sem preamble, sem fences extras  
- **code_solidity**: `function getPoolRatio() view returns (uint256)`, divisão por zero tratada, escala 1e18
- **readme**: `## Features`, ≥4 bullets com bold, sem preamble, bem escrito
- **script_ptbr**: Hook curto (5 seg), em pt-BR, conversacional, sem stage directions
- **script_en**: Hook curto (5 seg), em inglês, conversacional, sem stage directions

---
"""


def export_judge_sheet(scored: list[dict], source_path: Path, out_dir: Path) -> Path:
    out_dir.mkdir(exist_ok=True)
    ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
    out   = out_dir / f"{ts}_judge.md"

    from collections import defaultdict
    by_prompt: dict[str, list[dict]] = defaultdict(list)
    for s in scored:
        by_prompt[s["prompt_id"]].append(s)

    lines = [f"# Judge Sheet — {source_path.name}\n",
             f"Gerado em: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n",
             RUBRIC]

    for pid, entries in sorted(by_prompt.items()):
        lines.append(f"\n---\n\n## Tarefa: `{pid}`\n")
        for e in entries:
            model = e["model"]
            sample = e.get("output_sample", "").strip() or "_[output vazio ou não disponível]_"
            h = e.get("heuristic_score")
            h_str = f"{h:.0%}" if h is not None else "n/a"
            lines.append(f"\n### Modelo: `{model}` (heurística: {h_str})\n")
            lines.append("```\n" + sample + "\n```\n")

        # Tabela de scores para preenchimento
        models = [e["model"] for e in entries]
        lines.append("\n**Scores (preencha):**\n")
        lines.append("| modelo | nota (1-5) | comentário |\n")
        lines.append("|--------|-----------|------------|\n")
        for m in models:
            lines.append(f"| `{m}` |  | |\n")

    with open(out, "w", encoding="utf-8") as f:
        f.writelines(lines)

    return out


# ---------------------------------------------------------------------------
# CARREGAR BENCH JSON
# ---------------------------------------------------------------------------

def load_bench(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def find_latest_bench() -> Path | None:
    """Procura o bench_all.json mais recente, depois qualquer bench.json."""
    candidates = sorted(RESULTS_DIR.glob("*_bench_all.json"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        candidates = sorted(RESULTS_DIR.glob("*_bench.json"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="bench_quality.py — heurísticas + judge sheet para outputs do bench"
    )
    parser.add_argument("--input",        default="",    help="Caminho para bench_all.json ou bench.json")
    parser.add_argument("--export-judge", action="store_true", help="Exportar judge sheet (.md)")
    parser.add_argument("--judge-dir",    default="quality", help="Pasta para o judge sheet (padrão: quality/)")
    parser.add_argument("--output-dir",   default="",    help="Pasta para quality.json (padrão: results/)")
    args = parser.parse_args()

    # Localizar arquivo de bench
    if args.input:
        bench_path = Path(args.input)
    else:
        bench_path = find_latest_bench()
        if not bench_path:
            print("[ERROR] Nenhum arquivo bench encontrado em results/. Use --input.")
            sys.exit(1)

    print(f"\n[bench_quality] Lendo: {bench_path.name}")
    data    = load_bench(bench_path)
    results = data.get("results", [])

    if not results:
        print("[ERROR] Nenhum resultado encontrado no arquivo.")
        sys.exit(1)

    # Pontuar todos os resultados
    scored = [score_result(r) for r in results]

    # Tabela no terminal
    print_table(scored)

    # Detalhe dos checks que falharam
    failures = [(s["model"], s["prompt_id"], c)
                for s in scored for c in s["checks"] if not c["passed"]]
    if failures:
        print("  CHECKS QUE FALHARAM")
        print("  " + "-" * 60)
        for model, pid, c in failures:
            print(f"  {model:<38} {pid:<26} FAIL {c['label']}: {c['detail']}")
        print()

    # Salvar quality.json
    out_dir  = Path(args.output_dir) if args.output_dir else RESULTS_DIR
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_json = out_dir / f"{ts}_quality.json"
    payload  = {
        "timestamp":   datetime.now().isoformat(),
        "source_file": str(bench_path.name),
        "models":      data.get("models", []),
        "scored":      scored,
    }
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"[saved] {out_json.name}")

    # Judge sheet
    if args.export_judge:
        judge_dir = Path(__file__).parent / args.judge_dir
        out_md    = export_judge_sheet(scored, bench_path, judge_dir)
        print(f"[saved] {out_md}")
        print(f"\n  >> Abra o arquivo, copie o conteudo e cole em qualquer LLM para avaliacao manual.\n")


if __name__ == "__main__":
    main()
