import logging
from contextlib import closing
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


SOURCE_FILENAME = "APTA_Protocols.xlsx"
SCHEMA_NAME = "APTA"
TABLE_NAME = "apta_protocols"
BATCH_SIZE = 500

LOGGER = logging.getLogger("apta_protocols_ingest")

SOURCE_COLUMNS = {
    "APTA Standard Code": "apta_standard_code",
    "Official Document Title": "official_document_title",
    "Scope & Relevance to Surge Management": "scope_relevance_to_surge_management",
    "Strictly Human-Centric (Man-Protocol) Ground Tactics Justified": "human_centric_ground_tactics",
    "Open-Access Link / Source": "open_access_link_source",
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


def read_records(source_file) -> List[Tuple[Any, ...]]:
    frame = pd.read_excel(source_file, header=0).dropna(how="all")
    missing_columns = set(SOURCE_COLUMNS) - set(frame.columns)
    if missing_columns:
        raise ValueError("APTA protocols workbook is missing required columns.")

    records: List[Tuple[Any, ...]] = []
    for sequence, (_, row) in enumerate(frame.iterrows(), start=1):
        records.append(
            (
                f"APTA-{sequence:04d}",
                required_text(row["APTA Standard Code"], max_length=128),
                required_text(row["Official Document Title"]),
                required_text(row["Scope & Relevance to Surge Management"]),
                required_text(row["Strictly Human-Centric (Man-Protocol) Ground Tactics Justified"]),
                required_text(row["Open-Access Link / Source"]),
            )
        )

    if not records:
        raise ValueError("No APTA protocol records were parsed.")
    return records


def archive_source_file(source_file) -> None:
    archive_pending_file(source_file, EXTERNAL_ARCHIVE_DIR)


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
                apta_standard_code TEXT NOT NULL,
                official_document_title TEXT NOT NULL,
                scope_relevance_to_surge_management TEXT NOT NULL,
                human_centric_ground_tactics TEXT NOT NULL,
                open_access_link_source TEXT NOT NULL,
                load_timestamp TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        ).format(table=sql.Identifier(SCHEMA_NAME, TABLE_NAME))
    )
    cursor.execute(sql.SQL("REVOKE ALL ON TABLE {table} FROM PUBLIC").format(table=sql.Identifier(SCHEMA_NAME, TABLE_NAME)))


def insert_records(cursor: psycopg2.extensions.cursor, records: List[Tuple[Any, ...]]) -> None:
    columns = (
        "id",
        "apta_standard_code",
        "official_document_title",
        "scope_relevance_to_surge_management",
        "human_centric_ground_tactics",
        "open_access_link_source",
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
        source_file = pending_named_file(SOURCE_FILENAME, "External")
        if not source_file:
            LOGGER.info("APTA protocols source file is not pending. Skipping.")
            return 0
        records = read_records(source_file)
        with closing(psycopg2.connect(**connection_kwargs())) as connection:
            connection.autocommit = False
            with connection.cursor() as cursor:
                create_table(cursor)
                insert_records(cursor, records)
            connection.commit()
        archive_source_file(source_file)
        LOGGER.info("APTA protocols ingestion completed. rows=%s", len(records))
        return 0
    except Exception as error:
        LOGGER.error("APTA protocols ingestion failed securely. error_type=%s", safe_error_code(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
