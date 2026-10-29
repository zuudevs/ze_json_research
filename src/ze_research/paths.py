"""External data path configuration and repository separation checks."""

from __future__ import annotations

import os
from pathlib import Path


class PathConfigurationError(ValueError):
    """Raised when configured paths violate repository separation."""


def research_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_data_dir(value: str | Path | None = None) -> Path:
    raw = str(value) if value is not None else os.environ.get("ZE_JSON_RESEARCH_HOME")
    if not raw:
        raise PathConfigurationError(
            "Data directory belum ditetapkan. Gunakan --data-dir atau ZE_JSON_RESEARCH_HOME."
        )
    return Path(raw).expanduser().resolve()


def resolve_source_dir(value: str | Path | None = None) -> Path:
    raw = str(value) if value is not None else os.environ.get("ZE_JSON_SOURCE_DIR")
    if not raw:
        raise PathConfigurationError(
            "Source directory belum ditetapkan. Gunakan --source-dir atau ZE_JSON_SOURCE_DIR."
        )
    path = Path(raw).expanduser().resolve()
    if not path.is_dir():
        raise PathConfigurationError(f"Source directory tidak ditemukan: {path}")
    if not (path / ".git").exists() and not (path / ".git").is_file():
        raise PathConfigurationError(f"Source directory bukan checkout Git: {path}")
    return path


def is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def validate_external_data_dir(
    data_dir: Path, *, source_dir: Path | None = None, extra_forbidden_roots: tuple[Path, ...] = ()
) -> None:
    resolved = data_dir.expanduser().resolve()
    forbidden = [research_repo_root()]
    if source_dir is not None:
        forbidden.append(source_dir.expanduser().resolve())
    forbidden.extend(root.expanduser().resolve() for root in extra_forbidden_roots)

    for root in forbidden:
        if is_within(resolved, root):
            raise PathConfigurationError(
                f"Data/build directory harus berada di luar repository. "
                f"{resolved} berada di dalam {root}"
            )


def init_data_layout(data_dir: Path) -> None:
    directories = (
        "database",
        "runs",
        "artifacts/benchmark",
        "artifacts/test",
        "artifacts/profiling",
        "artifacts/coverage",
        "artifacts/static-analysis",
        "artifacts/sanitizer",
        "builds",
        "reports",
        "staging",
    )
    for relative in directories:
        (data_dir / relative).mkdir(parents=True, exist_ok=True)
