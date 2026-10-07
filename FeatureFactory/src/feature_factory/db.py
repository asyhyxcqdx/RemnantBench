from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from feature_factory.config import Settings, get_settings


class Base(DeclarativeBase):
    pass


def build_engine(settings: Settings | None = None) -> Engine:
    settings = settings or get_settings()
    connect_args = {}
    sqlite_is_file = False
    engine_kwargs: dict[str, Any] = {
        "future": True,
        "connect_args": connect_args,
        "pool_pre_ping": True,
    }
    if settings.database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        connect_args["timeout"] = settings.sqlite_busy_timeout_seconds
        url = make_url(settings.database_url)
        if url.database and url.database != ":memory:":
            sqlite_is_file = True
            db_path = Path(url.database)
            if not db_path.is_absolute():
                db_path = Path.cwd() / db_path
            db_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        engine_kwargs.update(
            {
                "pool_size": settings.database_pool_size,
                "max_overflow": settings.database_max_overflow,
                "pool_timeout": settings.database_pool_timeout_seconds,
                "pool_recycle": settings.database_pool_recycle_seconds,
            }
        )
    engine = create_engine(settings.database_url, **engine_kwargs)

    if settings.database_url.startswith("sqlite"):
        busy_timeout_ms = int(settings.sqlite_busy_timeout_seconds * 1000)

        @event.listens_for(engine, "connect")
        def configure_sqlite(dbapi_connection, _connection_record) -> None:  # type: ignore[no-redef]
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
                cursor.execute("PRAGMA foreign_keys = ON")
                if sqlite_is_file and settings.sqlite_enable_wal:
                    cursor.execute("PRAGMA journal_mode = WAL")
                    cursor.execute("PRAGMA synchronous = NORMAL")
            finally:
                cursor.close()

    return engine


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )


class InitDbRepairCleanupError(RuntimeError):
    def __init__(self, message: str, *, repaired_duplicate_archives: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.repaired_duplicate_archives = list(repaired_duplicate_archives)


def init_db(
    engine: Engine,
    *,
    repair_active_runs: bool = True,
) -> list[dict[str, Any]]:
    from feature_factory import models  # noqa: F401

    repaired_duplicate_archives: list[dict[str, Any]] = []
    Base.metadata.create_all(bind=engine)
    _ensure_optional_schema_columns(engine)
    if repair_active_runs:
        repaired_duplicate_archives = _repair_stage2_duplicate_active_runs(engine)
        _repair_stage3_duplicate_active_runs(engine)
        _repair_stage4_duplicate_active_runs(engine)
    try:
        _ensure_optional_schema_indexes(engine)
    except Exception as exc:
        raise InitDbRepairCleanupError(
            "database initialization failed after repairing duplicate active runs",
            repaired_duplicate_archives=repaired_duplicate_archives,
        ) from exc
    return repaired_duplicate_archives


def _ensure_optional_schema_columns(engine: Engine) -> None:
    inspector = inspect(engine)
    optional_columns_by_table = {
        "crawl_partition_query_executions": {
            "current_page": "INTEGER",
            "total_pages": "INTEGER",
        },
        "stage2_runs": {
            "runtime_snapshot_json": "JSON",
        },
        "stage2_validation_attempts": {
            "full_report_json": "JSON",
            "checkpoint_json": "JSON",
            "version_finalized": "BOOLEAN DEFAULT FALSE",
        },
        "stage2_test_results": {
            "target_selector": "TEXT",
        },
        "stage3_entry_files": {
            "target_selector": "TEXT",
        },
        "stage3_savepoint_file_results": {
            "target_selector": "TEXT",
        },
    }

    with engine.begin() as connection:
        for table_name, optional_columns in optional_columns_by_table.items():
            if not inspector.has_table(table_name):
                continue
            existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
            missing_columns = {
                column_name: column_type
                for column_name, column_type in optional_columns.items()
                if column_name not in existing_columns
            }
            for column_name, column_type in missing_columns.items():
                try:
                    connection.exec_driver_sql(
                        f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
                    )
                except DBAPIError as exc:
                    if "duplicate column" in str(exc).lower():
                        continue
                    raise


def _repair_stage2_duplicate_active_runs(engine: Engine) -> list[dict[str, Any]]:
    from feature_factory.stage2.service import Stage2Service

    session = build_session_factory(engine)()
    try:
        service = Stage2Service(session)
        archives = list(service.repair_duplicate_active_runs(return_archives=True))
        if archives:
            session.commit()
        return archives
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _repair_stage3_duplicate_active_runs(engine: Engine) -> None:
    from feature_factory.stage3.service import Stage3Service

    session = build_session_factory(engine)()
    try:
        service = Stage3Service(session)
        repaired = service.repair_duplicate_active_runs()
        if repaired:
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _repair_stage4_duplicate_active_runs(engine: Engine) -> None:
    from feature_factory.stage4.service import Stage4Service

    session = build_session_factory(engine)()
    try:
        service = Stage4Service(session)
        repaired = service.repair_duplicate_active_runs()
        if repaired:
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _ensure_optional_schema_indexes(engine: Engine) -> None:
    inspector = inspect(engine)
    if engine.dialect.name not in {"sqlite", "postgresql"}:
        return
    has_stage2_runs = inspector.has_table("stage2_runs")
    has_stage3_runs = inspector.has_table("stage3_runs")
    has_stage4_runs = inspector.has_table("stage4_runs")
    has_stage3_commit_snapshots = inspector.has_table("stage3_commit_snapshots")
    if not has_stage2_runs and not has_stage3_runs and not has_stage4_runs and not has_stage3_commit_snapshots:
        return

    with engine.begin() as connection:
        if has_stage3_commit_snapshots and engine.dialect.name == "postgresql":
            connection.exec_driver_sql(
                """
                ALTER TABLE stage3_commit_snapshots
                DROP CONSTRAINT IF EXISTS uq_stage3_commit_snapshots_repo_commit
                """
            )
        elif has_stage3_commit_snapshots and engine.dialect.name == "sqlite":
            connection.exec_driver_sql(
                "DROP INDEX IF EXISTS uq_stage3_commit_snapshots_repo_commit"
            )
        if has_stage2_runs:
            connection.exec_driver_sql(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_stage2_runs_repository_active
                ON stage2_runs (repository_id)
                WHERE status IN ('queued', 'running')
                """
            )
        if has_stage3_runs:
            connection.exec_driver_sql(
                "DROP INDEX IF EXISTS uq_stage3_runs_entry_file_active"
            )
            connection.exec_driver_sql(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_stage3_runs_entry_file_active
                ON stage3_runs (entry_file_id)
                WHERE status IN ('pending', 'queued', 'running')
                """
            )
        if has_stage4_runs:
            connection.exec_driver_sql(
                "DROP INDEX IF EXISTS uq_stage4_runs_source_savepoint_active"
            )
            connection.exec_driver_sql(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_stage4_runs_source_savepoint_active
                ON stage4_runs (source_savepoint_id)
                WHERE status IN ('pending', 'queued', 'running')
                """
            )


@contextmanager
def session_scope(settings: Settings | None = None) -> Iterator[Session]:
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
