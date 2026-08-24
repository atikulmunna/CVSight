import pytest

from shelfsight_api.config import (
    ConfigurationError,
    get_database_url,
    get_import_root,
    get_media_root,
)
from shelfsight_api.database import database_is_ready, get_engine


def test_database_url_is_required() -> None:
    with pytest.raises(ConfigurationError, match="SHELFSIGHT_DATABASE_URL is required"):
        get_database_url({})


def test_database_url_must_be_valid() -> None:
    with pytest.raises(ConfigurationError, match="SHELFSIGHT_DATABASE_URL is invalid"):
        get_database_url({"SHELFSIGHT_DATABASE_URL": "not a URL"})


def test_database_url_must_use_postgresql() -> None:
    with pytest.raises(ConfigurationError, match="must use PostgreSQL"):
        get_database_url({"SHELFSIGHT_DATABASE_URL": "sqlite:///local.db"})


def test_database_url_accepts_psycopg_driver() -> None:
    value = "postgresql+psycopg://user:password@localhost:5432/shelfsight"

    assert get_database_url({"SHELFSIGHT_DATABASE_URL": value}) == value


def test_database_readiness_hides_connection_errors(monkeypatch) -> None:
    monkeypatch.delenv("SHELFSIGHT_DATABASE_URL", raising=False)
    get_engine.cache_clear()

    assert database_is_ready() is False


def test_media_and_import_roots_are_required_and_resolved(tmp_path) -> None:
    with pytest.raises(ConfigurationError, match="SHELFSIGHT_MEDIA_ROOT is required"):
        get_media_root({})
    with pytest.raises(ConfigurationError, match="SHELFSIGHT_IMPORT_ROOT is required"):
        get_import_root({})

    assert get_media_root({"SHELFSIGHT_MEDIA_ROOT": str(tmp_path / "media")}) == (
        tmp_path / "media"
    ).resolve()
    assert get_import_root({"SHELFSIGHT_IMPORT_ROOT": str(tmp_path / "imports")}) == (
        tmp_path / "imports"
    ).resolve()


def test_managed_roots_cannot_be_filesystem_roots() -> None:
    with pytest.raises(ConfigurationError, match="cannot be a filesystem root"):
        get_media_root({"SHELFSIGHT_MEDIA_ROOT": "C:\\"})
