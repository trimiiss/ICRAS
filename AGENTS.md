# AGENTS.md — ICRAS Development Guidelines

## Rules for All Contributors

1. **Run `pytest` after every change.** All tests must pass before committing.
2. **Never commit secrets.** No API keys, tokens, or credentials in source code. Use `.env` (which is gitignored).
3. **Keep agent modules separate.** Each agent lives in its own file under `agents/`. Do not merge agent logic.
4. **Write generated runtime files only inside `runs/<run_id>/`.** Never write runtime output to `data/`, `icras/`, or the project root.
5. **Use Pydantic models for structured agent inputs and outputs.** All inter-agent data must be validated through schemas in `schemas/`.
6. **Do not add LLM calls until a later user story requires them.** Sprint 1 is foundation only — no OpenAI, LangChain, or external AI services.
7. **Use type hints everywhere.** Every function signature must have type annotations.
8. **Write clear error messages.** Exceptions should tell the developer exactly what went wrong and how to fix it.
