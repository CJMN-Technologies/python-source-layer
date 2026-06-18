import logging
from contextlib import closing
from typing import Any, List, Optional, Tuple

import pandas as pd
import psycopg2
from psycopg2 import sql
from psycopg2.extras import execute_batch

from ingest_raw_internal import (
    INTERNAL_ARCHIVE_DIR,
    SCHEMA_NAME,
    archive_pending_file,
    connection_kwargs,
    create_schema,
    pending_named_file,
    safe_error_code,
)


SOURCE_FILENAME = "Student_Transaction.xlsx"
TABLE_NAME = "student_transactions"
BATCH_SIZE = 500

LOGGER = logging.getLogger("student_transaction_ingest")

MONTHS = {
    "JANUARY": 1,
    "FEBRUARY": 2,
    "MARCH": 3,
    "APRIL": 4,
    "MAY": 5,
    "JUNE": 6,
    "JULY": 7,
    "AUGUST": 8,
    "SEPTEMBER": 9,
    "OCTOBER": 10,
    "NOVEMBER": 11,
    "DECEMBER": 12,
}


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def optional_int(value: Any) -> Optional[int]:
    if pd.isna(value):
        return None
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if not cleaned or not any(character.isdigit() for character in cleaned):
            return None
        value = cleaned
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        return None
    if int(parsed) != parsed or parsed < 0:
        raise ValueError("Invalid transaction count.")
    return int(parsed)


def parse_year(value: Any) -> int:
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError("Invalid year column.")
    return int(parsed)


def read_records(source_file) -> List[Tuple[Any, ...]]:
    frame = pd.read_excel(source_file, header=0)
    if frame.empty or len(frame.columns) < 2:
        raise ValueError("Student transaction workbook has no usable data.")

    month_column = frame.columns[0]
    records: List[Tuple[Any, ...]] = []

    for year_column in frame.columns[1:]:
        year = parse_year(year_column)
        sequence = 1
        for _, row in frame.iterrows():
            label = str(row[month_column]).strip().upper() if not pd.isna(row[month_column]) else ""
            if label in MONTHS:
                value = optional_int(row[year_column])
                if value is None:
                    continue
                records.append(
                    (
                        f"STU{year}-{sequence:04d}",
                        year,
                        MONTHS[label],
                        label.title(),
                        False,
                        value,
                    )
                )
                sequence += 1
            elif label == "TOTAL":
                value = optional_int(row[year_column])
                if value is not None:
                    records.append(
                        (
                            f"STU{year}-TOTAL",
                            year,
                            None,
                            "Total",
                            True,
                            value,
                        )
                    )

    if not records:
        raise ValueError("No student transaction records were parsed.")
    return records


def archive_source_file(source_file) -> None:
    archive_pending_file(source_file, INTERNAL_ARCHIVE_DIR)


def create_table(cursor: psycopg2.extensions.cursor) -> None:
    create_schema(cursor)
    cursor.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {table} (
                id TEXT PRIMARY KEY,
                year INTEGER NOT NULL CHECK (year >= 2000),
                month_number INTEGER CHECK (month_number BETWEEN 1 AND 12),
                month_name TEXT NOT NULL,
                is_total BOOLEAN NOT NULL DEFAULT FALSE,
                student_transactions INTEGER NOT NULL CHECK (student_transactions >= 0),
                load_timestamp TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        ).format(table=sql.Identifier(SCHEMA_NAME, TABLE_NAME))
    )
    cursor.execute(sql.SQL("REVOKE ALL ON TABLE {table} FROM PUBLIC").format(table=sql.Identifier(SCHEMA_NAME, TABLE_NAME)))


def insert_records(cursor: psycopg2.extensions.cursor, records: List[Tuple[Any, ...]]) -> None:
    columns = ("id", "year", "month_number", "month_name", "is_total", "student_transactions")
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
            LOGGER.info("Student transaction source file is not pending. Skipping.")
            return 0
        records = read_records(source_file)
        with closing(psycopg2.connect(**connection_kwargs())) as connection:
            connection.autocommit = False
            with connection.cursor() as cursor:
                create_table(cursor)
                insert_records(cursor, records)
            connection.commit()
        archive_source_file(source_file)
        LOGGER.info("Student transaction ingestion completed. rows=%s", len(records))
        return 0
    except Exception as error:
        LOGGER.error("Student transaction ingestion failed securely. error_type=%s", safe_error_code(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
