"""Shared helpers for run-local artifact file handling."""

import json
from pathlib import Path
from typing import Any


def validate_run_dir(
    run_dir: str | Path,
    *,
    error_type: type[Exception],
    before_action: str,
    trailing_period: bool = False,
) -> Path:
    """Return a valid run directory or raise the caller's error type."""
    run_path = Path(run_dir).resolve()
    if not run_path.exists():
        raise error_type(
            f"Run directory does not exist: {run_path}. "
            f"Create it with create_run_folder before {before_action}."
        )
    if not run_path.is_dir():
        suffix = "." if trailing_period else ""
        raise error_type(f"Run path is not a directory: {run_path}{suffix}")
    return run_path


def write_model_json(
    path: Path,
    model: Any,
    *,
    error_type: type[Exception] | None = None,
    failure_message: str | None = None,
    default: Any = None,
    ensure_ascii: bool = False,
    trailing_newline: bool = True,
) -> None:
    """Write a Pydantic model as deterministic, formatted JSON."""
    try:
        with open(path, "w", encoding="utf-8") as file:
            json.dump(
                model.model_dump(mode="json"),
                file,
                indent=2,
                ensure_ascii=ensure_ascii,
                default=default,
            )
            if trailing_newline:
                file.write("\n")
    except Exception as exc:
        if error_type is None:
            raise
        message = failure_message or f"Failed to write artifact '{path}': {exc}"
        raise error_type(message.format(path=path, exc=exc)) from exc
