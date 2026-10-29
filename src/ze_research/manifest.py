"""Manifest generation, source provenance, artifact hashing, and verification."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .registry import definition_sha256


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _git(source_dir: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=source_dir,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} gagal di {source_dir}: {result.stderr.strip()}")
    return result.stdout.strip()


def capture_source_info(source_dir: Path) -> dict[str, Any]:
    root = Path(_git(source_dir, "rev-parse", "--show-toplevel")).resolve()
    if root != source_dir.resolve():
        raise RuntimeError(f"Source directory harus root checkout Git: {root}")
    commit = _git(source_dir, "rev-parse", "HEAD")
    branch_result = subprocess.run(
        ["git", "branch", "--show-current"], cwd=source_dir,
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    branch = branch_result.stdout.strip() or None
    status = _git(source_dir, "status", "--porcelain=v1", "--untracked-files=all")
    tracked_diff = _git(source_dir, "diff", "--binary", "HEAD", "--")
    untracked_hashes: dict[str, str] = {}
    for line in status.splitlines():
        if line.startswith("?? "):
            relative = line[3:]
            candidate = (source_dir / relative).resolve()
            try:
                candidate.relative_to(source_dir.resolve())
            except ValueError:
                continue
            if candidate.is_file() and not candidate.is_symlink():
                untracked_hashes[relative] = sha256_file(candidate)

    submodule_result = subprocess.run(
        ["git", "submodule", "status", "--recursive"],
        cwd=source_dir, capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=False,
    )
    origin_result = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"],
        cwd=source_dir, capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=False,
    )
    return {
        "repository_root": str(root),
        "repository_url": origin_result.stdout.strip() if origin_result.returncode == 0 else None,
        "git_commit": commit,
        "branch": branch,
        "worktree_clean": not status,
        "status_porcelain": status,
        "tracked_diff_sha256": sha256_text(tracked_diff) if tracked_diff else None,
        "untracked_file_sha256": untracked_hashes,
        "submodule_status": submodule_result.stdout.strip() if submodule_result.returncode == 0 else None,
    }


def collect_artifacts(run_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        if path.name in {"manifest.json", "manifest.json.tmp"}:
            continue
        relative = path.resolve().relative_to(run_dir.resolve()).as_posix()
        if relative.startswith("raw/"):
            kind = "raw_result"
        elif relative.startswith("logs/"):
            kind = "execution_log"
        elif relative.startswith("metadata/"):
            kind = "run_metadata"
        else:
            kind = "run_file"
        records.append({
            "path": relative,
            "kind": kind,
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        })
    return records


def verify_manifest(manifest_path: Path) -> dict[str, Any]:
    manifest_path = manifest_path.expanduser().resolve()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "valid": False,
            "manifest_path": str(manifest_path),
            "verification_status": "FAILED",
            "checked_artifacts": 0,
            "errors": [f"Manifest tidak dapat dibaca: {exc}"],
        }
    if not isinstance(manifest, dict):
        return {
            "valid": False,
            "manifest_path": str(manifest_path),
            "verification_status": "FAILED",
            "checked_artifacts": 0,
            "errors": ["Root manifest harus berupa object."],
        }

    errors: list[str] = []
    run_dir = manifest_path.parent.resolve()
    artifacts = manifest.get("artifacts", [])
    if not isinstance(artifacts, list):
        artifacts = []
        errors.append("manifest.artifacts harus berupa array.")
    checked = 0
    seen_paths: set[str] = set()
    for record in artifacts:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            errors.append("Entry artefak tidak valid.")
            continue
        relative = PurePosixPath(record["path"])
        if relative.is_absolute() or ".." in relative.parts:
            errors.append(f"Path artefak tidak aman: {record['path']}")
            continue
        if record["path"] in seen_paths:
            errors.append(f"Path artefak duplikat: {record['path']}")
            continue
        seen_paths.add(record["path"])
        unresolved = run_dir / Path(*relative.parts)
        if unresolved.is_symlink():
            errors.append(f"Symlink artefak tidak dipercaya: {record['path']}")
            continue
        candidate = unresolved.resolve()
        try:
            candidate.relative_to(run_dir)
        except ValueError:
            errors.append(f"Path artefak keluar dari run directory: {record['path']}")
            continue
        if not candidate.is_file() or candidate.is_symlink():
            errors.append(f"Artefak tidak ditemukan atau bukan file biasa: {record['path']}")
            continue
        checked += 1
        if sha256_file(candidate) != record.get("sha256"):
            errors.append(f"SHA-256 tidak cocok: {record['path']}")
        if candidate.stat().st_size != record.get("size_bytes"):
            errors.append(f"Ukuran file tidak cocok: {record['path']}")

    expected_outputs = manifest.get("expected_outputs", [])
    if isinstance(expected_outputs, list):
        for output in expected_outputs:
            if not isinstance(output, str):
                errors.append("expected_outputs harus berisi string.")
                continue
            relative = PurePosixPath(output)
            if relative.is_absolute() or ".." in relative.parts:
                errors.append(f"Expected output path tidak aman: {output}")
                continue
            unresolved = run_dir / Path(*relative.parts)
            if unresolved.is_symlink():
                errors.append(f"Symlink expected output tidak dipercaya: {output}")
                continue
            candidate = unresolved.resolve()
            try:
                candidate.relative_to(run_dir)
            except ValueError:
                errors.append(f"Expected output keluar dari run directory: {output}")
                continue
            if not candidate.is_file():
                errors.append(f"Expected output belum tersedia: {output}")

    snapshot = manifest.get("definition_snapshot")
    declared_hash = manifest.get("definition_sha256")
    if not isinstance(snapshot, dict) or not declared_hash:
        errors.append("Snapshot definisi atau fingerprint tidak tersedia.")
    elif definition_sha256(snapshot) != declared_hash:
        errors.append("Fingerprint definisi eksperimen tidak cocok.")

    if manifest.get("run_id") is None or manifest.get("experiment_id") is None:
        errors.append("run_id dan experiment_id wajib tersedia.")

    build = manifest.get("build", {})
    if isinstance(build, dict) and build.get("executable_path") and build.get("binary_sha256"):
        executable = Path(build["executable_path"]).expanduser().resolve()
        if not executable.is_file():
            errors.append(f"Executable tidak ditemukan: {executable}")
        else:
            if sha256_file(executable) != build["binary_sha256"]:
                errors.append("SHA-256 executable tidak cocok.")
            if executable.stat().st_size != build.get("binary_size_bytes"):
                errors.append("Ukuran executable tidak cocok.")

    return {
        "valid": not errors,
        "manifest_path": str(manifest_path),
        "run_id": manifest.get("run_id"),
        "execution_status": manifest.get("status"),
        "verification_status": "VERIFIED" if not errors else "FAILED",
        "checked_artifacts": checked,
        "errors": errors,
    }
