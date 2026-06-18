import logging
from contextlib import closing
from typing import Any, List, Optional, Tuple

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


SOURCE_FILENAME = "Station_and_Platform_Capacity.xlsx"
SCHEMA_NAME = "Station Capacity"
TABLE_NAME = "station_platform_capacity"
BATCH_SIZE = 500

LOGGER = logging.getLogger("station_platform_capacity_ingest")


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def optional_int(value: Any) -> Optional[int]:
    if pd.isna(value):
        return None
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        return None
    if int(parsed) != parsed or parsed < 0:
        raise ValueError("Invalid capacity integer.")
    return int(parsed)


def optional_float(value: Any) -> Optional[float]:
    if pd.isna(value):
        return None
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed) or parsed < 0:
        raise ValueError("Invalid capacity area.")
    return float(parsed)


def required_text(value: Any, max_length: int = 128) -> str:
    if pd.isna(value):
        raise ValueError("Required text value is missing.")
    text = str(value).strip()
    if not text or len(text) > max_length:
        raise ValueError("Invalid text value.")
    return text


def read_records(source_file) -> List[Tuple[Any, ...]]:
    frame = pd.read_excel(source_file, header=0).dropna(how="all")
    required_columns = {
        "Station_Name",
        "Platform_Design",
        "Directional_Usable_Area_m2",
        "Directional_Platform_Limit_Pax",
        "Total_Concourse_Limit_Pax",
        "Total_Station_Limit_Pax",
    }
    if not required_columns.issubset(set(frame.columns)):
        raise ValueError("Station capacity workbook is missing required columns.")

    records: List[Tuple[Any, ...]] = []
    for sequence, (_, row) in enumerate(frame.iterrows(), start=1):
        records.append(
            (
                f"CAP-{sequence:04d}",
                required_text(row["Station_Name"]),
                required_text(row["Platform_Design"]),
                optional_float(row["Directional_Usable_Area_m2"]),
                optional_int(row["Directional_Platform_Limit_Pax"]),
                optional_int(row["Total_Concourse_Limit_Pax"]),
                optional_int(row["Total_Station_Limit_Pax"]),
            )
        )

    if not records:
        raise ValueError("No station capacity records were parsed.")
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
                station_name TEXT NOT NULL,
                platform_design TEXT NOT NULL,
                directional_usable_area_m2 NUMERIC CHECK (directional_usable_area_m2 IS NULL OR directional_usable_area_m2 >= 0),
                directional_platform_limit_pax INTEGER CHECK (directional_platform_limit_pax IS NULL OR directional_platform_limit_pax >= 0),
                total_concourse_limit_pax INTEGER CHECK (total_concourse_limit_pax IS NULL OR total_concourse_limit_pax >= 0),
                total_station_limit_pax INTEGER CHECK (total_station_limit_pax IS NULL OR total_station_limit_pax >= 0),
                load_timestamp TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        ).format(table=sql.Identifier(SCHEMA_NAME, TABLE_NAME))
    )
    cursor.execute(sql.SQL("REVOKE ALL ON TABLE {table} FROM PUBLIC").format(table=sql.Identifier(SCHEMA_NAME, TABLE_NAME)))


def insert_records(cursor: psycopg2.extensions.cursor, records: List[Tuple[Any, ...]]) -> None:
    columns = (
        "id",
        "station_name",
        "platform_design",
        "directional_usable_area_m2",
        "directional_platform_limit_pax",
        "total_concourse_limit_pax",
        "total_station_limit_pax",
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
        source_file = pending_named_file(SOURCE_FILENAME, "Internal")
        if not source_file:
            LOGGER.info("Station platform capacity source file is not pending. Skipping.")
            return 0
        records = read_records(source_file)
        with closing(psycopg2.connect(**connection_kwargs())) as connection:
            connection.autocommit = False
            with connection.cursor() as cursor:
                create_table(cursor)
                insert_records(cursor, records)
            connection.commit()
        archive_source_file(source_file)
        LOGGER.info("Station platform capacity ingestion completed. rows=%s", len(records))
        return 0
    except Exception as error:
        LOGGER.error("Station platform capacity ingestion failed securely. error_type=%s", safe_error_code(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
