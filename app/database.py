from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.schema import CreateTable

from app.config import DATA_DIR, settings


class Base(DeclarativeBase):
    pass


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)
if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def initialize_database() -> None:
    from app import models  # noqa: F401

    if settings.database_url.startswith("sqlite"):
        database_path = make_url(settings.database_url).database
        if database_path and database_path != ":memory:":
            from pathlib import Path

            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        else:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    if settings.database_url.startswith("sqlite"):
        _migrate_legacy_section_schedule_constraint()


def _migrate_legacy_section_schedule_constraint() -> None:
    inspector = inspect(engine)
    if not inspector.has_table("sections"):
        return

    legacy_columns = ["room_id", "scheduled_at"]
    has_legacy_constraint = any(
        constraint["column_names"] == legacy_columns
        for constraint in inspector.get_unique_constraints("sections")
    )
    has_legacy_index = any(
        index["unique"]
        and index["column_names"] == legacy_columns
        and index.get("dialect_options", {}).get("sqlite_where") is None
        for index in inspector.get_indexes("sections")
    )
    if not (has_legacy_constraint or has_legacy_index):
        return

    section_table = Base.metadata.tables["sections"]
    replacement = section_table.to_metadata(
        section_table.metadata, name="sections_replacement"
    )
    columns = [column.name for column in section_table.columns]
    quoted_columns = ", ".join(
        engine.dialect.identifier_preparer.quote(column) for column in columns
    )

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        try:
            with connection.begin():
                connection.execute(CreateTable(replacement))
                connection.exec_driver_sql(
                    f"INSERT INTO sections_replacement ({quoted_columns}) "
                    f"SELECT {quoted_columns} FROM sections"
                )
                connection.exec_driver_sql("DROP TABLE sections")
                connection.exec_driver_sql(
                    "ALTER TABLE sections_replacement RENAME TO sections"
                )
                for index in section_table.indexes:
                    index.create(connection, checkfirst=True)
                violations = connection.exec_driver_sql(
                    "PRAGMA foreign_key_check"
                ).all()
                if violations:
                    raise RuntimeError(
                        f"SQLite section migration found foreign-key violations: {violations}"
                    )
        finally:
            section_table.metadata.remove(replacement)
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()
