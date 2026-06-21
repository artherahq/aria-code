# Contributing to Aria Code

We welcome contributions of all kinds — bug fixes, new features, documentation improvements, and new financial tools.

---

## Quick Start

```bash
git clone https://github.com/Cinsoul/Aria-Code.git
cd aria-code
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Set up Ollama for local testing (no API key needed)
ollama pull qwen2.5-coder:7b

# Verify everything works
python3 aria_cli.py -p "你好"
```

---

## What to Contribute

### Great first issues
- Add a new `/slash` command
- Add a new financial formula to `local_finance_tools.py`
- Improve a built-in prompt template
- Add a new data source adapter in `datasources/sources/`
- Fix a formatting issue in `finance_formulas.py`
- Write a new test

### Bigger contributions
- New LLM provider in `providers/llm/`
- New specialist agent in `agents/financial/`
- New quant strategy in `strategy_vault.py`
- MCP tool improvements

---

## Code Style

```bash
# Format (Black)
pip install black
black .

# Lint (Ruff)
pip install ruff
ruff check .

# Type check (mypy)
pip install mypy
mypy aria_cli.py --ignore-missing-imports
```

**Rules:**
- Max line length: 100
- Docstrings on all public functions
- Type annotations on new code
- No `print()` for logging — use `console.print()` (Rich) in CLI context

---

## Running Tests

```bash
pytest tests/ -v
pytest tests/test_aria_cli_core.py -v    # Core tests only
pytest tests/ -k "not e2e"               # Skip end-to-end (requires running LLM)
```

---

## Commit Convention

```
feat(cli): add /whale slash command for dark pool detection
fix(formula): handle unclosed LaTeX buffer at stream end
docs(readme): add Tushare setup instructions
test(provider): add Ollama connection timeout test
```

Types: `feat` · `fix` · `docs` · `style` · `refactor` · `test` · `chore`

---

## Branch Convention

Long-lived branches:

- `main` for protected releases.
- `develop` for integration, if the team keeps a staging branch.

Short-lived branches:

- `feature/<topic>`
- `fix/<topic>`
- `refactor/<topic>`
- `chore/<topic>`
- `docs/<topic>`
- `release/vX.Y`
- `codex/<topic>` for temporary agent-authored work only.

Do not merge directly into `main`. Open a pull request and wait for CI.

For organization migration and repository ownership, see
[`docs/operations/github_enterprise_migration.md`](docs/operations/github_enterprise_migration.md).
Run the migration preflight before pushing to a new Arthera remote:

```bash
python3 scripts/github_migration_preflight.py
```

---

## PR Checklist

- [ ] Tests pass: `pytest tests/ -v`
- [ ] Code formatted: `black . --check`
- [ ] No hardcoded API keys, IPs, or credentials
- [ ] New features have at least one test
- [ ] `requirements.txt` updated if new dependency added
- [ ] Graceful fallback if optional dependency is missing

---

## Adding a Financial Tool

All local financial calculations live in `local_finance_tools.py`. To add one:

```python
def calculate_your_metric(param1: float, param2: float) -> dict:
    """
    Brief description. Formula: result = param1 / param2
    
    Args:
        param1: Description of param1
        param2: Description of param2
    
    Returns:
        dict with keys: result, formula, interpretation
    """
    result = param1 / param2
    return {
        "result": result,
        "formula": "result = param1 / param2",
        "interpretation": f"Value is {result:.2f}"
    }
```

Then register it in `aria_cli.py` in the `_TOOL_REGISTRY` dict so the LLM can call it.

---

## Security

**Never commit:**
- API keys or tokens
- `.env` files (only `.env.example`)
- Private keys (`.pem`, `.key`)
- Hardcoded server IPs

Report security vulnerabilities to: `security@arthera.finance`

---

## License

By contributing, you agree your contributions will be licensed under the MIT License.
