import logging
from contextlib import closing
from pathlib import Path
from typing import Any, List, Tuple

import pandas as pd
import psycopg2
from psycopg2 import sql
from psycopg2.extras import execute_batch

from ingestion_helpers import (
    EXTERNAL_ARCHIVE_DIR,
    archive_pending_file,
    connection_kwargs,
    pending_named_file,
    safe_error_code,
)


SCHEMA_NAME = "external"
TABLE_NAME = "friction_index"
BATCH_SIZE = 500

LOGGER = logging.getLogger("external_friction_index_ingest")

SOURCE_FILES = {
    "academic": "FrictionIndex_Academic.xlsx",
    "operational": "FrictionIndex_Operational.xlsx",
    "pagasa": "FrictionIndex_PagASA.xlsx",
}

REQUIRED_COLUMNS = {
    "Trigger Category",
    "Specific Condition (API Input)",
    "Friction Weight (0.0 to 1.0)",
    "NCR-Based Literature Source / Basis",
    "Open-Access Link",
}


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def required_text(value: Any, max_length: int = 4096) -> str:
    if pd.isna(value):
        raise ValueError("Required text value is missing.")
    text = str(value).strip()
    if not text or len(text) > max_length:
        raise ValueError("Invalid text value.")
    return text


def friction_weight(value: Any) -> float:
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed) or parsed < 0 or parsed > 1:
        raise ValueError("Invalid friction weight.")
    return float(parsed)


def pending_sources() -> List[Tuple[str, Path]]:
    sources: List[Tuple[str, Path]] = []
    for domain, filename in SOURCE_FILES.items():
        source_file = pending_named_file(filename, "External")
        if source_file:
            sources.append((domain, source_file))
    return sources


def read_source_records(domain: str, path: Path) -> List[Tuple[Any, ...]]:
    frame = pd.read_excel(path, header=0).dropna(how="all")
    if not REQUIRED_COLUMNS.issubset(set(frame.columns)):
        raise ValueError("External friction workbook is missing required columns.")

    records: List[Tuple[Any, ...]] = []
    for sequence, (_, row) in enumerate(frame.iterrows(), start=1):
        records.append(
            (
                f"FRI-{domain.upper()}-{sequence:04d}",
                domain,
                required_text(row["Trigger Category"]),
                required_text(row["Specific Condition (API Input)"]),
                friction_weight(row["Friction Weight (0.0 to 1.0)"]),
                required_text(row["NCR-Based Literature Source / Basis"]),
                required_text(row["Open-Access Link"]),
            )
        )

    if not records:
        raise ValueError("No external friction records were parsed.")
    return records


def archive_source_file(source_path: Path) -> None:
    archive_pending_file(source_path, EXTERNAL_ARCHIVE_DIR)


def create_schema(cursor: psycopg2.extensions.cursor) -> None:
    cursor.execute(
        sql.SQL("CREATE SCHEMA IF NOT EXISTS {schema}").format(
            schema=sql.Identifier(SCHEMA_NAME)
        )
    )
    cursor.execute(
        sql.SQL("REVOKE ALL ON SCHEMA {schema} FROM PUBLIC").format(
            schema=sql.Identifier(SCHEMA_NAME)
        )
    )


def create_table(cursor: psycopg2.extensions.cursor) -> None:
    create_schema(cursor)
    cursor.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {table} (
                id TEXT PRIMARY KEY,
                friction_domain TEXT NOT NULL,
                trigger_category TEXT NOT NULL,
                specific_condition_api_input TEXT NOT NULL,
                friction_weight NUMERIC NOT NULL CHECK (friction_weight >= 0 AND friction_weight <= 1),
                ncr_literature_source_basis TEXT NOT NULL,
                open_access_link TEXT NOT NULL,
                load_timestamp TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        ).format(table=sql.Identifier(SCHEMA_NAME, TABLE_NAME))
    )
    cursor.execute(sql.SQL("REVOKE ALL ON TABLE {table} FROM PUBLIC").format(table=sql.Identifier(SCHEMA_NAME, TABLE_NAME)))


def insert_records(cursor: psycopg2.extensions.cursor, records: List[Tuple[Any, ...]]) -> None:
    columns = (
        "id",
        "friction_domain",
        "trigger_category",
        "specific_condition_api_input",
        "friction_weight",
        "ncr_literature_source_basis",
        "open_access_link",
    )
    statement = sql.SQL(
        """
        INSERT INTO {table} ({columns})
        VALUES ({placeholders})
        ON CONFLICT (id) DO NOTHING
        """
    ).format(
        table=sql.Identifier(SCHEMA_NAME, TABLE_NAME),
        columns=sql.SQL(", ").join(sql.Identifier(column) for column in columns),
        placeholders=sql.SQL(", ").join(sql.Placeholder() for _ in columns),
    )
    execute_batch(cursor, statement.as_string(cursor), records, page_size=BATCH_SIZE)


def run() -> int:
    configure_logging()
    try:
        sources = pending_sources()
        if not sources:
            LOGGER.info("External friction source files are not pending. Skipping.")
            return 0

        records: List[Tuple[Any, ...]] = []
        for domain, path in sources:
            records.extend(read_source_records(domain, path))

        with closing(psycopg2.connect(**connection_kwargs())) as connection:
            connection.autocommit = False
            with connection.cursor() as cursor:
                create_table(cursor)
                insert_records(cursor, records)
            connection.commit()

        for _, path in sources:
            archive_source_file(path)

        LOGGER.info("External friction index ingestion completed. files=%s rows=%s", len(sources), len(records))
        return 0
    except Exception as error:
        LOGGER.error("External friction index ingestion failed securely. error_type=%s", safe_error_code(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
