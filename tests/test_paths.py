from pathlib import Path

import pytest

from ze_research.paths import (
    PathConfigurationError,
    is_within,
    resolve_data_dir,
    validate_external_data_dir,
)


def test_data_dir_must_be_configured(monkeypatch):
    monkeypatch.delenv("ZE_JSON_RESEARCH_HOME", raising=False)
    with pytest.raises(PathConfigurationError):
        resolve_data_dir()


def test_data_dir_inside_research_repository_is_rejected(tmp_path):
    from ze_research.paths import research_repo_root

    data_dir = research_repo_root() / ".tmp-research-data"
    with pytest.raises(PathConfigurationError):
        validate_external_data_dir(data_dir)


def test_data_dir_inside_source_repository_is_rejected(tmp_path):
    source = tmp_path / "ze_json"
    source.mkdir()
    with pytest.raises(PathConfigurationError):
        validate_external_data_dir(source / "out", source_dir=source)


def test_sibling_data_directory_is_allowed(tmp_path):
    source = tmp_path / "ze_json"
    research = tmp_path / "ze_json_research"
    data = tmp_path / "research-data"
    source.mkdir()
    research.mkdir()
    validate_external_data_dir(data, source_dir=source)
    assert not is_within(data, source)
    assert not is_within(data, research)
