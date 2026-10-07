from feature_factory.config import Settings
from feature_factory.db import build_engine
import pytest
from pathlib import Path


def test_default_settings_use_postgresql_database_url(monkeypatch) -> None:
    monkeypatch.delenv("FEATURE_FACTORY_DATABASE_URL", raising=False)
    settings = Settings(_env_file=None)

    assert (
        settings.database_url
        == "postgresql+psycopg://feature_factory:feature_factory@127.0.0.1:55432/feature_factory"
    )
    assert settings.stage1_default_max_concurrent_partitions_per_job == 8
    assert settings.github_incomplete_results_retries == 3
    assert settings.stage2_openhands_preset == "default"
    assert settings.default_data_pool_root == Path("./data-pools/default")


def test_llm_ssl_verify_defaults_enabled(monkeypatch) -> None:
    monkeypatch.delenv("FEATURE_FACTORY_LLM_SSL_VERIFY", raising=False)
    monkeypatch.delenv("SSL_VERIFY", raising=False)

    settings = Settings(_env_file=None)

    assert settings.llm_ssl_verify is True


def test_llm_ssl_verify_loads_from_explicit_dotenv(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("FEATURE_FACTORY_LLM_SSL_VERIFY", raising=False)
    monkeypatch.delenv("SSL_VERIFY", raising=False)
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "FEATURE_FACTORY_LLM_SSL_VERIFY=false\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=dotenv_path)

    assert settings.llm_ssl_verify is False


def test_llm_ssl_verify_accepts_legacy_litellm_env(monkeypatch) -> None:
    monkeypatch.delenv("FEATURE_FACTORY_LLM_SSL_VERIFY", raising=False)
    monkeypatch.setenv("SSL_VERIFY", "false")

    settings = Settings(_env_file=None)

    assert settings.llm_ssl_verify is False


def test_llm_ssl_verify_preserves_legacy_ca_bundle_path(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("FEATURE_FACTORY_LLM_SSL_VERIFY", raising=False)
    ca_bundle = tmp_path / "llm-ca.pem"
    monkeypatch.setenv("SSL_VERIFY", str(ca_bundle))

    settings = Settings(_env_file=None)

    assert settings.llm_ssl_verify == ca_bundle


def test_settings_can_disable_implicit_dotenv(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FEATURE_FACTORY_DATABASE_URL", raising=False)
    monkeypatch.setenv("FEATURE_FACTORY_DISABLE_DOTENV", "1")
    (tmp_path / ".env").write_text("FEATURE_FACTORY_DATABASE_URL=sqlite:///from-dotenv.db\n", encoding="utf-8")

    settings = Settings()

    assert settings.database_url.startswith("postgresql+psycopg://")


def test_settings_explicit_env_file_still_loads_when_implicit_dotenv_is_disabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FEATURE_FACTORY_DISABLE_DOTENV", "1")
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("FEATURE_FACTORY_DATABASE_URL=sqlite:///explicit-dotenv.db\n", encoding="utf-8")

    settings = Settings(_env_file=dotenv_path)

    assert settings.database_url == "sqlite:///explicit-dotenv.db"


def test_sqlite_file_engine_enables_busy_timeout_and_wal(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'feature_factory.db'}",
        sqlite_busy_timeout_seconds=12.0,
        sqlite_enable_wal=True,
    )

    engine = build_engine(settings)
    try:
        with engine.connect() as connection:
            busy_timeout = connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one()
            journal_mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()

        assert busy_timeout == 12000
        assert str(journal_mode).lower() == "wal"
    finally:
        engine.dispose()


def test_postgresql_engine_can_be_constructed_without_sqlite_pragmas() -> None:
    settings = Settings(
        database_url="postgresql+psycopg://feature_factory:feature_factory@127.0.0.1:5432/feature_factory"
    )

    engine = build_engine(settings)
    try:
        assert engine.dialect.name == "postgresql"
    finally:
        engine.dispose()


def test_settings_reject_partition_default_above_cap() -> None:
    with pytest.raises(ValueError, match="stage1_default_max_concurrent_partitions_per_job"):
        Settings(
            database_url="sqlite://",
            stage1_default_max_concurrent_partitions_per_job=8,
            stage1_max_concurrent_partitions_per_job_cap=1,
        )


def test_default_system_capacity_and_task_quota_values() -> None:
    settings = Settings(database_url="sqlite://", _env_file=None)

    assert settings.stage1_max_concurrent_jobs == 4
    assert settings.stage1_max_concurrent_jobs_cap == 24
    assert settings.stage2_max_concurrent_runs == 24
    assert settings.stage3_max_concurrent_runs == 24
    assert settings.stage4_max_concurrent_runs == 24
    assert settings.stage2_default_task_max_concurrent_runs == 4
    assert settings.stage3_default_task_max_concurrent_runs == 4
    assert settings.stage4_default_task_max_concurrent_runs == 4


def test_task_quota_default_cannot_exceed_system_capacity() -> None:
    with pytest.raises(ValueError, match="stage2_default_task_max_concurrent_runs"):
        Settings(
            database_url="sqlite://",
            stage2_max_concurrent_runs=4,
            stage2_default_task_max_concurrent_runs=5,
        )