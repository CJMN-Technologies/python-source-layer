import logging
from contextlib import closing
from typing import Any, List, Tuple

import pandas as pd
import psycopg2
from psycopg2 import sql
from psycopg2.extras import execute_batch

from ingest_raw_internal import (
    INTERNAL_ARCHIVE_DIR,
    archive_pending_file,
    connection_kwargs,
    pending_named_file,
    safe_error_code,
)


SOURCE_FILENAME = "PSOR_Incidents.xlsx"
SCHEMA_NAME = "PSOR"
TABLE_NAME = "psor_incidents"
BATCH_SIZE = 500

LOGGER = logging.getLogger("psor_incidents_ingest")


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def required_text(value: Any, max_length: int = 256) -> str:
    if pd.isna(value):
        raise ValueError("Required text value is missing.")
    text = str(value).strip()
    if not text or len(text) > max_length:
        raise ValueError("Invalid text value.")
    return text


def read_records(source_file) -> List[Tuple[Any, ...]]:
    frame = pd.read_excel(source_file, header=0).dropna(how="all")
    required_columns = {"Category", "Specific Incident / Transgression"}
    if not required_columns.issubset(set(frame.columns)):
        raise ValueError("PSOR incidents workbook is missing required columns.")

    records: List[Tuple[Any, ...]] = []
    for sequence, (_, row) in enumerate(frame.iterrows(), start=1):
        records.append(
            (
                f"PSOR-{sequence:04d}",
                required_text(row["Category"]),
                required_text(row["Specific Incident / Transgression"]),
            )
        )

    if not records:
        raise ValueError("No PSOR incident records were parsed.")
    return records


def archive_source_file(source_file) -> None:
    archive_pending_file(source_file, INTERNAL_ARCHIVE_DIR)


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
                category TEXT NOT NULL,
                specific_incident_transgression TEXT NOT NULL,
                load_timestamp TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        ).format(table=sql.Identifier(SCHEMA_NAME, TABLE_NAME))
    )
    cursor.execute(sql.SQL("REVOKE ALL ON TABLE {table} FROM PUBLIC").format(table=sql.Identifier(SCHEMA_NAME, TABLE_NAME)))


def insert_records(cursor: psycopg2.extensions.cursor, records: List[Tuple[Any, ...]]) -> None:
    columns = ("id", "category", "specific_incident_transgression")
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
        source_file = pending_named_file(SOURCE_FILENAME, "Internal")
        if not source_file:
            LOGGER.info("PSOR incidents source file is not pending. Skipping.")
            return 0
        records = read_records(source_file)
        with closing(psycopg2.connect(**connection_kwargs())) as connection:
            connection.autocommit = False
            with connection.cursor() as cursor:
                create_table(cursor)
                insert_records(cursor, records)
            connection.commit()
        archive_source_file(source_file)
        LOGGER.info("PSOR incidents ingestion completed. rows=%s", len(records))
        return 0
    except Exception as error:
        LOGGER.error("PSOR incidents ingestion failed securely. error_type=%s", safe_error_code(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
